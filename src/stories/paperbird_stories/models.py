"""Модели сюжетов и задач рерайта."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.db import models
from django.utils import timezone

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
    last_rewrite_at = models.DateTimeField(
        "Дата последнего рерайта",
        blank=True,
        null=True,
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

        return self.posts.order_by("storypost__position", "storypost__id")

    # --- Обновление статуса и содержания ----------------------------------

    def mark_rewriting(self) -> None:
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
    ) -> None:
        self.title = title
        self.summary = summary
        self.body = body
        self.hashtags = hashtags
        self.sources = sources
        self.status = self.Status.READY
        self.last_rewrite_payload = payload
        self.last_rewrite_at = timezone.now()
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


class StoryPost(models.Model):
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

    class Meta:
        verbose_name = "Задача рерайта"
        verbose_name_plural = "Задачи рерайта"
        ordering = ("-created_at",)

    def mark_running(self) -> None:
        self.status = self.Status.RUNNING
        self.attempts += 1
        self.started_at = timezone.now()
        self.save(update_fields=["status", "attempts", "started_at", "updated_at"])

    def mark_success(self, *, result: dict, response_id: str | None = None) -> None:
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
        self.status = self.Status.FAILED
        self.error_message = error
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "error_message", "finished_at", "updated_at"])


@dataclass(slots=True)
class RewriteResult:
    """Типизированный результат рерайта."""

    title: str
    summary: str
    content: str
    hashtags: list[str]
    sources: list[str]

    @classmethod
    def from_dict(cls, data: dict) -> "RewriteResult":
        title = str(data.get("title", "")).strip()
        summary = str(data.get("summary", "")).strip()
        content = str(data.get("content", "")).strip()
        hashtags = cls._normalize_list(data.get("hashtags", []))
        sources = cls._normalize_list(data.get("sources", []))
        if not content:
            raise ValueError("Ответ модели не содержит текста контента")
        return cls(title=title, summary=summary, content=content, hashtags=hashtags, sources=sources)

    @staticmethod
    def _normalize_list(value: object) -> list[str]:
        if not value:
            return []
        if isinstance(value, (list, tuple, set)):
            return [str(item).strip() for item in value if str(item).strip()]
        return [str(value).strip()]


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
        self.status = self.Status.PUBLISHING
        self.attempts += 1
        self.save(update_fields=["status", "attempts", "updated_at"])

    def mark_published(self, *, message_ids: list[int], published_at, raw: dict | None = None) -> None:
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
        self.status = self.Status.FAILED
        self.error_message = error
        self.save(update_fields=["status", "error_message", "updated_at"])
