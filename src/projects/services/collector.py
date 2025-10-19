"""Сборщик постов из Telegram."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone
from telethon.tl.custom.message import Message
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from accounts.models import User
from projects.models import Post, Project, Source, SourceSyncLog
from projects.services.telethon_client import TelethonClientFactory


@dataclass
class CollectOptions:
    limit: int = 100
    with_media: bool = True


class PostCollector:
    """Загружает посты для проектов пользователя."""

    def __init__(self, *, user: User, options: CollectOptions | None = None):
        self.user = user
        self.options = options or CollectOptions()

    async def collect_for_project(self, project: Project) -> None:
        factory = TelethonClientFactory(user=self.user)
        async with factory.connect() as client:
            for source in project.sources.filter(is_active=True):
                log = SourceSyncLog.objects.create(source=source)
                fetched = skipped = 0
                try:
                    target = source.username or source.telegram_id or source.invite_link
                    if not target:
                        log.finish(status="failed", error="Источник не содержит идентификатора")
                        continue
                    entity = await client.get_entity(target)
                    last_message_id = source.last_synced_id or 0
                    async for message in client.iter_messages(
                        entity,
                        limit=self.options.limit,
                        min_id=last_message_id,
                        reverse=True,
                    ):
                        if not isinstance(message, Message):
                            continue
                        processed = await self._process_message(message=message, source=source)
                        if processed:
                            fetched += 1
                            last_message_id = max(last_message_id, message.id)
                        else:
                            skipped += 1
                    source.last_synced_at = timezone.now()
                    if last_message_id:
                        source.last_synced_id = last_message_id
                    source.save(update_fields=["last_synced_at", "last_synced_id", "updated_at"])
                    log.finish(status="ok", fetched=fetched, skipped=skipped)
                except Exception as exc:  # pragma: no cover - зависит от API
                    log.finish(status="failed", error=str(exc), fetched=fetched, skipped=skipped)

    async def _process_message(self, *, message: Message, source: Source) -> bool:
        message_text = message.message or ""
        if message_text and not source.matches_keywords(message_text):
            return False

        media_bytes = None
        media_type = ""
        media_path = ""

        if message.media and self.options.with_media:
            if isinstance(message.media, MessageMediaPhoto | MessageMediaDocument):
                media_type = type(message.media).__name__
                # Фактическое сохранение медиа будет реализовано позже.
                media_bytes = getattr(message.media, "bytes", None)

        text_hash = Post.make_hash(message_text)
        media_hash = Post.make_hash(media_bytes) if media_bytes else ""
        if source.should_skip(text_hash=text_hash, media_hash=media_hash):
            return False

        raw = message.to_dict() if hasattr(message, "to_dict") else {}
        with transaction.atomic():
            Post.create_or_update(
                project=source.project,
                source=source,
                telegram_id=message.id,
                message=message_text,
                posted_at=message.date,
                raw_data=raw,
                media_type=media_type or None,
                media_path=media_path or None,
                media_bytes=media_bytes,
            )
        return True


async def collect_for_user(user: User, *, project_id: int | None = None, limit: int = 100) -> None:
    """Асинхронный запуск сборщика для пользователя."""

    if not user.has_telethon_credentials:
        raise RuntimeError("У пользователя отсутствуют ключи Telethon")

    options = CollectOptions(limit=limit)
    collector = PostCollector(user=user, options=options)
    projects = user.projects.filter(is_active=True)
    if project_id:
        projects = projects.filter(id=project_id)
    for project in projects:
        await collector.collect_for_project(project)


def collect_for_user_sync(user: User, *, project_id: int | None = None, limit: int = 100) -> None:
    """Синхронный адаптер для использования в manage-команде."""

    asyncio.run(collect_for_user(user, project_id=project_id, limit=limit))
