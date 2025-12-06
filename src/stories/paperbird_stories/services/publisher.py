"""Публикация сюжетов."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

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
