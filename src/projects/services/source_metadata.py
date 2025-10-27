"""Helpers for refreshing Telegram source metadata."""

from __future__ import annotations

from core.models import WorkerTask
from core.services.worker import enqueue_task
from projects.models import Source


def enqueue_source_refresh(source: Source, *, scheduled_for=None) -> WorkerTask:
    """Schedule background task to refresh metadata for the given source."""

    return enqueue_task(
        WorkerTask.Queue.SOURCE,
        payload={
            "source_id": source.pk,
            "project_id": source.project_id,
        },
        scheduled_for=scheduled_for,
    )


__all__ = ["enqueue_source_refresh"]
