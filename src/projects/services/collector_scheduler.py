"""Utilities to schedule collector tasks when sources change."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from core.models import WorkerTask
from core.services.worker import enqueue_task
from projects.models import Project, Source, WebPreset


def ensure_collector_tasks(project: Project, *, delay: int = 0) -> None:
    """Ensures project-level collector tasks are scheduled for active sources."""

    if not project.collector_enabled:
        return

    now = timezone.now()

    def _has_pending(queue: str) -> bool:
        return WorkerTask.objects.filter(
            queue=queue,
            payload__project_id=project.pk,
            status__in=[WorkerTask.Status.QUEUED, WorkerTask.Status.RUNNING],
        ).exists()

    def _schedule(queue: str, interval: int) -> None:
        if _has_pending(queue):
            return
        scheduled_for = now + timedelta(seconds=max(delay, 0))
        enqueue_task(
            queue,
            payload={
                "project_id": project.pk,
                "interval": interval,
            },
            scheduled_for=scheduled_for,
        )

    has_telegram_sources = project.sources.filter(
        is_active=True,
        type=Source.Type.TELEGRAM,
    ).exists()
    if has_telegram_sources and project.owner.has_telethon_credentials:
        _schedule(
            WorkerTask.Queue.COLLECTOR,
            max(project.collector_telegram_interval, 30),
        )

    has_web_sources = project.sources.filter(
        is_active=True,
        type=Source.Type.WEB,
        web_preset__status=WebPreset.Status.ACTIVE,
    ).exists()
    if has_web_sources:
        _schedule(
            WorkerTask.Queue.COLLECTOR_WEB,
            max(project.collector_web_interval, 60),
        )


__all__ = ["ensure_collector_tasks"]
