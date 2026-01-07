"""Models for shared media assets."""

from __future__ import annotations

from django.conf import settings
from django.db import models


class MediaAsset(models.Model):
    """Изображение, доступное в медиабиблиотеке проекта."""

    class SourceKind(models.TextChoices):
        GENERATED = "generated", "Сгенерировано"
        UPLOAD = "upload", "Загружено"
        IMPORTED = "imported", "Из поста"

    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.CASCADE,
        related_name="media_assets",
        verbose_name="Проект",
    )
    story = models.ForeignKey(
        "stories.Story",
        on_delete=models.SET_NULL,
        related_name="media_assets",
        null=True,
        blank=True,
        verbose_name="Сюжет",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="media_assets",
        null=True,
        blank=True,
        verbose_name="Автор",
    )
    image_file = models.FileField(
        "Изображение",
        upload_to="media_library/",
    )
    title = models.CharField("Заголовок", max_length=200, blank=True)
    prompt = models.TextField("Промпт", blank=True)
    source_kind = models.CharField(
        "Источник",
        max_length=20,
        choices=SourceKind.choices,
        default=SourceKind.UPLOAD,
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Медиа библиотеки"
        verbose_name_plural = "Медиа библиотека"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        title = self.title or self.image_file.name
        return f"{self.project.name} — {title}"
