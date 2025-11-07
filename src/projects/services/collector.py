"""Сборщик постов из Telegram."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time

from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone
from telethon.tl.custom.message import Message
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from accounts.models import User
from core.constants import DEFAULT_COLLECT_LIMIT
from projects.models import Post, Project, Source, SourceSyncLog
from projects.services.telethon_client import TelethonClientFactory


@dataclass
class CollectOptions:
    limit: int = DEFAULT_COLLECT_LIMIT
    with_media: bool = True


class PostCollector:
    """Загружает посты для проектов пользователя."""

    def __init__(self, *, user: User, options: CollectOptions | None = None):
        self.user = user
        self.options = options or CollectOptions()

    async def collect_for_project(self, project: Project) -> None:
        factory = TelethonClientFactory(user=self.user)
        sources = await sync_to_async(list)(
            project.sources.filter(is_active=True, type=Source.Type.TELEGRAM).order_by("id")
        )
        async with factory.connect() as client:
            for source in sources:
                log = await sync_to_async(SourceSyncLog.objects.create)(source=source)
                fetched = skipped = 0
                try:
                    target = source.username or source.telegram_id or source.invite_link
                    if not target:
                        await sync_to_async(log.finish)(
                            status="failed",
                            error="Источник не содержит идентификатора",
                            fetched=fetched,
                            skipped=skipped,
                        )
                        continue
                    entity = await client.get_entity(target)
                    last_message_id = source.last_synced_id or 0
                    cutoff = source.project.retention_cutoff()
                    async for message in client.iter_messages(
                        entity,
                        limit=None,
                        min_id=last_message_id,
                    ):
                        if not isinstance(message, Message):
                            continue
                        message_date = getattr(message, "date", None)
                        if cutoff and message_date is not None:
                            aware_date = message_date
                            if timezone.is_naive(aware_date):
                                aware_date = timezone.make_aware(
                                    aware_date,
                                    timezone.utc,
                                )
                            if aware_date < cutoff:
                                break
                        processed = await self._process_message(message=message, source=source)
                        last_message_id = max(last_message_id, message.id)
                        if processed:
                            fetched += 1
                        else:
                            skipped += 1
                    source.last_synced_at = timezone.now()
                    if last_message_id:
                        source.last_synced_id = last_message_id
                    await sync_to_async(source.save)(
                        update_fields=["last_synced_at", "last_synced_id", "updated_at"],
                    )
                except Exception as exc:  # pragma: no cover - зависит от API
                    await sync_to_async(log.finish)(
                        status="failed",
                        error=str(exc),
                        fetched=fetched,
                        skipped=skipped,
                    )
                else:
                    await sync_to_async(log.finish)(
                        status="ok",
                        fetched=fetched,
                        skipped=skipped,
                    )

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
        should_skip = await sync_to_async(source.should_skip)(
            text_hash=text_hash,
            media_hash=media_hash or None,
        )
        if should_skip:
            return False

        raw = message.to_dict() if hasattr(message, "to_dict") else {}
        raw = _normalize_raw(raw)
        await sync_to_async(self._store_post)(
            source=source,
            message=message,
            message_text=message_text,
            raw=raw,
            media_type=media_type,
            media_path=media_path,
            media_bytes=media_bytes,
        )
        return True

    @staticmethod
    def _store_post(
        *,
        source: Source,
        message: Message,
        message_text: str,
        raw: dict,
        media_type: str,
        media_path: str,
        media_bytes: bytes | None,
    ) -> None:
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


async def collect_for_user(
    user: User,
    *,
    project_id: int | None = None,
    limit: int = DEFAULT_COLLECT_LIMIT,
) -> None:
    """Асинхронный запуск сборщика для пользователя."""

    if not user.has_telethon_credentials:
        raise RuntimeError("У пользователя отсутствуют ключи Telethon")

    options = CollectOptions(limit=limit)
    collector = PostCollector(user=user, options=options)
    projects_qs = user.projects.filter(is_active=True)
    if project_id:
        projects_qs = projects_qs.filter(id=project_id)
    projects = await sync_to_async(list)(projects_qs.order_by("name"))
    for project in projects:
        await collector.collect_for_project(project)


async def collect_for_user_live(
    user: User,
    *,
    project_id: int | None = None,
    limit: int = DEFAULT_COLLECT_LIMIT,
    interval: int = 60,
) -> None:
    """Постоянный сбор постов с указанным интервалом опроса."""

    delay = max(interval, 5)
    while True:
        await collect_for_user(user, project_id=project_id, limit=limit)
        await asyncio.sleep(delay)


def collect_for_user_sync(
    user: User,
    *,
    project_id: int | None = None,
    limit: int = DEFAULT_COLLECT_LIMIT,
    continuous: bool = False,
    interval: int = 60,
) -> None:
    """Синхронный адаптер для использования в manage-команде."""

    async def runner() -> None:
        if continuous:
            await collect_for_user_live(
                user,
                project_id=project_id,
                limit=limit,
                interval=interval,
            )
        else:
            await collect_for_user(user, project_id=project_id, limit=limit)

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        # graceful stop for continuous mode
        pass


def _normalize_raw(value):
    """Recursively convert unsupported JSON types (e.g., datetime) to strings."""

    if isinstance(value, dict):
        return {key: _normalize_raw(sub) for key, sub in value.items()}
    if isinstance(value, list):
        return [_normalize_raw(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_raw(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    return value
