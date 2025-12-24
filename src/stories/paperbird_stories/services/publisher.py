"""Публикация сюжетов."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from core.logging import event_logger, logging_context
from core.models import WorkerTask
from projects.services.telethon_client import TelethonClientFactory
from stories.paperbird_stories.models import Publication, Story

from .exceptions import PublicationFailed
from .helpers import _json_safe


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
        media_order: str = "after",
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
                    media_order=media_order or "after",
                    status=Publication.Status.SCHEDULED,
                )

            if scheduled_for:
                from stories.paperbird_stories import services as story_services

                story_services.enqueue_task(
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
                    story=story,
                    text=publication.result_text,
                    target=publication.target,
                    media_order=publication.media_order or "after",
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

    async def _send_async(
        self,
        *,
        story: Story,
        text: str,
        target: str,
        media_order: str = "after",
    ) -> PublishResult:
        """Асинхронно отправляет сообщение."""
        factory = TelethonClientFactory(user=self.user)
        async with factory.connect() as client:
            messages_raw: list[dict] = []

            send_media_first = media_order == "before"

            def _append_message(message):
                mid = int(getattr(message, "id", 0))
                if mid <= 0:
                    raise PublicationFailed("Telegram не вернул идентификатор сообщения")
                published_at_value = getattr(message, "date", None) or timezone.now()
                if hasattr(message, "to_dict"):
                    messages_raw.append(message.to_dict())
                return mid, published_at_value

            message_ids: list[int] = []
            published_at = timezone.now()

            images = await sync_to_async(lambda: list(story.selected_images()))()
            legacy_image_path = None
            if not images and story.image_file:
                legacy_image_path = Path(story.image_file.path)

            def _image_path(entry):
                if not entry.image_file:
                    return None
                path = Path(entry.image_file.path)
                if not path.exists():
                    return None
                return path

            async def _send_images() -> None:
                nonlocal published_at
                if legacy_image_path and legacy_image_path.exists():
                    image_message = await client.send_file(
                        target,
                        legacy_image_path.as_posix(),
                        caption=None,
                    )
                    mid, published_at_value = _append_message(image_message)
                    message_ids.append(mid)
                    published_at = published_at_value
                    return
                for image in images:
                    image_path = _image_path(image)
                    if not image_path:
                        continue
                    image_message = await client.send_file(
                        target,
                        image_path.as_posix(),
                        caption=None,
                    )
                    mid, published_at_value = _append_message(image_message)
                    message_ids.append(mid)
                    published_at = published_at_value

            # Если медиа должно быть первым — отправляем файлы до текста.
            if send_media_first and (images or legacy_image_path):
                await _send_images()

            text_message = await client.send_message(target, text, parse_mode="html")
            mid, published_at = _append_message(text_message)
            message_ids.append(mid)

            # Если медиа идёт после текста — отправляем файлы вторыми.
            if not send_media_first and (images or legacy_image_path):
                await _send_images()

            raw = {"messages": messages_raw} if messages_raw else None
            return PublishResult(message_ids=message_ids, published_at=published_at, raw=raw)

    def send(
        self,
        *,
        story: Story,
        text: str,
        target: str,
        media_order: str = "after",
    ) -> PublishResult:
        """Отправляет сообщение синхронно."""
        return asyncio.run(
            self._send_async(
                story=story,
                text=text,
                target=target,
                media_order=media_order,
            )
        )


def default_publisher_for_story(story: Story) -> StoryPublisher:
    """Возвращает штатный паблишер для сюжета."""

    backend = TelethonPublisherBackend(user=story.project.owner)
    return StoryPublisher(backend=backend)
