"""Ставит в очередь задачи обслуживания для удаления просроченных постов."""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from projects.models import Project
from projects.services.retention import schedule_retention_cleanup


class Command(BaseCommand):
    help = "Планирует задачи очистки постов по сроку хранения."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--project",
            type=int,
            help="Ограничить расписание одним проектом",
        )
        parser.add_argument(
            "--delay",
            type=int,
            default=0,
            help="Отложить выполнение на указанное количество минут",
        )

    def handle(self, *args, **options):
        project_id = options.get("project")
        delay = options.get("delay", 0)
        scheduled_for = None
        if delay:
            scheduled_for = timezone.now() + timedelta(minutes=delay)

        projects = self._resolve_projects(project_id)
        tasks = []
        for project in projects:
            tasks.extend(
                schedule_retention_cleanup(project=project, scheduled_for=scheduled_for)
            )

        if not tasks:
            self.stdout.write(self.style.WARNING("Нет активных проектов для очистки"))
            return

        for task in tasks:
            when = task.available_at.isoformat()
            self.stdout.write(
                self.style.SUCCESS(
                    f"Очистка запланирована: task#{task.pk} для проекта {task.payload['project_id']} (доступна с {when})"
                )
            )

    def _resolve_projects(self, project_id):
        if project_id is None:
            return Project.objects.filter(is_active=True)
        try:
            return [Project.objects.get(pk=project_id)]
        except Project.DoesNotExist as exc:  # pragma: no cover - defensive branch
            raise CommandError(f"Проект с id={project_id} не найден") from exc
