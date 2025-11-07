"""Модели проектов, источников и постов."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.constants import (
    IMAGE_DEFAULT_MODEL,
    IMAGE_DEFAULT_QUALITY,
    IMAGE_DEFAULT_SIZE,
    IMAGE_MODEL_CHOICES,
    IMAGE_QUALITY_CHOICES,
    IMAGE_SIZE_CHOICES,
    REWRITE_DEFAULT_MODEL,
    REWRITE_MODEL_CHOICES,
)
from projects.services.language import detect_language

# Импорт только для подсказок типов.
if TYPE_CHECKING:  # pragma: no cover - используется только для подсказок типов
    from projects.services.post_filters import PostFilterOptions


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
    publish_target = models.CharField(
        "Целевой канал",
        max_length=255,
        blank=True,
        help_text="Например, @my_channel или ссылка на чат",
    )
    rewrite_model = models.CharField(
        "Модель рерайта",
        max_length=100,
        choices=REWRITE_MODEL_CHOICES,
        default=REWRITE_DEFAULT_MODEL,
        help_text="Выберите модель GPT, которая будет использоваться при переписывании текста.",
    )
    image_model = models.CharField(
        "Модель генерации изображений",
        max_length=100,
        choices=IMAGE_MODEL_CHOICES,
        default=IMAGE_DEFAULT_MODEL,
    )
    image_size = models.CharField(
        "Размер изображения",
        max_length=20,
        choices=IMAGE_SIZE_CHOICES,
        default=IMAGE_DEFAULT_SIZE,
    )
    image_quality = models.CharField(
        "Качество изображения",
        max_length=20,
        choices=IMAGE_QUALITY_CHOICES,
        default=IMAGE_DEFAULT_QUALITY,
    )
    retention_days = models.PositiveIntegerField(
        "Срок хранения постов (дней)",
        default=90,
        help_text="Посты старше указанного срока удаляются из ленты проекта.",
    )
    collector_enabled = models.BooleanField(
        "Сборщик активен",
        default=False,
        help_text="Если включено, фоновый сборщик будет регулярно обновлять ленту проекта.",
    )
    collector_interval = models.PositiveIntegerField(
        "Интервал сбора (сек)",
        default=300,
        help_text="Через какой промежуток времени запускать следующий цикл сбора.",
    )
    collector_last_run = models.DateTimeField(
        "Последний запуск сборщика",
        blank=True,
        null=True,
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Проект"
        verbose_name_plural = "Проекты"
        unique_together = ("owner", "name")
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.name}"

    def retention_cutoff(self):
        """Возвращает дату, старше которой посты подлежат удалению."""

        if not self.retention_days:
            return None
        return timezone.now() - timedelta(days=self.retention_days)


class WebPreset(models.Model):
    """Описывает JSON-пресет для универсального веб-парсера."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        ACTIVE = "active", "Активный"
        BROKEN = "broken", "Сломан"

    name = models.CharField("Системное имя", max_length=100)
    version = models.CharField("Версия", max_length=50, default="1.0.0")
    title = models.CharField("Название", max_length=255, blank=True)
    description = models.TextField("Описание", blank=True)
    schema_version = models.PositiveIntegerField("Версия схемы", default=1)
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    checksum = models.CharField("Контрольная сумма", max_length=64)
    config = models.JSONField("Конфигурация", default=dict, blank=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    class Meta:
        verbose_name = "Веб-пресет"
        verbose_name_plural = "Веб-пресеты"
        ordering = ("name", "version")
        constraints = [
            models.UniqueConstraint(
                fields=("name", "version"),
                name="web_preset_unique_version",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE


class Source(models.Model):
    """Источник данных: Telegram канал или веб-сайт с пресетом."""

    class Type(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        WEB = "web", "Web"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="sources",
        verbose_name="Проект",
    )
    type = models.CharField(
        "Тип источника",
        max_length=20,
        choices=Type.choices,
        default=Type.TELEGRAM,
    )
    title = models.CharField("Название", max_length=255, blank=True)
    telegram_id = models.BigIntegerField(
        "Telegram ID",
        blank=True,
        null=True,
        help_text="Опционально: числовой идентификатор канала (peer id).",
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
    web_preset = models.ForeignKey(
        WebPreset,
        on_delete=models.PROTECT,
        related_name="sources",
        blank=True,
        null=True,
        verbose_name="Пресет веб-парсера",
    )
    web_preset_snapshot = models.JSONField(
        "Снимок пресета",
        default=dict,
        blank=True,
        help_text="Конфигурация, с которой работает источник (фиксируется при импорте).",
    )
    web_last_synced_at = models.DateTimeField("Последний веб-сбор", blank=True, null=True)
    web_last_status = models.CharField("Статус последнего веб-сбора", max_length=20, blank=True)
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
        return self.title or self.username or (str(self.telegram_id) if self.telegram_id else "Источник")

    def _normalize_keywords(self, values: Iterable[str]) -> list[str]:
        return sorted({value.strip().lower() for value in values if value and value.strip()})

    def clean(self) -> None:
        self.include_keywords = self._normalize_keywords(self.include_keywords)
        self.exclude_keywords = self._normalize_keywords(self.exclude_keywords)
        if self.type == self.Type.WEB:
            if not self.web_preset_id:
                raise ValidationError("Для веб-источника нужно прикрепить пресет.")
            if not self.web_preset_snapshot:
                self.web_preset_snapshot = self.web_preset.config
        super().clean()

    def active_web_preset(self) -> dict:
        """Возвращает снимок активного пресета."""

        if self.web_preset_snapshot:
            return self.web_preset_snapshot
        if self.web_preset:
            return self.web_preset.config
        return {}

    def has_web_duplicates(
        self,
        *,
        source_url: str | None,
        canonical_url: str | None,
        content_hash: str | None,
    ) -> bool:
        """Проверяет, существует ли уже пост из веб-источника по одному из идентификаторов."""

        query = Post.objects.filter(source=self, origin_type=Post.Origin.WEB)
        filters = models.Q()
        if source_url:
            filters |= models.Q(source_url=source_url)
        if canonical_url:
            filters |= models.Q(canonical_url=canonical_url)
        if content_hash:
            filters |= models.Q(content_hash=content_hash)
        if not filters:
            return False
        return query.filter(filters).exists()

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


class ProjectPromptConfig(models.Model):
    """Набор фрагментов основного промта проекта."""

    project = models.OneToOneField(
        Project,
        on_delete=models.CASCADE,
        related_name="prompt_config",
        verbose_name="Проект",
    )
    system_role = models.TextField("Системная роль", blank=True)
    task_instruction = models.TextField("Задание", blank=True)
    documents_intro = models.TextField("Описание документов", blank=True)
    style_requirements = models.TextField("Требования к стилю", blank=True)
    output_format = models.TextField("Формат ответа", blank=True)
    output_example = models.TextField("Пример вывода", blank=True)
    editor_comment_note = models.TextField("Комментарий редактора", blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Шаблон промтов проекта"
        verbose_name_plural = "Шаблоны промтов проекта"

    def __str__(self) -> str:
        return f"Промт проекта «{self.project.name}»"


class PostQuerySet(models.QuerySet):
    def for_processing(self) -> PostQuerySet:
        return self.filter(status=Post.Status.NEW)

    def apply_filters(self, options: PostFilterOptions) -> PostQuerySet:
        """Применяет расширенные фильтры к queryset."""

        from projects.services.post_filters import apply_post_filters

        return apply_post_filters(self, options)


class Post(models.Model):
    """Сохранённый пост из источника (Telegram или Web)."""

    class Status(models.TextChoices):
        NEW = "new", "Новый"
        PROCESSING = "processing", "В обработке"
        USED = "used", "Использован"
        DELETED = "deleted", "Удалён"

    class Language(models.TextChoices):
        RU = "ru", "Русский"
        EN = "en", "Английский"
        UNKNOWN = "unknown", "Не определён"

    class Origin(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        WEB = "web", "Web"

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
    origin_type = models.CharField(
        "Тип источника",
        max_length=20,
        choices=Origin.choices,
        default=Origin.TELEGRAM,
    )
    telegram_id = models.BigIntegerField("Telegram ID", blank=True, null=True)
    external_id = models.CharField("Внешний ID", max_length=255, blank=True)
    source_url = models.URLField("URL источника", max_length=1000, blank=True)
    canonical_url = models.URLField("Канонический URL", max_length=1000, blank=True)
    message = models.TextField("Текст", blank=True)
    raw = models.JSONField("Сырые данные", default=dict, blank=True)
    raw_html = models.TextField("Сырый HTML", blank=True)
    content_html = models.TextField("HTML контент", blank=True)
    content_md = models.TextField("Markdown контент", blank=True)
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
    content_hash = models.CharField("Хэш контента", max_length=64, blank=True)
    images_manifest = models.JSONField("Изображения", default=list, blank=True)
    external_metadata = models.JSONField("Метаданные источника", default=dict, blank=True)
    language = models.CharField(
        "Язык",
        max_length=16,
        choices=Language.choices,
        default=Language.UNKNOWN,
    )

    objects = PostQuerySet.as_manager()

    class Meta:
        verbose_name = "Пост"
        verbose_name_plural = "Посты"
        ordering = ("-posted_at",)
        indexes = [
            models.Index(fields=("source", "status")),
            models.Index(fields=("project", "status")),
            models.Index(fields=("origin_type", "source")),
            models.Index(fields=("source_url",)),
            models.Index(fields=("canonical_url",)),
            models.Index(fields=("content_hash",)),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("source", "telegram_id"),
                name="post_unique_telegram_source",
                condition=models.Q(origin_type="telegram"),
            ),
            models.UniqueConstraint(
                fields=("source", "external_id"),
                name="post_unique_web_source_external",
                condition=models.Q(origin_type="web"),
            ),
        ]

    def __str__(self) -> str:
        identifier = (
            self.telegram_id
            if self.origin_type == self.Origin.TELEGRAM
            else self.external_id or self.source_url or ""
        )
        return f"{self.source}: {identifier or 'post'}"

    @property
    def media_url(self) -> str | None:
        """Возвращает публичный URL медиафайла, если он доступен."""

        if not self.media_path:
            return None
        base = getattr(settings, "MEDIA_URL", "") or ""
        base = base.rstrip("/")
        relative = self.media_path.lstrip("/")
        if not base:
            return f"/{relative}"
        return f"{base}/{relative}"

    @property
    def origin_identifier(self) -> str:
        """Возвращает человекочитаемый идентификатор поста."""

        if self.origin_type == self.Origin.TELEGRAM:
            return str(self.telegram_id or "")
        return self.canonical_url or self.source_url or self.external_id or ""

    @property
    def external_link(self) -> str | None:
        """Ссылка на оригинальный материал для веб-постов."""

        if self.origin_type != self.Origin.WEB:
            return None
        return self.canonical_url or self.source_url

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
        language = detect_language(message)
        defaults = {
            "project": project,
            "origin_type": cls.Origin.TELEGRAM,
            "message": message or "",
            "raw": raw_data,
            "posted_at": posted_at,
            "has_media": bool(media_type),
            "media_type": media_type or "",
            "media_path": media_path or "",
            "text_hash": text_hash,
            "media_hash": media_hash,
            "language": language,
        }
        post, _created = cls.objects.update_or_create(
            source=source,
            telegram_id=telegram_id,
            defaults=defaults,
        )
        return post

    @classmethod
    def create_or_update_web(
        cls,
        *,
        project: Project,
        source: Source,
        source_url: str,
        canonical_url: str | None,
        title: str,
        content_html: str,
        content_md: str,
        raw_html: str,
        raw_data: dict,
        posted_at,
        images: list[str] | None = None,
    ) -> tuple["Post", bool]:
        """Создаёт или обновляет пост, полученный с веб-сайта."""

        normalized_canonical = canonical_url or ""
        normalized_source = source_url
        body_for_hash = content_md or content_html or title
        content_hash = cls.make_hash(body_for_hash)
        text_hash = cls.make_hash(body_for_hash)
        language = detect_language(body_for_hash)
        lookup = models.Q(source_url=normalized_source)
        if normalized_canonical:
            lookup |= models.Q(canonical_url=normalized_canonical)
        if content_hash:
            lookup |= models.Q(content_hash=content_hash)
        existing = (
            cls.objects.filter(source=source, origin_type=cls.Origin.WEB)
            .filter(lookup)
            .order_by("-posted_at")
            .first()
        )
        defaults: dict[str, Any] = {
            "project": project,
            "source": source,
            "origin_type": cls.Origin.WEB,
            "external_id": (normalized_canonical or normalized_source)[:255],
            "source_url": normalized_source,
            "canonical_url": normalized_canonical,
            "message": content_md or content_html or title,
            "raw": raw_data or {},
            "raw_html": raw_html or "",
            "content_html": content_html or "",
            "content_md": content_md or "",
            "posted_at": posted_at,
            "has_media": bool(images),
            "text_hash": text_hash,
            "content_hash": content_hash,
            "images_manifest": images or [],
            "external_metadata": {"title": title, **(raw_data or {})},
            "language": language,
        }
        update_fields = [field for field in defaults.keys() if field not in {"project"}]
        if existing:
            for field, value in defaults.items():
                if field == "project":
                    continue
                setattr(existing, field, value)
            existing.save(update_fields=[*update_fields, "updated_at"])
            return existing, False
        post = cls.objects.create(**defaults)
        return post, True

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
