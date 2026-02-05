"""Export project artifacts to a private checkout directory."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from projects.models import Project
from projects.services.private_artifacts import export_project_artifacts_to_private_dir

User = get_user_model()


class Command(BaseCommand):
    help = "Экспортирует проекты, промпты и пресеты в приватную директорию артефактов."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Имя пользователя (django username)")
        parser.add_argument(
            "--project",
            dest="project_id",
            type=int,
            help="ID проекта. Если не указан — экспортируются все проекты пользователя.",
        )
        parser.add_argument(
            "--format",
            dest="format",
            choices=("json", "yaml"),
            default="json",
            help="Формат файла project-export (json по умолчанию).",
        )

    def handle(self, *args, **options):
        username = options["username"]
        project_id = options.get("project_id")
        export_format = options["format"]
        user = self._get_user(username=username)
        projects = self._get_projects(user=user, project_id=project_id)
        exported_count = 0

        for project in projects:
            result = export_project_artifacts_to_private_dir(
                project=project,
                username=user.username,
                fmt=export_format,
            )
            exported_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"Проект {project.pk} экспортирован в {result.project_dir}"
                )
            )
            self.stdout.write(f"  - {result.project_export_file}")
            self.stdout.write(f"  - {result.prompt_export_file}")
            if result.preset_files:
                self.stdout.write(f"  - пресетов: {len(result.preset_files)}")

        self.stdout.write(self.style.SUCCESS(f"Экспорт завершён. Проектов: {exported_count}."))

    @staticmethod
    def _get_user(*, username: str):
        try:
            return User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise CommandError(f"Пользователь {username!r} не найден.") from exc

    @staticmethod
    def _get_projects(*, user, project_id: int | None):
        projects = Project.objects.filter(owner=user).order_by("id")
        if project_id:
            projects = projects.filter(pk=project_id)
        if not projects.exists():
            if project_id:
                raise CommandError(
                    f"Проект с id={project_id} для пользователя {user.username!r} не найден."
                )
            raise CommandError(f"У пользователя {user.username!r} нет проектов для экспорта.")
        return projects
