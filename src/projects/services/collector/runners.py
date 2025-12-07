"""Async and sync runners for the collector."""

from __future__ import annotations

import asyncio
import logging

from asgiref.sync import sync_to_async

from accounts.models import User
from core.constants import DEFAULT_COLLECT_LIMIT
from projects.services.telethon_client import TelethonCredentialsMissingError

from .post_collector import CollectOptions, PostCollector

logger = logging.getLogger(__name__)


async def collect_for_user(
    user: User,
    *,
    project_id: int | None = None,
    limit: int = DEFAULT_COLLECT_LIMIT,
) -> None:
    """Асинхронный запуск сборщика для пользователя."""

    if not user.has_telethon_credentials:
        raise RuntimeError("У пользователя отсутствуют ключи Telethon")

    options = CollectOptions(limit=limit)
    collector = PostCollector(user=user, options=options)
    projects_qs = user.projects.filter(is_active=True)
    if project_id:
        projects_qs = projects_qs.filter(id=project_id)
    projects = await sync_to_async(list)(projects_qs.order_by("name"))
    for project in projects:
        await collector.collect_for_project(project)


async def collect_for_all_users(
    *,
    project_id: int | None = None,
    limit: int = DEFAULT_COLLECT_LIMIT,
    continuous: bool = False,
    interval: int = 60,
) -> None:
    """Запускает сборщик для всех пользователей с заполненными Telethon-данными."""

    delay = max(interval, 5)

    async def _eligible_users() -> list[User]:
        qs = (
            User.objects.filter(is_active=True, telethon_api_id__isnull=False)
            .exclude(telethon_api_hash="")
            .exclude(telethon_session="")
            .order_by("id")
        )
        return await sync_to_async(list, thread_sensitive=True)(qs)

    async def _run_once() -> None:
        from projects.services import collector as collector_pkg

        users = await _eligible_users()
        if not users:
            logger.info("collect_for_all_users_no_credentials")
            return
        for user in users:
            if not user.has_telethon_credentials:
                continue
            try:
                await collector_pkg.collect_for_user(
                    user,
                    project_id=project_id,
                    limit=limit,
                )
            except TelethonCredentialsMissingError as exc:
                logger.warning(
                    "collect_for_all_users_skipped",
                    extra={"user_id": user.pk, "reason": str(exc)},
                )
            except Exception as exc:  # pragma: no cover - защитный слой вокруг сети
                logger.exception(
                    "collect_for_all_users_error",
                    extra={"user_id": user.pk, "error": str(exc)},
                )

    while True:
        await _run_once()
        if not continuous:
            break
        await asyncio.sleep(delay)


async def collect_for_user_live(
    user: User,
    *,
    project_id: int | None = None,
    limit: int = DEFAULT_COLLECT_LIMIT,
    interval: int = 60,
) -> None:
    """Постоянный сбор постов с указанным интервалом опроса."""

    delay = max(interval, 5)
    while True:
        await collect_for_user(user, project_id=project_id, limit=limit)
        await asyncio.sleep(delay)


def collect_for_user_sync(
    user: User,
    *,
    project_id: int | None = None,
    limit: int = DEFAULT_COLLECT_LIMIT,
    continuous: bool = False,
    interval: int = 60,
) -> None:
    """Синхронный адаптер для использования в manage-команде."""

    async def runner() -> None:
        if continuous:
            await collect_for_user_live(
                user,
                project_id=project_id,
                limit=limit,
                interval=interval,
            )
        else:
            await collect_for_user(user, project_id=project_id, limit=limit)

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        # graceful stop for continuous mode
        pass


def collect_for_all_users_sync(
    *,
    project_id: int | None = None,
    limit: int = DEFAULT_COLLECT_LIMIT,
    continuous: bool = False,
    interval: int = 60,
) -> None:
    """Синхронный адаптер, запускающий сбор для всех пользователей."""

    async def runner() -> None:
        await collect_for_all_users(
            project_id=project_id,
            limit=limit,
            continuous=continuous,
            interval=interval,
        )

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        pass
