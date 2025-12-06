"""Исключения для сервисов сюжетов."""

from __future__ import annotations


class StoryCreationError(RuntimeError):
    """Ошибка при создании сюжета."""


class RewriteFailed(RuntimeError):
    """Ошибка выполнения рерайта."""


class ImageGenerationFailed(RuntimeError):
    """Ошибка генерации изображения."""


class PublicationFailed(RuntimeError):
    """Ошибка публикации сюжета."""
