"""Конфигурация приложения stories."""

from django.apps import AppConfig


class PaperbirdStoriesConfig(AppConfig):
    """Конфигурация приложения stories."""
    default_auto_field = "django.db.models.BigAutoField"
    name = "stories.paperbird_stories"
    label = "stories"

    def ready(self) -> None:
        """Регистрирует обработчики фоновых задач при запуске приложения."""
        from .workers import register_publish_worker

        register_publish_worker()
