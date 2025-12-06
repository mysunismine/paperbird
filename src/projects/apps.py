"""Конфигурация приложения `projects`."""

from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    """Конфигурация приложения `projects`."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "projects"

    def ready(self) -> None:
        """Регистрирует обработчики фоновых задач при запуске приложения."""
        from .workers import register_project_workers

        register_project_workers()
