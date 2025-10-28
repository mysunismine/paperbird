"""Очистка постов по сроку хранения проекта."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from core.models import WorkerTask
from core.services.worker import enqueue_task
from projects.models import Post, Project


def _expired_posts_queryset(project: Project, *, cutoff):
    queryset = Post.objects.filter(project=project, posted_at__lt=cutoff)
    return queryset.filter(stories__isnull=True)


def purge_expired_posts(
    *,
    project: Project,
    now=None,
    dry_run: bool = False,
) -> int:
    """Удаляет посты проекта, вышедшие за срок хранения."""

    if project.retention_days < 1:
        return 0
    reference_time = now or timezone.now()
    cutoff = reference_time - timedelta(days=project.retention_days)
    queryset = _expired_posts_queryset(project, cutoff=cutoff)
    if dry_run:
        return queryset.count()
    deleted, _ = queryset.delete()
    return deleted


def schedule_retention_cleanup(
    *,
    project: Project | None = None,
    scheduled_for=None,
) -> list[WorkerTask]:
    """Планирует задачи очистки для выбранных проектов."""

    projects: list[Project]
    if project is not None:
        projects = [project]
    else:
        projects = list(Project.objects.filter(is_active=True))

    tasks: list[WorkerTask] = []
    for item in projects:
        task = enqueue_task(
            WorkerTask.Queue.MAINTENANCE,
            payload={"project_id": item.pk},
            scheduled_for=scheduled_for,
        )
        tasks.append(task)
    return tasks


__all__ = ["purge_expired_posts", "schedule_retention_cleanup"]
