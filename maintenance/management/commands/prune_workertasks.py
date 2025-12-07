"""Удаление старых записей задач воркера."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.core.management.base import BaseCommand, CommandParser
from django.utils import timezone

from core.models import WorkerTask


class Command(BaseCommand):
    help = (
        "Удаляет завершённые задачи воркера старше указанного срока. "
        "По умолчанию чистит succeeded/cancelled старше 30 дней, "
        "сохраняя последние неудачные задачи для аудита."
    )

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--days",
            type=int,
            default=30,
            help="Сколько дней хранить завершённые задачи (по умолчанию 30).",
        )
        parser.add_argument(
            "--keep-failed",
            type=int,
            default=200,
            help="Сколько последних неудачных задач сохранить (по умолчанию 200).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать, сколько будет удалено, но не удалять.",
        )

    def handle(self, *args: Any, **options: Any) -> str:
        days: int = options["days"]
        keep_failed: int = options["keep_failed"]
        dry_run: bool = options["dry_run"]

        cutoff = timezone.now() - timedelta(days=days)

        # Готовим выборку завершённых задач, не трогаем running/queued.
        completed_qs = WorkerTask.objects.filter(
            status__in=[WorkerTask.Status.SUCCEEDED, WorkerTask.Status.CANCELLED],
            updated_at__lt=cutoff,
        )
        completed_count = completed_qs.count()

        # Определяем неудачные задачи старше cutoff, но сохраняем последние keep_failed.
        failed_qs = WorkerTask.objects.filter(
            status=WorkerTask.Status.FAILED,
            updated_at__lt=cutoff,
        ).order_by("-id")
        failed_to_keep_ids = list(failed_qs.values_list("id", flat=True)[:keep_failed])
        failed_to_delete_qs = failed_qs.exclude(id__in=failed_to_keep_ids)
        failed_delete_count = failed_to_delete_qs.count()

        total_delete = completed_count + failed_delete_count

        if dry_run:
            self.stdout.write(
                self.style.NOTICE(
                    f"[dry-run] Будет удалено: {total_delete} задач "
                    f"(завершено: {completed_count}, failed: {failed_delete_count})."
                )
            )
            return "ok"

        # Удаляем пачками.
        deleted_completed = completed_qs.delete()[0]
        deleted_failed = failed_to_delete_qs.delete()[0]

        self.stdout.write(
            self.style.SUCCESS(
                f"Удалено {deleted_completed + deleted_failed} задач "
                f"(завершено: {deleted_completed}, failed: {deleted_failed})."
            )
        )
        return "ok"
