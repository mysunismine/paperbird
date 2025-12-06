"""Сборщик постов из Telegram."""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from telethon.tl.custom.message import Message
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from accounts.models import User
from core.constants import DEFAULT_COLLECT_LIMIT
from projects.models import Post, Project, Source, SourceSyncLog
from projects.services.telethon_client import (
    TelethonClientFactory,
    TelethonCredentialsMissingError,
)

logger = logging.getLogger(__name__)


@dataclass
class CollectOptions:
    """Параметры сбора."""

    limit: int = DEFAULT_COLLECT_LIMIT
    with_media: bool = True


@dataclass
class StoredMedia:
    """Результат загрузки медиа из Telegram."""
    """Результат загрузки медиа из Telegram."""

    media_type: str
    path: str
    content: bytes


class PostCollector:
    """Загружает посты для проектов пользователя."""

    def __init__(self, *, user: User, options: CollectOptions | None = None):
        self.user = user
        self.options = options or CollectOptions()

    async def collect_for_project(self, project: Project) -> None:
        """Выполняет сбор постов для проекта."""
        factory = TelethonClientFactory(user=self.user)
        sources = await sync_to_async(list)(
            project.sources.filter(is_active=True, type=Source.Type.TELEGRAM).order_by("id")
        )
        project_cutoff = project.retention_cutoff()
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
                    async for message in client.iter_messages(
                        entity,
                        limit=None,
                        min_id=last_message_id,
                    ):
                        if not isinstance(message, Message):
                            continue
                        message_date = getattr(message, "date", None)
                        if project_cutoff and message_date is not None:
                            aware_date = message_date
                            if timezone.is_naive(aware_date):
                                aware_date = timezone.make_aware(
                                    aware_date,
                                    UTC,
                                )
                            if aware_date < project_cutoff:
                                break
                        processed = await self._process_message(
                            message=message,
                            source=source,
                        )
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
        if project_cutoff:
            cutoff_value = project_cutoff
            await sync_to_async(
                lambda: Post.objects.filter(
                    project=project,
                    posted_at__lt=cutoff_value,
                ).delete()
            )()

    async def _process_message(self, *, message: Message, source: Source) -> bool:
        """Обрабатывает одно сообщение из Telegram."""
        message_text = message.message or ""
        if message_text and not source.matches_keywords(message_text):
            return False

        media_bytes = None
        media_type = ""
        media_path = ""

        if message.media and self.options.with_media:
            stored_media = await self._download_message_media(
                message=message,
                source=source,
            )
            if stored_media:
                media_type = stored_media.media_type
                media_path = stored_media.path
                media_bytes = stored_media.content

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
        """Сохраняет пост в базу данных."""
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

    async def _download_message_media(
        self,
        *,
        message: Message,
        source: Source,
    ) -> StoredMedia | None:
        """Скачивает и сохраняет медиа для сообщения."""

        if not isinstance(message.media, MessageMediaPhoto | MessageMediaDocument):
            return None

        try:
            media_bytes = await message.download_media(file=bytes)
        except Exception as exc:  # pragma: no cover - зависит от Telethon
            logger.warning(
                "collector_media_download_failed",
                extra={
                    "source_id": source.pk,
                    "message_id": getattr(message, "id", None),
                    "error": str(exc),
                },
            )
            return None

        if not media_bytes:
            return None

        if isinstance(media_bytes, memoryview):  # pragma: no cover - зависит от клиента
            media_bytes = media_bytes.tobytes()
        elif isinstance(media_bytes, bytearray):  # pragma: no cover - зависит от клиента
            media_bytes = bytes(media_bytes)
        elif isinstance(media_bytes, str):  # pragma: no cover
            media_bytes = Path(media_bytes).read_bytes()

        extension = self._resolve_media_extension(message)
        relative_path = self._media_storage_path(
            source=source,
            message_id=message.id,
            extension=extension,
        )
        absolute_root = Path(settings.MEDIA_ROOT or "media")
        absolute_path = absolute_root / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(media_bytes)

        media_type = type(message.media).__name__
        return StoredMedia(
            media_type=media_type,
            path=relative_path.as_posix(),
            content=media_bytes,
        )

    def _media_storage_path(self, *, source: Source, message_id: int, extension: str) -> Path:
        """Генерирует путь для хранения медиафайла."""
        filename = f"{message_id}_{uuid.uuid4().hex}{extension}"
        return (
            Path("uploads")
            / "media"
            / str(source.project_id or "0")
            / str(source.pk or "0")
            / filename
        )

    def _resolve_media_extension(self, message: Message) -> str:
        """Определяет расширение файла медиа по информации сообщения."""
        file_info = getattr(message, "file", None)
        extension = ""
        if file_info is not None:
            extension = (getattr(file_info, "ext", "") or "").strip()
            if not extension and getattr(file_info, "mime_type", ""):
                guessed = mimetypes.guess_extension(file_info.mime_type)
                if guessed:
                    extension = guessed
            if not extension and getattr(file_info, "name", ""):
                extension = Path(file_info.name).suffix

        if not extension and isinstance(message.media, MessageMediaPhoto):
            extension = ".jpg"

        if not extension:
            extension = ".bin"

        if not extension.startswith("."):
            extension = f".{extension}"

        return extension


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


async def collect_for_all_users(
    *,
    project_id: int | None = None,
    limit: int = DEFAULT_COLLECT_LIMIT,
    continuous: bool = False,
    interval: int = 60,
) -> None:
    """Запускает сборщик для всех пользователей с заполненными Telethon-данными."""

    delay = max(interval, 5)

    async def _eligible_users() -> list[User]:
        qs = (
            User.objects.filter(is_active=True, telethon_api_id__isnull=False)
            .exclude(telethon_api_hash="")
            .exclude(telethon_session="")
            .order_by("id")
        )
        return await sync_to_async(list, thread_sensitive=True)(qs)

    async def _run_once() -> None:
        users = await _eligible_users()
        if not users:
            logger.info("collect_for_all_users_no_credentials")
            return
        for user in users:
            if not user.has_telethon_credentials:
                continue
            try:
                await collect_for_user(user, project_id=project_id, limit=limit)
            except TelethonCredentialsMissingError as exc:
                logger.warning(
                    "collect_for_all_users_skipped",
                    extra={"user_id": user.pk, "reason": str(exc)},
                )
            except Exception as exc:  # pragma: no cover - защитный слой вокруг сети
                logger.exception(
                    "collect_for_all_users_error",
                    extra={"user_id": user.pk, "error": str(exc)},
                )

    while True:
        await _run_once()
        if not continuous:
            break
        await asyncio.sleep(delay)


async def collect_for_user_live(
    user: User,
    *,
    project_id: int | None = None,
    limit: int = DEFAULT_COLLECT_LIMIT,
    interval: int = 60,
) -> None:
    """Постоянный сбор постов с указанным интервалом опроса."""
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


def collect_for_all_users_sync(
    *,
    project_id: int | None = None,
    limit: int = DEFAULT_COLLECT_LIMIT,
    continuous: bool = False,
    interval: int = 60,
) -> None:
    """Синхронный адаптер, запускающий сбор для всех пользователей."""

    async def runner() -> None:
        await collect_for_all_users(
            project_id=project_id,
            limit=limit,
            continuous=continuous,
            interval=interval,
        )

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass


def _normalize_raw(value):
    """Рекурсивно преобразует неподдерживаемые типы JSON (например, datetime) в строки."""

    if isinstance(value, dict):
        return {key: _normalize_raw(sub) for key, sub in value.items()}
    if isinstance(value, list):
        return [_normalize_raw(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_raw(item) for item in value]
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    return value
