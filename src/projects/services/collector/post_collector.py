"""Core Telegram post collector implementation."""

from __future__ import annotations

import logging
import mimetypes
import uuid
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from telethon.tl.custom.message import Message as TelethonMessage
from telethon.tl.types import (
    MessageMediaDocument as TelethonMessageMediaDocument,
    MessageMediaPhoto as TelethonMessageMediaPhoto,
)

from accounts.models import User
from core.constants import DEFAULT_COLLECT_LIMIT
from projects.models import Post, Project, Source, SourceSyncLog
from projects.services.telethon_client import TelethonClientFactory

from .utils import _normalize_raw

logger = logging.getLogger(__name__)


def _collector_message_type() -> type[TelethonMessage]:
    from projects.services import collector as collector_pkg

    return getattr(collector_pkg, "Message", TelethonMessage)


def _collector_media_types() -> tuple[
    type[TelethonMessageMediaPhoto],
    type[TelethonMessageMediaDocument],
]:
    from projects.services import collector as collector_pkg

    return (
        getattr(collector_pkg, "MessageMediaPhoto", TelethonMessageMediaPhoto),
        getattr(collector_pkg, "MessageMediaDocument", TelethonMessageMediaDocument),
    )


@dataclass
class CollectOptions:
    """Параметры сбора."""

    limit: int = DEFAULT_COLLECT_LIMIT
    with_media: bool = True


@dataclass
class StoredMedia:
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
                        if not isinstance(message, _collector_message_type()):
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

    async def _process_message(self, *, message: TelethonMessage, source: Source) -> bool:
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
        message: TelethonMessage,
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
        message: TelethonMessage,
        source: Source,
    ) -> StoredMedia | None:
        """Скачивает и сохраняет медиа для сообщения."""

        media_photo, media_document = _collector_media_types()
        if not isinstance(message.media, media_photo | media_document):
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

    def _resolve_media_extension(self, message: TelethonMessage) -> str:
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

        media_photo, _ = _collector_media_types()
        if not extension and isinstance(message.media, media_photo):
            extension = ".jpg"

        if not extension:
            extension = ".bin"

        if not extension.startswith("."):
            extension = f".{extension}"

        return extension
