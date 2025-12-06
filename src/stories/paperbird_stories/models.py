"""Модели сюжетов и задач рерайта."""

from __future__ import annotations

import json
import mimetypes
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field

from django.core.files.base import ContentFile
from django.db import models
from django.utils import timezone

from core.constants import REWRITE_DEFAULT_MAX_TOKENS
from projects.models import Post, Project


class Story(models.Model):
    """Сюжет, объединяющий несколько постов."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        REWRITING = "rewriting", "Рерайт"
        READY = "ready", "Готов к публикации"
        PUBLISHED = "published", "Опубликован"

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="stories",
        verbose_name="Проект",
    )
    title = models.CharField("Заголовок", max_length=255, blank=True)
    summary = models.TextField("Краткое описание", blank=True)
    body = models.TextField("Текст", blank=True)
    hashtags = models.JSONField("Хэштеги", default=list, blank=True)
    sources = models.JSONField("Источники", default=list, blank=True)
    editor_comment = models.TextField(
        "Комментарий редактора",
        blank=True,
        help_text="Инструкции для модели перед рерайтом.",
    )
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    prompt_snapshot = models.JSONField(
        "Последний промпт",
        default=list,
        blank=True,
        help_text="Сообщения, отправленные в модель при последнем рерайте.",
    )
    last_rewrite_payload = models.JSONField(
        "Результат рерайта",
        default=dict,
        blank=True,
        help_text="Сырые данные ответа модели.",
    )
    image_prompt = models.TextField(
        "Описание изображения",
        blank=True,
        default="",
        help_text="Последний промпт, по которому было сгенерировано изображение.",
    )
    image_file = models.FileField(
        "Прикреплённое изображение",
        upload_to="story_images/",
        blank=True,
        null=True,
    )
    last_rewrite_at = models.DateTimeField(
        "Дата последнего рерайта",
        blank=True,
        null=True,
    )
    last_rewrite_preset = models.ForeignKey(
        "RewritePreset",
        on_delete=models.SET_NULL,
        related_name="stories",
        blank=True,
        null=True,
        verbose_name="Последний пресет",
    )
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)

    posts = models.ManyToManyField(
        Post,
        through="StoryPost",
        related_name="stories",
        verbose_name="Посты",
    )

    class Meta:
        verbose_name = "Сюжет"
        verbose_name_plural = "Сюжеты"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.title or f"Сюжет #{self.pk}"

    # --- Работа с постами -------------------------------------------------

    def attach_posts(self, posts: Iterable[Post]) -> None:
        """Привязывает посты к сюжету, сохраняя порядок передачи."""

        StoryPost.objects.filter(story=self).delete()
        story_posts = [
            StoryPost(story=self, post=post, position=index)
            for index, post in enumerate(posts)
        ]
        StoryPost.objects.bulk_create(story_posts)

    def ordered_posts(self) -> models.QuerySet[Post]:
        """Возвращает queryset постов в порядке `StoryPost.position`."""

        return self.posts.order_by("story_posts__position", "story_posts__id")

    # --- Обновление статуса и содержания ----------------------------------

    def mark_rewriting(self) -> None:
        """Отмечает сюжет как находящийся в процессе рерайта."""
        self.status = self.Status.REWRITING
        self.save(update_fields=["status", "updated_at"])

    def apply_rewrite(
        self,
        *,
        title: str,
        summary: str,
        body: str,
        hashtags: list[str],
        sources: list[str],
        payload: dict,
        preset: RewritePreset | None = None,
    ) -> None:
        """Применяет результаты рерайта к сюжету."""
        self.title = title
        self.summary = summary
        self.body = body
        self.hashtags = hashtags
        self.sources = sources
        self.status = self.Status.READY
        self.last_rewrite_payload = payload
        self.last_rewrite_at = timezone.now()
        self.last_rewrite_preset = preset
        self.save(
            update_fields=[
                "title",
                "summary",
                "body",
                "hashtags",
                "sources",
                "status",
                "last_rewrite_payload",
                "last_rewrite_at",
                "last_rewrite_preset",
                "updated_at",
            ]
        )

    def mark_published(self) -> None:
        """Отмечает сюжет как опубликованный."""

        self.status = self.Status.PUBLISHED
        self.save(update_fields=["status", "updated_at"])

    def compose_publication_text(self) -> str:
        """Собирает текст для публикации (заголовок, тело, хэштеги, источники)."""

        parts: list[str] = []
        title = (self.title or "").strip()
        if title:
            parts.append(title)
        body = (self.body or "").strip()
        if body:
            parts.append(body)
        if self.hashtags:
            tags = " ".join(f"#{tag.lstrip('#').replace(' ', '_')}" for tag in self.hashtags if tag)
            if tags:
                parts.append(tags)
        if self.sources:
            sources_text = ", ".join(source for source in self.sources if source)
            if sources_text:
                parts.append(f"Источники: {sources_text}")
        combined = "\n\n".join(part for part in parts if part)
        return combined.strip()

    # --- Работа с изображением ----------------------------------------------

    def attach_image(self, *, prompt: str, data: bytes, mime_type: str) -> None:
        """Сохраняет изображение сюжета, заменяя предыдущее."""
        """Сохраняет изображение сюжета, заменяя предыдущее."""

        if not data:
            raise ValueError("Пустые данные изображения")

        extension = self._extension_from_mime(mime_type)
        filename = f"story_{self.pk}_{uuid.uuid4().hex}.{extension}"
        content = ContentFile(data)

        if self.image_file:
            self.image_file.delete(save=False)

        self.image_file.save(filename, content, save=False)
        self.image_prompt = prompt.strip()
        self.save(update_fields=["image_prompt", "image_file", "updated_at"])

    def remove_image(self) -> None:
        """Удаляет прикреплённое изображение."""
        """Удаляет прикреплённое изображение."""

        if self.image_file:
            self.image_file.delete(save=False)
        self.image_file = None
        self.image_prompt = ""
        self.save(update_fields=["image_prompt", "image_file", "updated_at"])

    @staticmethod
    def _extension_from_mime(mime_type: str) -> str:
        """Определяет расширение файла по MIME-типу."""
        default_extension = "png"
        if not mime_type:
            return default_extension
        extension = mimetypes.guess_extension(mime_type) or ""
        extension = extension.lstrip(".")
        if extension:
            return extension
        if mime_type == "image/jpeg":
            return "jpg"
        return default_extension


class StoryPost(models.Model):
    """Связь сюжета с постом и позиция поста внутри сюжета."""
    """Связь сюжета с постом и позиция поста внутри сюжета."""

    story = models.ForeignKey(
        Story,
        on_delete=models.CASCADE,
        related_name="story_posts",
        verbose_name="Сюжет",
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="story_posts",
        verbose_name="Пост",
    )
    position = models.PositiveIntegerField("Позиция", default=0)
    added_at = models.DateTimeField("Добавлен", auto_now_add=True)

    class Meta:
        verbose_name = "Пост сюжета"
        verbose_name_plural = "Посты сюжетов"
        ordering = ("position", "id")
        unique_together = ("story", "post")

    def __str__(self) -> str:
        return f"{self.story_id}->{self.post_id} ({self.position})"


class RewriteTask(models.Model):
    """Задача рерайта для сюжета."""

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        RUNNING = "running", "В работе"
        SUCCESS = "success", "Успех"
        FAILED = "failed", "Ошибка"

    story = models.ForeignKey(
        Story,
        on_delete=models.CASCADE,
        related_name="rewrite_tasks",
        verbose_name="Сюжет",
    )
    provider = models.CharField("Провайдер", max_length=50, default="openai")
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    prompt_messages = models.JSONField(
        "Промпт",
        default=list,
        blank=True,
        help_text="Сообщения, отправленные модели.",
    )
    editor_comment = models.TextField("Комментарий редактора", blank=True)
    result = models.JSONField("Результат", default=dict, blank=True)
    error_message = models.TextField("Ошибка", blank=True)
    response_id = models.CharField("ID ответа", max_length=128, blank=True)
    attempts = models.PositiveIntegerField("Попытки", default=0)
    started_at = models.DateTimeField("Начато", blank=True, null=True)
    finished_at = models.DateTimeField("Завершено", blank=True, null=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)
    preset = models.ForeignKey(
        "RewritePreset",
        on_delete=models.SET_NULL,
        related_name="rewrite_tasks",
        blank=True,
        null=True,
        verbose_name="Пресет",
    )

    class Meta:
        verbose_name = "Задача рерайта"
        verbose_name_plural = "Задачи рерайта"
        ordering = ("-created_at",)

    def mark_running(self) -> None:
        """Отмечает задачу рерайта как запущенную."""
        self.status = self.Status.RUNNING
        self.attempts += 1
        self.started_at = timezone.now()
        self.save(update_fields=["status", "attempts", "started_at", "updated_at"])

    def mark_success(self, *, result: dict, response_id: str | None = None) -> None:
        """Отмечает задачу рерайта как успешно выполненную."""
        self.status = self.Status.SUCCESS
        self.result = result
        self.response_id = response_id or ""
        self.finished_at = timezone.now()
        self.error_message = ""
        self.save(
            update_fields=[
                "status",
                "result",
                "response_id",
                "finished_at",
                "error_message",
                "updated_at",
            ]
        )

    def mark_failed(self, *, error: str) -> None:
        """Отмечает задачу рерайта как проваленную."""
        self.status = self.Status.FAILED
        self.error_message = error
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "error_message", "finished_at", "updated_at"])


@dataclass(slots=True)
class RewriteResult:
    """Типизированный результат рерайта."""

    title: str
    content: str
    summary: str = ""
    hashtags: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> RewriteResult:
        """Создает объект RewriteResult из словаря."""
        title = str(data.get("title", "")).strip()
        raw_content = data.get("content")
        if raw_content is None and "text" in data:
            raw_content = data.get("text")
        content = cls._coerce_content(raw_content)
        if not content:
            raise ValueError("Ответ модели не содержит текста контента")
        return cls(title=title, content=content)

    @staticmethod
    def _coerce_content(value: object) -> str:
        """Приводит контент к строковому виду."""
        texts: list[str] = []
        seen: set[str] = set()

        def add_text(text: str) -> None:
            normalized = text.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                texts.append(normalized)

        def collect(node: object) -> None:
            if not node:
                return
            if isinstance(node, str):
                add_text(node)
                return
            if isinstance(node, list | tuple | set):
                for item in node:
                    collect(item)
                return
            if isinstance(node, dict):
                direct_keys = ("text", "value")
                for key in direct_keys:
                    if key in node and isinstance(node[key], str):
                        add_text(node[key])
                container_keys = (
                    "paragraphs",
                    "chunks",
                    "children",
                    "items",
                    "nodes",
                    "sections",
                    "parts",
                    "content",
                )
                for key in container_keys:
                    if key in node:
                        collect(node[key])
                for value in node.values():
                    if isinstance(value, list | tuple | set | dict):
                        collect(value)
                return
            add_text(str(node))

        collect(value)
        return "\n\n".join(texts)


class Publication(models.Model):
    """Факт публикации сюжета."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Запланирована"
        PUBLISHING = "publishing", "В процессе"
        PUBLISHED = "published", "Опубликована"
        FAILED = "failed", "Ошибка"

    story = models.ForeignKey(
        Story,
        on_delete=models.CASCADE,
        related_name="publications",
        verbose_name="Сюжет",
    )
    target = models.CharField("Цель публикации", max_length=255)
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.SCHEDULED,
    )
    result_text = models.TextField("Текст публикации", blank=True)
    scheduled_for = models.DateTimeField("Запланировано на", blank=True, null=True)
    published_at = models.DateTimeField("Опубликовано в", blank=True, null=True)
    message_ids = models.JSONField("ID сообщений", default=list, blank=True)
    error_message = models.TextField("Ошибка", blank=True)
    attempts = models.PositiveIntegerField("Попытки", default=0)
    raw_response = models.JSONField("Ответ Telegram", default=dict, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Публикация"
        verbose_name_plural = "Публикации"
        ordering = ("-created_at",)

    def mark_publishing(self) -> None:
        """Отмечает публикацию как находящуюся в процессе."""
        self.status = self.Status.PUBLISHING
        self.attempts += 1
        self.save(update_fields=["status", "attempts", "updated_at"])

    def mark_published(
        self,
        *,
        message_ids: list[int],
        published_at,
        raw: dict | None = None,
    ) -> None:
        """Отмечает публикацию как опубликованную."""
        self.status = self.Status.PUBLISHED
        self.message_ids = message_ids
        self.published_at = published_at
        self.error_message = ""
        if raw is not None:
            self.raw_response = raw
        self.save(
            update_fields=[
                "status",
                "message_ids",
                "published_at",
                "error_message",
                "raw_response",
                "updated_at",
            ]
        )

    def mark_failed(self, *, error: str) -> None:
        """Отмечает публикацию как проваленную."""
        self.status = self.Status.FAILED
        self.error_message = error
        self.save(update_fields=["status", "error_message", "updated_at"])

    def resolved_target(self) -> str:
        """Возвращает целевой канал, учитывая настройки проекта."""

        project_target = (self.story.project.publish_target or "").strip()
        if project_target:
            return project_target
        return (self.target or "").strip()

    def _target_alias(self) -> str | None:
        """Приводит целевой канал к alias для формирования ссылки."""

        target = self.resolved_target()
        if not target:
            return None
        normalized = target.strip()
        if normalized.startswith("@"):
            alias = normalized[1:]
            return alias or None
        lowered = normalized.lower()
        if lowered.startswith(("https://t.me/", "http://t.me/")):
            start = lowered.index("t.me/") + len("t.me/")
            alias = normalized[start:].strip("/")
            if alias.startswith("+") or not alias:
                return None
            return alias
        if lowered.startswith("tg://resolve?domain="):
            alias = normalized.split("domain=", 1)[1]
            alias = alias.split("&", 1)[0]
            return alias or None
        return None

    def primary_message_id(self) -> int | None:
        """Возвращает первый ID сообщения из публикации."""

        for value in self.message_ids or []:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    def message_url(self) -> str | None:
        """Формирует ссылку на опубликованное сообщение, если возможно."""

        if self.status != self.Status.PUBLISHED:
            return None
        alias = self._target_alias()
        message_id = self.primary_message_id()
        if not alias or not message_id:
            return None
        return f"https://t.me/{alias}/{message_id}"


class RewritePreset(models.Model):
    """Настраиваемый пресет рерайта для проекта."""

    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="rewrite_presets",
        verbose_name="Проект",
    )
    name = models.CharField("Название", max_length=100)
    description = models.TextField("Описание", blank=True)
    style = models.CharField("Стиль", max_length=255, blank=True)
    editor_comment = models.TextField("Комментарий редактора", blank=True)
    max_length_tokens = models.PositiveIntegerField(
        "Максимальное количество токенов",
        default=REWRITE_DEFAULT_MAX_TOKENS,
    )
    output_format = models.JSONField("Формат вывода", default=dict, blank=True)
    is_active = models.BooleanField("Активен", default=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Пресет рерайта"
        verbose_name_plural = "Пресеты рерайта"
        ordering = ("name",)
        unique_together = ("project", "name")

    def __str__(self) -> str:
        return f"{self.project.name}: {self.name}"

    def instruction_block(self) -> str:
        """Формирует человекочитаемое описание настроек пресета."""

        parts: list[str] = []
        if self.description:
            parts.append(f"Описание: {self.description.strip()}")
        if self.style:
            parts.append(f"Стиль: {self.style.strip()}")
        if self.max_length_tokens:
            parts.append(
                "Максимальная длина ответа: "
                f"{self.max_length_tokens} токенов"
            )
        if self.output_format:
            formatted = json.dumps(self.output_format, ensure_ascii=False, indent=2)
            parts.append(f"Формат вывода:\n{formatted}")
        return "\n".join(parts)
