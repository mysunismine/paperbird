"""Удаление постов, вышедших за срок хранения проектов."""

from __future__ import annotations

from typing import Iterable

from django.core.management.base import BaseCommand, CommandError

from projects.models import Project
from projects.services.retention import purge_expired_posts


class Command(BaseCommand):
    help = "Удаляет посты, старше срока хранения, для указанных проектов."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--project",
            type=int,
            help="Идентификатор проекта для выборочной очистки",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Подсчитать количество кандидатов без удаления",
        )

    def handle(self, *args, **options):
        project_id = options.get("project")
        dry_run = options.get("dry_run", False)

        projects = self._resolve_projects(project_id)
        total_removed = 0

        for project in projects:
            removed = purge_expired_posts(project=project, dry_run=dry_run)
            total_removed += removed
            if dry_run:
                self.stdout.write(
                    self.style.NOTICE(
                        f"Проект #{project.pk} «{project.name}»: к удалению {removed} постов"
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Проект #{project.pk} «{project.name}»: удалено {removed} постов"
                    )
                )

        if dry_run:
            self.stdout.write(self.style.NOTICE(f"Всего к удалению: {total_removed}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Всего удалено: {total_removed}"))

    def _resolve_projects(self, project_id: int | None) -> Iterable[Project]:
        if project_id is None:
            return Project.objects.all()
        try:
            project = Project.objects.get(pk=project_id)
        except Project.DoesNotExist as exc:  # pragma: no cover - контрольная ветка
            raise CommandError(f"Проект с id={project_id} не найден") from exc
        return [project]
