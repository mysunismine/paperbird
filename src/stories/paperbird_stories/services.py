"""Сервисы для работы с сюжетами: рерайт и публикация."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import struct
import urllib.error
import urllib.request
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.constants import (
    IMAGE_DEFAULT_SIZE,
    IMAGE_SIZE_CHOICES,
    OPENAI_DEFAULT_TEMPERATURE,
    OPENAI_RESPONSE_FORMAT,
    REWRITE_MAX_ATTEMPTS,
)
from core.models import WorkerTask
from core.logging import event_logger, logging_context
from core.services.worker import enqueue_task
from projects.models import Post, Project
from projects.services.telethon_client import TelethonClientFactory

from .models import (
    Publication,
    RewritePreset,
    RewriteResult,
    RewriteTask,
    Story,
)

SYSTEM_PROMPT = (
    "Вы — профессиональный редактор новостей. "
    "Объединяйте предоставленные материалы в связный текст, сохраняйте факты, "
    "исключайте домыслы и следуйте деловому стилю. "
    "Отвечайте только структурированными данными и не добавляйте необязательные поля "
    "(summary, hashtags, sources и т. д.)."
)

RESPONSE_REQUIREMENTS = (
    "Верните валидный JSON только с полями: title (строка, до 120 символов) и text "
    "(основной текст заметки; допускается строка или массив абзацев или фрагментов). "
    "Не добавляйте другие поля, не используйте преамбулы и формируйте строго корректный JSON."
)

ALLOWED_IMAGE_SIZES = {choice[0] for choice in IMAGE_SIZE_CHOICES}


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
    if cleaned in ALLOWED_IMAGE_SIZES:
        return cleaned
    return IMAGE_DEFAULT_SIZE


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
    """Формирует сообщения для LLM по спецификации промптов."""

    documents = []
    for index, post in enumerate(posts, start=1):
        body = (post.message or "").strip()
        if not body:
            body = "(пустой текст поста)"
        documents.append(f"#{index}\n```\n{body}\n```")

    comment = editor_comment.strip() or "Без дополнительных указаний."
    preset_block = preset_instruction.strip()
    if preset_block:
        comment = (
            f"{comment}\n\n"
            f"Настройки пресета:\n{preset_block}"
        )
    title_context = title.strip() or "Подберите информативный заголовок."
    user_prompt = (
        "Собери из постов новую заметку и выполни рерайт."\
        "\n\nТекущий заголовок: "
        f"{title_context}\n\nДокументы:\n" + "\n\n".join(documents) + "\n\n"
        f"Комментарий редактора: {comment}\n\n"
        f"{RESPONSE_REQUIREMENTS}\n"
        "Не добавляй преамбулу, ответ должен быть только JSON."
    )

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
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
    """Минимальная обёртка для OpenAI Chat Completions API."""

    api_url = "https://api.openai.com/v1/chat/completions"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: int | float | None = None,
    ) -> None:
        self.api_key = api_key or getattr(settings, "OPENAI_API_KEY", "")
        self.model = model or getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")
        base_url = getattr(settings, "OPENAI_BASE_URL", "").strip()
        self.api_url = base_url or self.api_url
        self.timeout = timeout or getattr(settings, "OPENAI_TIMEOUT", 30)
        if not self.api_key:
            raise RewriteFailed("OPENAI_API_KEY не задан")

    def run(self, *, messages: Sequence[dict[str, str]]) -> ProviderResponse:
        import urllib.error
        import urllib.request

        payload = json.dumps(
            {
                "model": self.model,
                "messages": list(messages),
                "temperature": OPENAI_DEFAULT_TEMPERATURE,
                "response_format": OPENAI_RESPONSE_FORMAT.copy(),
            }
        ).encode("utf-8")
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
            content = choice["message"]["content"]
            response_id = data.get("id")
            parsed = json.loads(content)
        except (KeyError, json.JSONDecodeError, IndexError) as exc:
            raise RewriteFailed("Некорректный ответ OpenAI") from exc

        return ProviderResponse(result=parsed, raw=data, response_id=response_id)


def default_rewriter(*, project: Project | None = None) -> StoryRewriter:
    """Фабрика стандартного рерайтера с OpenAI провайдером для проекта."""

    provider_kwargs = {}
    if project is not None:
        rewrite_model = getattr(project, "rewrite_model", "") or ""
        if rewrite_model:
            provider_kwargs["model"] = rewrite_model
    provider = OpenAIChatProvider(**provider_kwargs)
    return StoryRewriter(provider=provider)


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack("!I", len(data))
        + tag
        + data
        + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _placeholder_image_bytes(prompt: str) -> bytes:
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
    ) -> None:
        self.api_url = api_url or os.getenv(
            "OPENAI_IMAGE_URL", "https://api.openai.com/v1/images/generations"
        )
        self.model = model or os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1")
        self.size = normalize_image_size(size or os.getenv("OPENAI_IMAGE_SIZE", IMAGE_DEFAULT_SIZE))
        self.quality = quality or os.getenv("OPENAI_IMAGE_QUALITY", "standard")
        if response_format is None:
            response_format = os.getenv("OPENAI_IMAGE_RESPONSE_FORMAT", "b64_json")
        self.response_format = (response_format or "").strip()

    def generate(
        self,
        *,
        prompt: str,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        _allow_without_format: bool = False,
    ) -> GeneratedImage:
        prompt = prompt.strip()
        if not prompt:
            raise ImageGenerationFailed("Описание не может быть пустым")

        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            data = _placeholder_image_bytes(prompt or "placeholder")
            return GeneratedImage(data=data, mime_type="image/png")

        use_model = model or self.model
        use_size = normalize_image_size(size or self.size)
        use_quality = quality or self.quality
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
            with urllib.request.urlopen(request, timeout=30) as response:
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
        return self.provider.generate(
            prompt=prompt,
            model=model,
            size=size,
            quality=quality,
        )


def default_image_generator() -> StoryImageGenerator:
    """Возвращает генератор изображений по умолчанию."""

    provider = OpenAIImageProvider()
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
        return asyncio.run(self._send_async(story=story, text=text, target=target))


def default_publisher_for_story(story: Story) -> StoryPublisher:
    """Возвращает штатный паблишер для сюжета."""

    backend = TelethonPublisherBackend(user=story.project.owner)
    return StoryPublisher(backend=backend)
