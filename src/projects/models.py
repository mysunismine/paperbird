"""Модели проектов, источников и постов."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from django.conf import settings
from django.db import models
from django.utils import timezone


class Project(models.Model):
    """Проект объединяет источники и собранные посты."""

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="projects",
        verbose_name="Владелец",
    )
    name = models.CharField("Название", max_length=200)
    description = models.TextField("Описание", blank=True)
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Проект"
        verbose_name_plural = "Проекты"
        unique_together = ("owner", "name")
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.name}"


class Source(models.Model):
    """Источник данных из Telegram."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="sources",
        verbose_name="Проект",
    )
    title = models.CharField("Название", max_length=255, blank=True)
    telegram_id = models.BigIntegerField(
        "Telegram ID",
        help_text="Идентификатор канала или чата (peer id).",
    )
    username = models.CharField(
        "Username",
        max_length=255,
        blank=True,
        help_text="Публичный username канала без @",
    )
    invite_link = models.CharField(
        "Инвайт ссылка",
        max_length=512,
        blank=True,
        help_text="Ссылка-приглашение, если канал приватный.",
    )
    include_keywords = models.JSONField(
        "Whitelist ключевых слов",
        default=list,
        blank=True,
        help_text=(
            "Посты проходят фильтр, если содержат одно из слов (регистронезависимо). "
            "Оставьте пустым для отключения."
        ),
    )
    exclude_keywords = models.JSONField(
        "Blacklist ключевых слов",
        default=list,
        blank=True,
        help_text="Посты игнорируются, если содержат слова из списка.",
    )
    deduplicate_text = models.BooleanField("Дедупликация текста", default=True)
    deduplicate_media = models.BooleanField("Дедупликация медиа", default=True)
    retention_days = models.PositiveIntegerField(
        "Срок хранения (дней)",
        default=7,
        help_text="По истечении срока медиафайлы должны быть удалены фоновым процессом.",
    )
    last_synced_id = models.BigIntegerField(
        "Последний обработанный пост",
        blank=True,
        null=True,
        help_text="Используется для инкрементального сбора.",
    )
    last_synced_at = models.DateTimeField("Последний сбор", blank=True, null=True)
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Источник"
        verbose_name_plural = "Источники"
        unique_together = ("project", "telegram_id")
        ordering = ("project", "title")

    def __str__(self) -> str:
        return self.title or self.username or str(self.telegram_id)

    def _normalize_keywords(self, values: Iterable[str]) -> list[str]:
        return sorted({value.strip().lower() for value in values if value and value.strip()})

    def clean(self) -> None:
        self.include_keywords = self._normalize_keywords(self.include_keywords)
        self.exclude_keywords = self._normalize_keywords(self.exclude_keywords)
        super().clean()

    # --- Фильтрация постов -------------------------------------------------

    def matches_keywords(self, text: str) -> bool:
        """Проверка whitelist/blacklist для текста поста."""

        if not text:
            return not self.include_keywords

        normalized = text.lower()
        if self.include_keywords:
            if not any(keyword in normalized for keyword in self.include_keywords):
                return False
        if self.exclude_keywords:
            if any(keyword in normalized for keyword in self.exclude_keywords):
                return False
        return True

    def should_skip(self, *, text_hash: str | None, media_hash: str | None) -> bool:
        """Решение, нужно ли пропустить сообщение из-за дубликатов."""

        if text_hash and self.deduplicate_text:
            if Post.objects.filter(source=self, text_hash=text_hash).exists():
                return True
        if media_hash and self.deduplicate_media:
            if Post.objects.filter(source=self, media_hash=media_hash).exists():
                return True
        return False


class PostQuerySet(models.QuerySet):
    def for_processing(self):
        return self.filter(status=Post.Status.NEW)


