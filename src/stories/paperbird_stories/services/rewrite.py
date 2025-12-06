"""Сервисы для рерайта сюжетов."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from django.conf import settings
from django.db import transaction

from core.constants import OPENAI_DEFAULT_TEMPERATURE, OPENAI_RESPONSE_FORMAT, REWRITE_MAX_ATTEMPTS
from projects.models import Project
from stories.paperbird_stories.models import RewritePreset, RewriteResult, RewriteTask, Story

from .exceptions import RewriteFailed
from .helpers import (
    _looks_like_yandex_text_model,
    _openai_temperature_for_model,
    _strip_code_fence,
    build_yandex_model_uri,
)
from .prompts import make_prompt_messages


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
    """Фабрика стандартного рерайтера с OpenAI или Yandex провайдером для проекта."""

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
