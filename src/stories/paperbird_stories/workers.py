"""Handlers for background tasks in the stories app."""

from __future__ import annotations

from typing import Any

from core.logging import event_logger, logging_context
from core.models import WorkerTask
from core.services.worker import TaskExecutionError, register_handler
from stories.paperbird_stories.models import Publication
from stories.paperbird_stories.services import (
    PublicationFailed,
    default_publisher_for_story,
)


logger = event_logger("stories.publish_worker")


def publish_story_task(task: WorkerTask) -> dict[str, Any]:
    """Handle queued publication by sending it to Telegram."""

    payload = task.payload or {}
    publication_id = payload.get("publication_id")
    if not publication_id:
        raise TaskExecutionError(
            "Payload must contain publication_id",
            code="INVALID_PAYLOAD",
            retry=False,
        )

    try:
        publication = (
            Publication.objects.select_related("story", "story__project__owner")
            .get(pk=publication_id)
        )
    except Publication.DoesNotExist as exc:  # pragma: no cover - defensive branch
        raise TaskExecutionError(
            "Запись публикации не найдена",
            code="NOT_FOUND",
            retry=False,
        ) from exc

    with logging_context(
        project_id=publication.story.project_id,
        story_id=publication.story_id,
    ):
        logger.info(
            "publish_worker_received",
            task_id=task.pk,
            publication_id=publication.pk,
            status=publication.status,
        )

        if publication.status == Publication.Status.PUBLISHED:
            return {"status": publication.status}
        if publication.status == Publication.Status.FAILED:
            raise TaskExecutionError(
                "Публикация завершена с ошибкой и требует ручной проверки",
                code="ALREADY_FAILED",
                retry=False,
            )

        publisher = default_publisher_for_story(publication.story)
        try:
            publisher.deliver(publication)
        except PublicationFailed as exc:
            logger.error(
                "publish_worker_failed",
                task_id=task.pk,
                publication_id=publication.pk,
                error=str(exc),
            )
            raise TaskExecutionError(
                str(exc),
                code="PUBLISH_ERROR",
            ) from exc

        publication.refresh_from_db()
        logger.info(
            "publish_worker_succeeded",
            task_id=task.pk,
            publication_id=publication.pk,
            status=publication.status,
        )
        return {"status": publication.status}


_is_registered = False


def register_publish_worker() -> None:
    """Ensure the publish queue has a handler registered."""

    global _is_registered
    if _is_registered:
        return
    register_handler(WorkerTask.Queue.PUBLISH, publish_story_task)
    _is_registered = True
