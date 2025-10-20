"""Сервисы для работы с сюжетами: рерайт и публикация."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from core.constants import (
    OPENAI_DEFAULT_TEMPERATURE,
    OPENAI_RESPONSE_FORMAT,
    REWRITE_MAX_ATTEMPTS,
)
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
    "исключайте домыслы и следуйте деловому стилю. Отвечайте только структурированными данными."
)

RESPONSE_REQUIREMENTS = (
    "Верните валидный JSON с полями: title (строка, до 120 символов), summary (до 300 символов), "
    "content (структурированный текст, допускается Markdown), hashtags (массив хэштегов без #), "
    "sources (массив ссылок или кратких указаний на источники)."
)


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
    ) -> RewriteTask:
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
                    "structured": provider_response.result,
                    "raw": provider_response.raw,
                    "hashtags": result.hashtags,
                    "sources": result.sources,
                }
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


def default_rewriter() -> StoryRewriter:
    """Фабрика стандартного рерайтера с OpenAI провайдером."""

    provider = OpenAIChatProvider()
    return StoryRewriter(provider=provider)


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

        with transaction.atomic():
            publication = Publication.objects.create(
                story=story,
                target=target,
                result_text=text,
                scheduled_for=scheduled_for,
                status=(
                    Publication.Status.SCHEDULED
                    if scheduled_for
                    else Publication.Status.PUBLISHING
                ),
            )

        if scheduled_for:
            return publication

        try:
            publication.mark_publishing()
            result = self.backend.send(story=story, text=text, target=target)
        except Exception as exc:  # pragma: no cover - проверяется тестами
            publication.mark_failed(error=str(exc))
            raise PublicationFailed(str(exc)) from exc

        published_at = result.published_at or timezone.now()
        publication.mark_published(
            message_ids=result.message_ids,
            published_at=published_at,
            raw=result.raw,
        )
        story.mark_published()
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
            published_at = getattr(message, "date", None) or timezone.now()
            raw = message.to_dict() if hasattr(message, "to_dict") else None
            return PublishResult(message_ids=[message_id], published_at=published_at, raw=raw)

    def send(self, *, story: Story, text: str, target: str) -> PublishResult:
        return asyncio.run(self._send_async(story=story, text=text, target=target))


def default_publisher_for_story(story: Story) -> StoryPublisher:
    """Возвращает штатный паблишер для сюжета."""

    backend = TelethonPublisherBackend(user=story.project.owner)
    return StoryPublisher(backend=backend)
