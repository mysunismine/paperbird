"""Handlers for background maintenance tasks in the projects app."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from core.logging import logging_context
from core.models import WorkerTask
from core.services.worker import TaskExecutionError, enqueue_task, register_handler
from projects.models import Project, Source, SourceSyncLog, WebPreset
from projects.services.collector import collect_for_user
from projects.services.retention import purge_expired_posts
from projects.services.telethon_client import (
    TelethonClientFactory,
    TelethonCredentialsMissingError,
)
from projects.services.web_collector import WebCollector
from projects.services.web_preset_registry import PresetValidationError


_is_registered = False


def retention_cleanup_task(task: WorkerTask) -> dict[str, Any]:
    """Remove expired posts for the requested project."""

    payload = task.payload or {}
    project_id = payload.get("project_id")
    if not project_id:
        raise TaskExecutionError(
            "Payload must contain project_id",
            code="INVALID_PAYLOAD",
            retry=False,
        )

    try:
        project = Project.objects.get(pk=project_id)
    except Project.DoesNotExist as exc:
        raise TaskExecutionError(
            "Проект не найден",
            code="PROJECT_MISSING",
            retry=False,
        ) from exc

    if not project.is_active:
        return {"status": "skipped", "reason": "inactive"}

    with logging_context(project_id=project.pk):
        with transaction.atomic():
            removed = purge_expired_posts(project=project)
    return {"status": "ok", "removed": removed}


def register_project_workers() -> None:
    """Ensure maintenance queue handlers are registered."""

    global _is_registered
    if _is_registered:
        return
    register_handler(WorkerTask.Queue.COLLECTOR, collect_project_posts_task)
    register_handler(WorkerTask.Queue.COLLECTOR_WEB, collect_project_web_sources_task)
    register_handler(WorkerTask.Queue.MAINTENANCE, retention_cleanup_task)
    register_handler(WorkerTask.Queue.SOURCE, refresh_source_metadata_task)
    _is_registered = True


def refresh_source_metadata_task(task: WorkerTask) -> dict[str, Any]:
    """Fetch metadata for a source via Telethon and store it."""

    payload = task.payload or {}
    source_id = payload.get("source_id")
    if not source_id:
        raise TaskExecutionError("Payload must contain source_id", code="INVALID_PAYLOAD", retry=False)

    try:
        source = Source.objects.select_related("project__owner").get(pk=source_id)
    except Source.DoesNotExist as exc:  # pragma: no cover - defensive branch
        raise TaskExecutionError("Источник не найден", code="NOT_FOUND", retry=False) from exc

    project = source.project
    owner = project.owner
    if not owner.has_telethon_credentials:
        return {"status": "skipped", "reason": "no_credentials"}

    target = source.username or source.telegram_id or source.invite_link
    if not target:
        return {"status": "skipped", "reason": "no_identifier"}

    async def runner():
        factory = TelethonClientFactory(user=owner)
        async with factory.connect() as client:
            return await client.get_entity(target)

    try:
        entity = asyncio.run(runner())
    except TelethonCredentialsMissingError as exc:
        raise TaskExecutionError(str(exc), code="AUTH_ERROR", retry=False) from exc
    except ValueError as exc:
        raise TaskExecutionError(str(exc), code="LOOKUP_ERROR") from exc
    except Exception as exc:  # pragma: no cover - defensive logging
        raise TaskExecutionError(str(exc), code="SOURCE_REFRESH_ERROR") from exc

    title = getattr(entity, "title", None)
    if not title:
        first_name = getattr(entity, "first_name", "")
        last_name = getattr(entity, "last_name", "")
        title = " ".join(filter(None, [first_name, last_name]))
    username = getattr(entity, "username", None) or source.username
    telegram_id = getattr(entity, "id", None) or source.telegram_id

    updates: dict[str, Any] = {}
    if title and source.title != title:
        updates["title"] = title
    if username and source.username != username:
        updates["username"] = username.lower()
    if telegram_id and source.telegram_id != telegram_id:
        updates["telegram_id"] = telegram_id
    if updates:
        Source.objects.filter(pk=source.pk).update(**updates)

    return {"status": "ok", "updated": bool(updates)}


def collect_project_posts_task(task: WorkerTask) -> dict[str, Any]:
    """Launch Telegram collector for a specific project."""

    payload = task.payload or {}
    project_id = payload.get("project_id")
    interval = max(int(payload.get("interval", 300)), 30)
    if not project_id:
        raise TaskExecutionError(
            "Payload must contain project_id",
            code="INVALID_PAYLOAD",
            retry=False,
        )

    try:
        project = Project.objects.select_related("owner").get(pk=project_id)
    except Project.DoesNotExist as exc:
        raise TaskExecutionError(
            "Проект не найден",
            code="PROJECT_MISSING",
            retry=False,
        ) from exc

    owner = project.owner
    if not project.is_active:
        return {"status": "skipped", "reason": "inactive"}
    if not project.collector_enabled:
        return {"status": "skipped", "reason": "disabled"}
    if not owner.has_telethon_credentials:
        raise TaskExecutionError(
            "У пользователя не настроены Telethon-ключи",
            code="NO_CREDENTIALS",
            retry=False,
        )

    async def runner() -> None:
        await collect_for_user(owner, project_id=project.pk)

    with logging_context(project_id=project.pk, user_id=owner.pk):
        try:
            asyncio.run(runner())
        except TelethonCredentialsMissingError as exc:
            raise TaskExecutionError(str(exc), code="AUTH_ERROR", retry=False) from exc
        except Exception as exc:  # pragma: no cover - защитный слой
            raise TaskExecutionError(str(exc), code="COLLECT_ERROR") from exc

    now = timezone.now()
    Project.objects.filter(pk=project.pk).update(
        collector_last_run=now,
        updated_at=now,
    )

    if project.collector_enabled:
        scheduled_for = now + timedelta(seconds=interval)
        enqueue_task(
            WorkerTask.Queue.COLLECTOR,
            payload={"project_id": project.pk, "interval": interval},
            scheduled_for=scheduled_for,
        )

    return {"status": "ok", "next_run_in": interval}


def collect_project_web_sources_task(task: WorkerTask) -> dict[str, Any]:
    """Launch universal web collector for web sources in a project."""

    payload = task.payload or {}
    project_id = payload.get("project_id")
    interval = max(int(payload.get("interval", 300)), 60)
    source_id = payload.get("source_id")
    if not project_id:
        raise TaskExecutionError(
            "Payload must contain project_id",
            code="INVALID_PAYLOAD",
            retry=False,
        )
    try:
        project = Project.objects.prefetch_related("sources__web_preset").get(pk=project_id)
    except Project.DoesNotExist as exc:
        raise TaskExecutionError(
            "Проект не найден",
            code="PROJECT_MISSING",
            retry=False,
        ) from exc
    if not project.is_active:
        return {"status": "skipped", "reason": "inactive"}
    if not project.collector_enabled and not source_id:
        return {"status": "skipped", "reason": "disabled"}
    sources_qs = project.sources.filter(
        is_active=True,
        type=Source.Type.WEB,
        web_preset__status=WebPreset.Status.ACTIVE,
    ).select_related("web_preset")
    if source_id:
        sources_qs = sources_qs.filter(pk=source_id)
    sources = list(sources_qs)
    if not sources:
        return {"status": "skipped", "reason": "no_sources"}

    collector = WebCollector()
    summary = {"created": 0, "updated": 0, "skipped": 0}
    for source in sources:
        log = SourceSyncLog.objects.create(source=source)
        with logging_context(project_id=project.pk, source_id=source.pk):
            try:
                stats = collector.collect(source)
            except PresetValidationError as exc:
                log.finish(status="failed", error=str(exc))
                WebPreset.objects.filter(pk=source.web_preset_id).update(
                    status=WebPreset.Status.BROKEN,
                    updated_at=timezone.now(),
                )
                Source.objects.filter(pk=source.pk).update(
                    web_last_status="broken",
                    web_last_synced_at=timezone.now(),
                    updated_at=timezone.now(),
                )
                continue
            except Exception as exc:  # pragma: no cover - defensive logging
                log.finish(status="failed", error=str(exc))
                Source.objects.filter(pk=source.pk).update(
                    web_last_status="failed",
                    web_last_synced_at=timezone.now(),
                    updated_at=timezone.now(),
                )
                continue
            fetched = stats.get("created", 0) + stats.get("updated", 0)
            log.finish(status="ok", fetched=fetched, skipped=stats.get("skipped", 0))
            summary["created"] += stats.get("created", 0)
            summary["updated"] += stats.get("updated", 0)
            summary["skipped"] += stats.get("skipped", 0)

    should_schedule = project.collector_enabled and not source_id
    if should_schedule:
        scheduled_for = timezone.now() + timedelta(seconds=interval)
        enqueue_task(
            WorkerTask.Queue.COLLECTOR_WEB,
            payload={"project_id": project.pk, "interval": interval},
            scheduled_for=scheduled_for,
        )
    return {"status": "ok", **summary}
