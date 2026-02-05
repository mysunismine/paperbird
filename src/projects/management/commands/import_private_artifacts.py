"""Import project artifacts from a private checkout directory."""

from __future__ import annotations

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from projects.services.private_artifacts import (
    import_project_artifacts_from_private_dir,
    private_user_artifacts_dir,
)
from projects.services.project_import import ProjectImportError

User = get_user_model()


class Command(BaseCommand):
    help = "Импортирует проекты из приватной директории артефактов."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Имя пользователя (django username)")
        parser.add_argument(
            "--project-dir",
            dest="project_dir",
            default="",
            help=(
                "Имя папки проекта внутри артефактов пользователя или абсолютный путь. "
                "Если не указан — импортируются все подпапки."
            ),
        )
        parser.add_argument(
            "--file",
            dest="source_file",
            default="",
            help="Имя файла экспорта в папке проекта (по умолчанию авто-поиск project-export.*).",
        )

    def handle(self, *args, **options):
        username = options["username"]
        project_dir = (options.get("project_dir") or "").strip()
        source_file = (options.get("source_file") or "").strip() or None
        user = self._get_user(username=username)
        dirs = self._resolve_source_dirs(username=user.username, project_dir=project_dir)
        imported = 0

        for source_dir in dirs:
            try:
                result = import_project_artifacts_from_private_dir(
                    owner=user,
                    source_dir=source_dir,
                    source_file=source_file,
                )
            except ProjectImportError as exc:
                raise CommandError(f"Не удалось импортировать {source_dir}: {exc}") from exc

            imported += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"Импортирован проект «{result.result.project.name}» из {result.source_file}"
                )
            )
            self.stdout.write(
                f"  - источников: {result.result.sources_created}, "
                f"пресетов: {result.result.presets_imported}"
            )

        self.stdout.write(self.style.SUCCESS(f"Импорт завершён. Проектов: {imported}."))

    @staticmethod
    def _get_user(*, username: str):
        try:
            return User.objects.get(username=username)
        except User.DoesNotExist as exc:
            raise CommandError(f"Пользователь {username!r} не найден.") from exc

    @staticmethod
    def _resolve_source_dirs(*, username: str, project_dir: str) -> list[Path]:
        if project_dir:
            candidate = Path(project_dir).expanduser()
            if not candidate.is_absolute():
                candidate = private_user_artifacts_dir(username=username) / candidate
            resolved = candidate.resolve()
            if not resolved.exists() or not resolved.is_dir():
                raise CommandError(f"Папка проекта не найдена: {resolved}")
            return [resolved]

        root = private_user_artifacts_dir(username=username).resolve()
        if not root.exists() or not root.is_dir():
            raise CommandError(f"Папка артефактов пользователя не найдена: {root}")
        dirs = sorted(path for path in root.iterdir() if path.is_dir())
        if not dirs:
            raise CommandError(f"В папке {root} нет подпапок проектов для импорта.")
        return dirs
