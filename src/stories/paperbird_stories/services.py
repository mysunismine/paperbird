"""Сервисы для работы с сюжетами: рерайт и публикация."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import socket
import struct
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.constants import (
    IMAGE_DEFAULT_QUALITY,
    IMAGE_DEFAULT_SIZE,
    IMAGE_QUALITY_CHOICES,
    IMAGE_SIZE_CHOICES,
    OPENAI_DEFAULT_TEMPERATURE,
    OPENAI_RESPONSE_FORMAT,
    REWRITE_MAX_ATTEMPTS,
)
from core.models import WorkerTask
from core.logging import event_logger, logging_context
from core.services.worker import enqueue_task
from projects.models import Post, Project
from projects.services.prompt_config import render_prompt
from projects.services.telethon_client import TelethonClientFactory

from .models import (
    Publication,
    RewritePreset,
    RewriteResult,
    RewriteTask,
    Story,
)

ALLOWED_IMAGE_SIZES = {choice[0] for choice in IMAGE_SIZE_CHOICES}
ALLOWED_IMAGE_QUALITIES = {choice[0] for choice in IMAGE_QUALITY_CHOICES}
OPENAI_MODELS_WITH_FIXED_TEMPERATURE = {"gpt-5", "gpt-5.0", "gpt-5o", "gpt-5o-mini"}


def _json_safe(value):
    """Преобразует произвольные объекты в JSON-совместимый вид."""

    if isinstance(value, dict):
        return {str(key): _json_safe(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(value).decode("ascii")
    return value


def normalize_image_size(value: str | None) -> str:
    """Возвращает безопасный размер изображения."""

    if not value:
        return IMAGE_DEFAULT_SIZE
    cleaned = str(value).strip().lower()
    legacy_map = {
        "512x512": "1024x1024",
        "256x256": "1024x1024",
    }
    cleaned = legacy_map.get(cleaned, cleaned)
    if cleaned in ALLOWED_IMAGE_SIZES:
        return cleaned
    return IMAGE_DEFAULT_SIZE


def normalize_image_quality(value: str | None) -> str:
    """Возвращает допустимое качество изображения."""

    if not value:
        return IMAGE_DEFAULT_QUALITY
    cleaned = str(value).strip().lower()
    legacy_map = {
        "standard": "medium",
        "hd": "high",
    }
    cleaned = legacy_map.get(cleaned, cleaned)
    if cleaned in ALLOWED_IMAGE_QUALITIES:
        return cleaned
    return IMAGE_DEFAULT_QUALITY


def build_yandex_model_uri(model: str, *, folder_id: str, scheme: str = "gpt") -> str:
    """Формирует URI модели YandexGPT/ART."""

    cleaned = (model or "").strip()
    if cleaned.startswith(("gpt://", "art://")):
        return cleaned
    base, _, version = cleaned.partition("/")
    version = version or "latest"
    return f"{scheme}://{folder_id}/{base}/{version}"


def _looks_like_yandex_text_model(model: str) -> bool:
    """Проверяет, похожа ли модель на текстовую модель Yandex."""
    lowered = (model or "").lower()
    prefixes = ("yandex", "qwen", "gpt-oss", "gemma", "gpt://")
    return any(lowered.startswith(prefix) for prefix in prefixes)


def _looks_like_yandex_art_model(model: str) -> bool:
    """Проверяет, похожа ли модель на модель YandexART."""
    lowered = (model or "").lower()
    return lowered.startswith("yandex") or lowered.startswith("art://")


def _strip_code_fence(text: str) -> str:
    """Удаляет ограничители кода."""
    if not text:
        return text
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        body = stripped.strip("`")
        # remove optional language prefix after opening fence
        first_newline = body.find("\n")
        if first_newline != -1:
            language = body[:first_newline].strip().lower()
            if language in {"json", "yaml", "markdown", "text"}:
                return body[first_newline + 1 :].strip()
        return body.strip()
    return stripped


def _openai_temperature_for_model(model: str) -> float:
    """Возвращает температуру OpenAI для модели."""
    lowered = (model or "").lower()
    if any(lowered.startswith(prefix) for prefix in ("gpt-5", "gpt-5o")):
        return 1.0
    if model in OPENAI_MODELS_WITH_FIXED_TEMPERATURE:
        return 1.0
    return OPENAI_DEFAULT_TEMPERATURE


@dataclass(slots=True)
class ProviderResponse:
    """Ответ внешнего провайдера рерайта."""

    result: dict
    raw: dict
    response_id: str | None = None


class RewriteProvider(Protocol):
    """Интерфейс провайдера для выполнения рерайта."""

    def run(
        self,
        *,
        messages: Sequence[dict[str, str]],
    ) -> ProviderResponse:  # pragma: no cover - protocol
        ...


class StoryCreationError(RuntimeError):
    """Ошибка при создании сюжета."""


class RewriteFailed(RuntimeError):
    """Ошибка выполнения рерайта."""


@dataclass(slots=True)
class GeneratedImage:
    """Результат генерации изображения."""

    data: bytes
    mime_type: str = "image/png"


class ImageGenerationFailed(RuntimeError):
    """Ошибка генерации изображения."""


class ImageGenerationProvider(Protocol):
    """Интерфейс генератора изображений."""

    def generate(self, *, prompt: str) -> GeneratedImage:  # pragma: no cover - protocol
        ...


def build_prompt(
    *,
    posts: Sequence[Post],
    editor_comment: str,
    title: str,
    preset_instruction: str = "",
) -> list[dict[str, str]]:
    """Формирует сообщения для LLM, используя шаблон проекта."""

    if not posts:
        raise ValueError("Невозможно сформировать промт без постов")
    project = posts[0].project
    rendered = render_prompt(
        project=project,
        posts=posts,
        title=title,
        editor_comment=editor_comment,
        preset_instruction=preset_instruction,
    )
    return [
        {"role": "system", "content": rendered.system_message},
        {"role": "user", "content": rendered.user_message},
    ]


def make_prompt_messages(
    story: Story,
    *,
    editor_comment: str | None = None,
    preset: RewritePreset | None = None,
) -> tuple[list[dict[str, str]], str]:
    """Собирает промпт для сюжета и возвращает сообщения и комментарий пользователя."""

    posts = list(story.ordered_posts())
    if not posts:
        raise RewriteFailed("Сюжет не содержит постов для рерайта")

    user_comment = editor_comment if editor_comment is not None else story.editor_comment
    user_comment = user_comment.strip() if user_comment else ""
    preset_comment = preset.editor_comment.strip() if preset and preset.editor_comment else ""
    if preset_comment and user_comment:
        combined_comment = (
            f"{preset_comment}\n\n"
            "Дополнительные указания редактора:\n"
            f"{user_comment}"
        )
    else:
        combined_comment = preset_comment or user_comment

    messages = build_prompt(
        posts=posts,
        editor_comment=combined_comment,
        title=story.title,
        preset_instruction=preset.instruction_block() if preset else "",
    )
    return messages, user_comment


@dataclass(slots=True)
class StoryFactory:
    """Создаёт сюжет на основе выбранных постов."""

    project: Project

    def create(
        self,
        *,
        post_ids: Sequence[int],
        title: str = "",
        editor_comment: str = "",
    ) -> Story:
        """Создает сюжет и прикрепляет к нему посты."""
        if not post_ids:
            raise StoryCreationError("Список постов пуст")
        order_map = {post_id: index for index, post_id in enumerate(post_ids)}
        posts = list(
            Post.objects.filter(project=self.project, id__in=post_ids).order_by("id")
        )
        if len(order_map) != len(post_ids):
            raise StoryCreationError("Список постов содержит повторяющиеся значения")
        if len(posts) != len(post_ids):
            missing = set(post_ids) - {post.id for post in posts}
            raise StoryCreationError(
                f"Посты не найдены или не принадлежат проекту: {sorted(missing)}"
            )
        posts.sort(key=lambda post: order_map[post.id])

        story = Story.objects.create(
            project=self.project,
            title=title.strip(),
            editor_comment=editor_comment.strip(),
        )
        story.attach_posts(posts)
        return story


@dataclass(slots=True)
class StoryRewriter:
    """Отправляет сюжет на рерайт и применяет результат."""

    provider: RewriteProvider
    max_attempts: int = REWRITE_MAX_ATTEMPTS

    def rewrite(
        self,
        story: Story,
        *,
        editor_comment: str | None = None,
        preset: RewritePreset | None = None,
        messages_override: Sequence[dict[str, str]] | None = None,
    ) -> RewriteTask:
        """Выполняет рерайт сюжета."""
        messages, user_comment = make_prompt_messages(
            story,
            editor_comment=editor_comment,
            preset=preset,
        )
        if messages_override is not None:
            messages = [
                {"role": message.get("role", ""), "content": message.get("content", "")}
                for message in messages_override
            ]

        with transaction.atomic():
            story.status = Story.Status.REWRITING
            story.editor_comment = user_comment
            story.prompt_snapshot = messages
            story.save(update_fields=["status", "editor_comment", "prompt_snapshot", "updated_at"])
            task = RewriteTask.objects.create(
                story=story,
                prompt_messages=messages,
                editor_comment=story.editor_comment,
                preset=preset,
            )

        last_error = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                task.mark_running()
                provider_response = self.provider.run(messages=messages)
                result = RewriteResult.from_dict(provider_response.result)
                payload = {
                    "structured": {
                        "title": result.title,
                        "text": result.content,
                    },
                    "raw": provider_response.raw,
                }
                if provider_response.result != payload["structured"]:
                    payload["provider_result"] = provider_response.result
                task.mark_success(
                    result=provider_response.result,
                    response_id=provider_response.response_id,
                )
                story.apply_rewrite(
                    title=result.title or story.title,
                    summary=result.summary,
                    body=result.content,
                    hashtags=result.hashtags,
                    sources=result.sources,
                    payload=payload,
                    preset=preset,
                )
                story.prompt_snapshot = messages
                story.save(update_fields=["prompt_snapshot", "updated_at"])
                return task
            except Exception as exc:  # pragma: no cover - защитный слой, проверяется в тестах
                last_error = str(exc)
                task.mark_failed(error=last_error)
                if attempt >= self.max_attempts:
                    story.status = Story.Status.DRAFT
                    story.save(update_fields=["status", "updated_at"])
                    raise RewriteFailed(last_error) from exc
        raise RewriteFailed(last_error)


class OpenAIChatProvider:
    """Провайдер для моделей OpenAI Chat Completions."""

    def __init__(
        self,
        *,
        api_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | float | None = None,
    ) -> None:
        self.api_url = api_url or os.getenv(
            "OPENAI_URL",
            "https://api.openai.com/v1/chat/completions",
        )
        self.api_key = (api_key or os.getenv("OPENAI_API_KEY", "")).strip()
        self.model = (model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")).strip()
        self.timeout = timeout or getattr(settings, "OPENAI_TIMEOUT", 30)
        if not self.api_key:
            raise RewriteFailed("OPENAI_API_KEY не задан")

    def run(self, *, messages: Sequence[dict[str, str]]) -> ProviderResponse:
        """Выполняет запрос к OpenAI Chat Completions API."""
        import urllib.error
        import urllib.request

        temperature = _openai_temperature_for_model(self.model)
        payload_dict = {
            "model": self.model,
            "messages": list(messages),
            "temperature": temperature,
            "response_format": OPENAI_RESPONSE_FORMAT.copy(),
        }
        payload = json.dumps(payload_dict).encode("utf-8")
        request = urllib.request.Request(
            self.api_url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover - требует живого API
            message = exc.read().decode("utf-8", "replace")
            raise RewriteFailed(f"OpenAI HTTP {exc.code}: {message}") from exc
        except OSError as exc:  # pragma: no cover
            raise RewriteFailed(str(exc)) from exc

        try:
            choice = data["choices"][0]
            message = choice["message"]
            response_id = data.get("id")
            parsed = self._parse_message(message)
        except (KeyError, json.JSONDecodeError, IndexError, TypeError, ValueError) as exc:
            raise RewriteFailed("Некорректный ответ OpenAI") from exc

        return ProviderResponse(result=parsed, raw=data, response_id=response_id)

    @staticmethod
    def _parse_message(message: dict[str, Any]) -> dict:
        """Парсит сообщение от OpenAI."""
        parsed = message.get("parsed")
        if isinstance(parsed, dict):
            return parsed

        content = message.get("content")
        text = OpenAIChatProvider._extract_text(content)
        if not text:
            raise ValueError("Ответ OpenAI не содержит текста")
        return json.loads(text)

    @staticmethod
    def _extract_text(content: Any) -> str:
        """Извлекает текст из сообщения."""
        if isinstance(content, str):
            return content.strip()

        parts: list[str] = []
        if isinstance(content, dict):
            candidate = content.get("text") or content.get("content")
            if isinstance(candidate, str):
                parts.append(candidate)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    candidate = item.get("text") or item.get("content")
                    if isinstance(candidate, str):
                        parts.append(candidate)
        return "\n".join(part.strip() for part in parts if part.strip())


class YandexGPTProvider:
    """Провайдер для моделей YandexGPT через REST API."""

    api_url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        folder_id: str | None = None,
        model: str | None = None,
        timeout: int | float | None = None,
    ) -> None:
        self.api_key = api_key or getattr(settings, "YANDEX_API_KEY", "")
        self.folder_id = folder_id or getattr(settings, "YANDEX_FOLDER_ID", "")
        self.timeout = timeout or getattr(
            settings,
            "YANDEX_TIMEOUT",
            getattr(settings, "OPENAI_TIMEOUT", 30),
        )
        self.model = (model or "yandexgpt-lite").strip()
        if not self.api_key:
            raise RewriteFailed("YANDEX_API_KEY не задан")
        if self.model.startswith("gpt://"):
            self.model_uri = self.model
        else:
            if not self.folder_id:
                raise RewriteFailed("YANDEX_FOLDER_ID не задан")
            self.model_uri = build_yandex_model_uri(
                self.model,
                folder_id=self.folder_id,
                scheme="gpt",
            )

    def run(self, *, messages: Sequence[dict[str, str]]) -> ProviderResponse:
        """Выполняет запрос к YandexGPT API."""
        import urllib.error
        import urllib.request

        yc_messages = []
        for message in messages:
            role = message.get("role") or "user"
            text = message.get("content") or message.get("text") or ""
            yc_messages.append({"role": role, "text": text})
        payload = {
            "modelUri": self.model_uri,
            "completionOptions": {
                "stream": False,
                "temperature": OPENAI_DEFAULT_TEMPERATURE,
                "maxTokens": "2000",
            },
            "messages": yc_messages,
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.api_url,
            data=body,
            headers={
                "Authorization": f"Api-Key {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover - требует живого API
            message = exc.read().decode("utf-8", "replace")
            raise RewriteFailed(f"YandexGPT HTTP {exc.code}: {message}") from exc
        except OSError as exc:  # pragma: no cover
            raise RewriteFailed(str(exc)) from exc

        try:
            result = data["result"]
            alternatives = result.get("alternatives") or []
            message = alternatives[0]["message"]
            text = message.get("text", "")
            response_id = result.get("modelVersion")
        except (KeyError, IndexError, TypeError) as exc:
            raise RewriteFailed("Некорректный ответ YandexGPT") from exc

        clean_text = _strip_code_fence(text)
        try:
            parsed = json.loads(clean_text)
        except json.JSONDecodeError:
            parsed = {"content": clean_text}
        return ProviderResponse(result=parsed, raw=data, response_id=response_id)


def default_rewriter(*, project: Project | None = None) -> StoryRewriter:
    """Фабрика стандартного рерайтера с OpenAI провайдером для проекта."""

    provider_kwargs = {}
    if project is not None:
        rewrite_model = getattr(project, "rewrite_model", "") or ""
        if rewrite_model:
            provider_kwargs["model"] = rewrite_model
    model_name = provider_kwargs.get("model") or ""
    if model_name and _looks_like_yandex_text_model(model_name):
        provider = YandexGPTProvider(**provider_kwargs)
    else:
        provider = OpenAIChatProvider(**provider_kwargs)
    return StoryRewriter(provider=provider)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    """Создает PNG-чанк."""
    return (
        struct.pack("!I", len(data))
        + tag
        + data
        + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _placeholder_image_bytes(prompt: str) -> bytes:
    """Генерирует байты изображения-заглушки."""
    width = height = 320
    digest = hashlib.sha256(prompt.encode("utf-8", "ignore")).digest()
    color = digest[0], digest[8], digest[16]
    pixel = bytes([color[0], color[1], color[2], 255])
    rows = []
    for _ in range(height):
        rows.append(b"\x00" + pixel * width)
    raw = b"".join(rows)
    header = struct.pack("!2I5B", width, height, 8, 6, 0, 0, 0)
    compressed = zlib.compress(raw, 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", compressed)
        + _png_chunk(b"IEND", b"")
    )


class OpenAIImageProvider:
    """Генерирует изображения через OpenAI Images API."""

    def __init__(
        self,
        *,
        api_url: str | None = None,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        response_format: str | None = None,
        timeout: int | float | None = None,
    ) -> None:
        self.api_url = api_url or os.getenv(
            "OPENAI_IMAGE_URL", "https://api.openai.com/v1/images/generations"
        )
        self.model = model or os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
        self.size = normalize_image_size(
            size or os.getenv("OPENAI_IMAGE_SIZE", IMAGE_DEFAULT_SIZE)
        )
        self.quality = normalize_image_quality(
            quality or os.getenv("OPENAI_IMAGE_QUALITY", IMAGE_DEFAULT_QUALITY)
        )
        if response_format is None:
            response_format = os.getenv("OPENAI_IMAGE_RESPONSE_FORMAT", "b64_json")
        self.response_format = (response_format or "").strip()
        self.request_timeout = timeout or getattr(settings, "OPENAI_IMAGE_TIMEOUT", 60)

    def generate(
        self,
        *,
        prompt: str,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        _allow_without_format: bool = False,
    ) -> GeneratedImage:
        """Генерирует изображение."""
        prompt = prompt.strip()
        if not prompt:
            raise ImageGenerationFailed("Описание не может быть пустым")

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            data = _placeholder_image_bytes(prompt or "placeholder")
            return GeneratedImage(data=data, mime_type="image/png")

        use_model = model or self.model
        use_size = normalize_image_size(size or self.size)
        use_quality = normalize_image_quality(quality or self.quality)
        payload = {
            "model": use_model,
            "prompt": prompt,
            "size": use_size,
            "quality": use_quality,
        }
        if self.response_format and not _allow_without_format:
            payload["response_format"] = self.response_format
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(self.api_url, data=body, method="POST")
        request.add_header("Content-Type", "application/json")
        request.add_header("Authorization", f"Bearer {api_key}")

        try:
            with urllib.request.urlopen(request, timeout=self.request_timeout) as response:
                raw_body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:  # pragma: no cover - требует живого API
            message = exc.read().decode("utf-8", "replace")
            if (
                not _allow_without_format
                and self.response_format
                and exc.code == 400
                and "response_format" in message.lower()
            ):
                return self.generate(
                    prompt=prompt,
                    model=model,
                    size=size,
                    quality=quality,
                    _allow_without_format=True,
                )
            raise ImageGenerationFailed(f"OpenAI HTTP {exc.code}: {message}") from exc
        except (socket.timeout, TimeoutError) as exc:  # pragma: no cover - сетевой таймаут
            raise ImageGenerationFailed(
                "OpenAI не ответил вовремя. Повторите попытку через пару секунд — "
                "генерация иногда занимает дольше."
            ) from exc
        except urllib.error.URLError as exc:  # pragma: no cover
            if isinstance(getattr(exc, "reason", None), socket.timeout):
                raise ImageGenerationFailed(
                    "OpenAI не ответил вовремя. Повторите попытку через пару секунд — "
                    "генерация иногда занимает дольше."
                ) from exc
            raise ImageGenerationFailed(str(exc)) from exc
        except OSError as exc:  # pragma: no cover
            raise ImageGenerationFailed(str(exc)) from exc

        try:
            parsed = json.loads(raw_body)
            item = parsed["data"][0]
            encoded = item.get("b64_json") or item.get("base64_data") or ""
            mime_type = item.get("mime_type", "image/png") or "image/png"
            if not encoded and item.get("url"):
                raise ImageGenerationFailed(
                    "Провайдер вернул ссылку на изображение. "
                    "Установите OPENAI_IMAGE_RESPONSE_FORMAT=b64_json для встроенного ответа."
                )
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ImageGenerationFailed("Некорректный ответ OpenAI") from exc

        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageGenerationFailed("Некорректные данные изображения") from exc

        if not image_bytes:
            raise ImageGenerationFailed("Пустой ответ от провайдера")

        return GeneratedImage(data=image_bytes, mime_type=mime_type)


class YandexArtProvider:
    """Генерация изображений через YandexART."""

    api_url = "https://llm.api.cloud.yandex.net/foundationModels/v1/imageGenerationAsync"
    operations_url = "https://llm.api.cloud.yandex.net/operations/"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        folder_id: str | None = None,
        model: str | None = None,
        timeout: int | float | None = None,
        poll_interval: float = 1.0,
    ) -> None:
        self.api_key = api_key or getattr(settings, "YANDEX_API_KEY", "")
        self.folder_id = folder_id or getattr(settings, "YANDEX_FOLDER_ID", "")
        self.model = (model or "yandex-art").strip()
        self.timeout = timeout or getattr(settings, "YANDEX_IMAGE_TIMEOUT", 90)
        self.poll_interval = poll_interval
        self.size = normalize_image_size(getattr(settings, "OPENAI_IMAGE_SIZE", IMAGE_DEFAULT_SIZE))
        if not self.api_key:
            raise ImageGenerationFailed("YANDEX_API_KEY не задан")
        if not self.model.startswith("art://") and not self.folder_id:
            raise ImageGenerationFailed("YANDEX_FOLDER_ID не задан")

    def _aspect_ratio(self, size: str) -> str:
        """Возвращает соотношение сторон для указанного размера."""
        """Возвращает соотношение сторон для указанного размера."""
        mapping = {
            "1024x1024": "1:1",
            "1024x1536": "2:3",
            "1536x1024": "3:2",
            "auto": "auto",
        }
        return mapping.get(size, "auto")

    def generate(
        self,
        *,
        prompt: str,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
    ) -> GeneratedImage:
        """Генерирует изображение."""
        import urllib.error
        import urllib.request

        prompt = prompt.strip()
        if not prompt:
            raise ImageGenerationFailed("Описание не может быть пустым")
        use_model = (model or self.model).strip()
        if use_model.startswith("art://"):
            model_uri = use_model
        else:
            model_uri = build_yandex_model_uri(use_model, folder_id=self.folder_id, scheme="art")
        aspect_ratio = self._aspect_ratio(normalize_image_size(size or self.size))
        payload = {
            "modelUri": model_uri,
            "generationOptions": {"aspectRatio": aspect_ratio},
            "messages": [{"role": "user", "text": prompt}],
        }
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Api-Key {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                operation = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover - требует живого API
            message = exc.read().decode("utf-8", "replace")
            raise ImageGenerationFailed(f"YandexART HTTP {exc.code}: {message}") from exc
        except OSError as exc:  # pragma: no cover
            raise ImageGenerationFailed(str(exc)) from exc

        operation_id = operation.get("id") or operation.get("operationId")
        if not operation_id:
            raise ImageGenerationFailed("YandexART не вернул идентификатор операции")

        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"{self.operations_url}{operation_id}",
                    timeout=self.timeout,
                ) as response:
                    op_data = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:  # pragma: no cover
                message = exc.read().decode("utf-8", "replace")
                raise ImageGenerationFailed(f"YandexART HTTP {exc.code}: {message}") from exc
            except OSError as exc:  # pragma: no cover
                raise ImageGenerationFailed(str(exc)) from exc

            if op_data.get("done"):
                if "error" in op_data:
                    message = op_data["error"].get("message", "Неизвестная ошибка")
                    raise ImageGenerationFailed(f"YandexART: {message}")
                results = (
                    op_data.get("response", {}).get("results")
                    or op_data.get("response", {}).get("images")
                    or []
                )
                if not results:
                    raise ImageGenerationFailed("YandexART не вернул изображение")
                image_info = results[0].get("image") or results[0]
                encoded = (
                    image_info.get("imageBase64")
                    or image_info.get("base64")
                    or image_info.get("data")
                )
                if not encoded:
                    raise ImageGenerationFailed("Некорректный ответ YandexART")
                mime_type = image_info.get("mimeType", "image/png") or "image/png"
                try:
                    image_bytes = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ImageGenerationFailed(
                        "Некорректные данные изображения от YandexART"
                    ) from exc
                if not image_bytes:
                    raise ImageGenerationFailed("Пустой ответ от YandexART")
                return GeneratedImage(data=image_bytes, mime_type=mime_type)
            time.sleep(self.poll_interval)

        raise ImageGenerationFailed("YandexART не успел завершить генерацию")

@dataclass(slots=True)
class StoryImageGenerator:
    """Обёртка вокруг провайдера генерации изображений."""

    provider: ImageGenerationProvider

    def generate(
        self,
        *,
        prompt: str,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
    ) -> GeneratedImage:
        """Генерирует изображение."""
        return self.provider.generate(
            prompt=prompt,
            model=model,
            size=size,
            quality=quality,
        )


def default_image_generator(*, model: str | None = None) -> StoryImageGenerator:
    """Возвращает генератор изображений по умолчанию."""

    selected_model = (model or getattr(settings, "OPENAI_IMAGE_MODEL", IMAGE_DEFAULT_MODEL)).strip()
    if _looks_like_yandex_art_model(selected_model):
        provider = YandexArtProvider(model=selected_model)
    else:
        provider = OpenAIImageProvider(model=selected_model)
    return StoryImageGenerator(provider=provider)


class PublicationFailed(RuntimeError):
    """Ошибка публикации сюжета."""


@dataclass(slots=True)
class PublishResult:
    """Результат отправки публикации."""

    message_ids: list[int]
    published_at: datetime | None
    raw: dict | None = None


class PublisherBackend(Protocol):
    """Интерфейс механизма доставки публикации."""

    def send(
        self,
        *,
        story: Story,
        text: str,
        target: str,
    ) -> PublishResult:  # pragma: no cover - protocol
        ...


@dataclass(slots=True)
class StoryPublisher:
    """Управляет публикацией сюжета в Telegram."""

    backend: PublisherBackend
    logger = event_logger("stories.publication")

    def publish(
        self,
        story: Story,
        *,
        target: str,
        scheduled_for=None,
    ) -> Publication:
        """Публикует сюжет."""
        if not target:
            raise PublicationFailed("Не указан канал публикации")
        if story.status not in {Story.Status.READY, Story.Status.PUBLISHED}:
            raise PublicationFailed("Сюжет ещё не готов к публикации")

        text = story.compose_publication_text()
        if not text:
            raise PublicationFailed("Сюжет не содержит текста для публикации")

        with logging_context(project_id=story.project_id, story_id=story.pk):
            with transaction.atomic():
                publication = Publication.objects.create(
                    story=story,
                    target=target,
                    result_text=text,
                    scheduled_for=scheduled_for,
                    status=Publication.Status.SCHEDULED,
                )

            if scheduled_for:
                enqueue_task(
                    WorkerTask.Queue.PUBLISH,
                    payload={"publication_id": publication.pk},
                    scheduled_for=scheduled_for,
                )
                self.logger.info(
                    "publication_scheduled",
                    publication_id=publication.pk,
                    target=target,
                    scheduled_for=scheduled_for.isoformat(),
                )
                return publication

            self.logger.info(
                "publication_requested",
                publication_id=publication.pk,
                target=target,
            )
            publication.mark_publishing()
            return self.deliver(publication)

    def deliver(self, publication: Publication) -> Publication:
        """Выполняет отправку публикации, если она ещё не выполнена."""
        """Выполняет отправку публикации, если она ещё не выполнена."""

        story = publication.story
        if publication.status == Publication.Status.PUBLISHED:
            return publication
        if publication.status == Publication.Status.FAILED:
            raise PublicationFailed("Публикация уже завершилась с ошибкой")
        if not publication.result_text:
            raise PublicationFailed("Сюжет не содержит текста для публикации")

        with logging_context(project_id=story.project_id, story_id=story.pk):
            try:
                if publication.status != Publication.Status.PUBLISHING:
                    publication.mark_publishing()
                result = self.backend.send(
                    story=story, text=publication.result_text, target=publication.target
                )
            except Exception as exc:  # pragma: no cover - проверяется тестами
                publication.mark_failed(error=str(exc))
                self.logger.error(
                    "publication_failed",
                    publication_id=publication.pk,
                    target=publication.target,
                    error=str(exc),
                    exception=exc.__class__.__name__,
                )
                raise PublicationFailed(str(exc)) from exc

            published_at = result.published_at or timezone.now()
            safe_raw = _json_safe(result.raw) if result.raw is not None else None
            publication.mark_published(
                message_ids=result.message_ids,
                published_at=published_at,
                raw=safe_raw,
            )
            story.mark_published()
            self.logger.info(
                "publication_succeeded",
                publication_id=publication.pk,
                target=publication.target,
                published_at=published_at.isoformat(),
                message_ids=result.message_ids,
            )
            return publication


class TelethonPublisherBackend:
    """Публикует сюжет в Telegram через Telethon."""

    def __init__(self, *, user) -> None:
        self.user = user

    async def _send_async(self, *, story: Story, text: str, target: str) -> PublishResult:
        """Асинхронно отправляет сообщение."""
        factory = TelethonClientFactory(user=self.user)
        async with factory.connect() as client:
            message = await client.send_message(target, text)
            message_id = int(getattr(message, "id", 0))
            if message_id <= 0:
                raise PublicationFailed("Telegram не вернул идентификатор сообщения")
            published_at = getattr(message, "date", None) or timezone.now()
            raw = message.to_dict() if hasattr(message, "to_dict") else None
            return PublishResult(message_ids=[message_id], published_at=published_at, raw=raw)

    def send(self, *, story: Story, text: str, target: str) -> PublishResult:
        """Отправляет сообщение синхронно."""
        return asyncio.run(self._send_async(story=story, text=text, target=target))


def default_publisher_for_story(story: Story) -> StoryPublisher:
    """Возвращает штатный паблишер для сюжета."""

    backend = TelethonPublisherBackend(user=story.project.owner)
    return StoryPublisher(backend=backend)