class Post(models.Model):
    """Сохранённый пост из Telegram."""

    class Status(models.TextChoices):
        NEW = "new", "Новый"
        PROCESSING = "processing", "В обработке"
        USED = "used", "Использован"
        DELETED = "deleted", "Удалён"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="posts",
        verbose_name="Проект",
    )
    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        related_name="posts",
        verbose_name="Источник",
    )
    telegram_id = models.BigIntegerField("Telegram ID")
    message = models.TextField("Текст", blank=True)
    raw = models.JSONField("Сырые данные", default=dict, blank=True)
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
    )
    posted_at = models.DateTimeField("Дата публикации")
    collected_at = models.DateTimeField("Собран", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)
    has_media = models.BooleanField("Есть медиа", default=False)
    media_type = models.CharField("Тип медиа", max_length=50, blank=True)
    media_path = models.CharField("Путь к медиа", max_length=512, blank=True)
    text_hash = models.CharField("Хэш текста", max_length=64, blank=True)
    media_hash = models.CharField("Хэш медиа", max_length=64, blank=True)

    objects = PostQuerySet.as_manager()

    class Meta:
        verbose_name = "Пост"
        verbose_name_plural = "Посты"
        ordering = ("-posted_at",)
        unique_together = ("source", "telegram_id")
        indexes = [
            models.Index(fields=("source", "status")),
            models.Index(fields=("project", "status")),
        ]

    def __str__(self) -> str:
        return f"{self.source}: {self.telegram_id}"

    @staticmethod
    def make_hash(value: str | bytes | None) -> str:
        """Возвращает SHA256 хэш строки или байтов."""

        if value is None:
            return ""
        if isinstance(value, str):
            value = value.encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    @classmethod
    def create_or_update(
        cls,
        *,
        project: Project,
        source: Source,
        telegram_id: int,
        message: str,
        posted_at,
        raw_data: dict,
        media_type: str | None = None,
        media_path: str | None = None,
        media_bytes: bytes | None = None,
    ) -> Post:
        """Сохраняет пост, обновляя существующий при повторном сборе."""

        text_hash = cls.make_hash(message) if message else ""
        media_hash = cls.make_hash(media_bytes) if media_bytes else ""
        defaults = {
            "project": project,
            "message": message or "",
            "raw": raw_data,
            "posted_at": posted_at,
            "has_media": bool(media_type),
            "media_type": media_type or "",
            "media_path": media_path or "",
            "text_hash": text_hash,
            "media_hash": media_hash,
        }
        post, _created = cls.objects.update_or_create(
            source=source,
            telegram_id=telegram_id,
            defaults=defaults,
        )
        return post

    def mark_used(self) -> None:
        self.status = self.Status.USED
        self.save(update_fields=["status", "updated_at"])

    def mark_deleted(self) -> None:
        self.status = self.Status.DELETED
        self.save(update_fields=["status", "updated_at"])


class SourceSyncLog(models.Model):
    """История запусков сборщика."""

    source = models.ForeignKey(
        Source,
        on_delete=models.CASCADE,
        related_name="sync_logs",
        verbose_name="Источник",
    )
    started_at = models.DateTimeField("Старт", default=timezone.now)
    finished_at = models.DateTimeField("Завершение", blank=True, null=True)
    fetched_messages = models.PositiveIntegerField("Получено сообщений", default=0)
    skipped_messages = models.PositiveIntegerField("Пропущено", default=0)
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=[
            ("ok", "Успех"),
            ("partial", "Частично"),
            ("failed", "Ошибка"),
        ],
        default="ok",
    )
    error_message = models.TextField("Ошибка", blank=True)

    class Meta:
        verbose_name = "Лог синхронизации источника"
        verbose_name_plural = "Логи синхронизации источников"
        ordering = ("-started_at",)

    def finish(
        self,
        status: str = "ok",
        error: str | None = None,
        fetched: int = 0,
        skipped: int = 0,
    ) -> None:
        self.finished_at = timezone.now()
        self.status = status
        self.error_message = error or ""
        self.fetched_messages = fetched
        self.skipped_messages = skipped
        self.save()
