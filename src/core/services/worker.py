"""Утилиты очереди задач: постановка задач, запуск воркеров и обработка ошибок."""

from __future__ import annotations

import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from django.utils import timezone

from core.logging import (
    current_correlation_id,
    event_logger,
    generate_correlation_id,
    logging_context,
)
from core.models import WorkerTask, queue_settings

logger = event_logger("core.worker")


class TaskExecutionError(RuntimeError):
    """Структурированная ошибка, сигнализирующая, как должен реагировать воркер."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "TASK_ERROR",
        retry: bool = True,
        retry_in: int | float | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retry = retry
        self.retry_in = retry_in
        self.payload = payload or {}


class TaskHandler(Protocol):  # pragma: no cover - protocol definition
    """Сигнатура вызываемого объекта, который обрабатывает задачу."""

    def __call__(self, task: WorkerTask) -> dict[str, Any] | None:
        """Обрабатывает задачу и опционально возвращает результат."""
        ...


@dataclass(slots=True)
class WorkerRunner:
    """Простой исполнитель на основе цикла для очередей воркеров."""

    queue: str
    handler: TaskHandler
    worker_id: str | None = None
    batch_size: int = 1
    idle_sleep: float = 1.0
    stale_lock_timeout: int | float | None = None

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if self.idle_sleep < 0:
            raise ValueError("idle_sleep must be >= 0")
        if self.stale_lock_timeout is not None and self.stale_lock_timeout < 0:
            raise ValueError("stale_lock_timeout must be >= 0")
        if not self.worker_id:
            self.worker_id = make_worker_id(self.queue)

    # --- public API ---------------------------------------------------------

    def run_once(self) -> int:
        """Получает и обрабатывает одну пачку задач."""

        revived = 0
        if self.stale_lock_timeout:
            revived = WorkerTask.revive_stale(
                queue=self.queue,
                max_age_seconds=int(self.stale_lock_timeout),
            )
            if revived:
                logger.warning(
                    "worker_recovered_stale_tasks",
                    queue=self.queue,
                    worker_id=self.worker_id,
                    count=revived,
                    stale_seconds=int(self.stale_lock_timeout),
                )
        reserved = WorkerTask.reserve(
            queue=self.queue,
            worker_id=self.worker_id,
            limit=self.batch_size,
        )
        for task in reserved:
            self._process_task(task)
        processed = len(reserved)
        if revived or processed:
            logger.info(
                "worker_batch_processed",
                queue=self.queue,
                worker_id=self.worker_id,
                processed=processed,
                revived=revived,
            )
        return processed

    def run_forever(self) -> None:  # pragma: no cover - requires long-running loop
        """Непрерывно опрашивает очередь до прерывания."""

        logger.info(
            "worker_started",
            queue=self.queue,
            worker_id=self.worker_id,
        )
        try:
            while True:
                processed = self.run_once()
                if processed == 0 and self.idle_sleep:
                    time.sleep(self.idle_sleep)
        except KeyboardInterrupt:  # pragma: no cover - manual interruption
            logger.warning(
                "worker_interrupted",
                queue=self.queue,
                worker_id=self.worker_id,
            )

    # --- internals ----------------------------------------------------------

    def _process_task(self, task: WorkerTask) -> None:
        payload = task.payload or {}
        correlation_id = payload.get("correlation_id") or generate_correlation_id()
        project_id = payload.get("project_id")
        story_id = payload.get("story_id")
        start = timezone.now()

        with logging_context(
            correlation_id=correlation_id,
            project_id=project_id,
            story_id=story_id,
        ):
            try:
                result = self.handler(task)
            except TaskExecutionError as exc:
                self._handle_task_error(task, exc)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.error(
                    "worker_unexpected_error",
                    worker_id=self.worker_id,
                    task_id=task.pk,
                    queue=self.queue,
                    error=str(exc),
                    exception=exc.__class__.__name__,
                )
                self._finalize_task_failure(
                    task,
                    error_code="UNEXPECTED_ERROR",
                    error_message=str(exc),
                    error_payload={},
                    force_fail=not task.can_retry(),
                )
            else:
                if result is None:
                    result = {}
                task.mark_succeeded(result=result)
                logger.info(
                    "worker_task_succeeded",
                    worker_id=self.worker_id,
                    task_id=task.pk,
                    queue=self.queue,
                    duration=(timezone.now() - start).total_seconds(),
                )

    def _handle_task_error(self, task: WorkerTask, exc: TaskExecutionError) -> None:
        logger.warning(
            "worker_task_retry",
            worker_id=self.worker_id,
            task_id=task.pk,
            queue=self.queue,
            error_code=exc.code,
            error_message=str(exc),
            will_retry=exc.retry,
            retry_in=exc.retry_in,
        )
        self._finalize_task_failure(
            task,
            error_code=exc.code,
            error_message=str(exc),
            error_payload=exc.payload,
            retry_in=exc.retry_in,
            force_fail=not exc.retry,
        )

    def _finalize_task_failure(
        self,
        task: WorkerTask,
        *,
        error_code: str,
        error_message: str,
        error_payload: dict[str, Any] | None,
        retry_in: int | float | None = None,
        force_fail: bool = False,
    ) -> None:
        if not force_fail and task.can_retry():
            task.mark_for_retry(
                error_code=error_code,
                error_message=error_message,
                error_payload=error_payload,
                retry_in=retry_in,
            )
            logger.info(
                "worker_task_requeued",
                worker_id=self.worker_id,
                task_id=task.pk,
                queue=self.queue,
                attempts=task.attempts,
                max_attempts=task.max_attempts,
                next_run_at=task.available_at.isoformat() if task.available_at else None,
            )
            return
        task.mark_failed(
            error_code=error_code,
            error_message=error_message,
            error_payload=error_payload,
        )
        logger.error(
            "worker_task_failed",
            worker_id=self.worker_id,
            task_id=task.pk,
            queue=self.queue,
            attempts=task.attempts,
            error_code=error_code,
            error_message=error_message,
        )


@dataclass(slots=True)
class WorkerRegistry:
    """Хранит сопоставление имен очередей с обработчиками."""

    _handlers: dict[str, TaskHandler]

    def __init__(self) -> None:
        self._handlers = {}

    def register(self, queue: str, handler: TaskHandler) -> None:
        self._handlers[queue] = handler

    def get(self, queue: str) -> TaskHandler:
        try:
            return self._handlers[queue]
        except KeyError as exc:  # pragma: no cover - defensive branch
            raise LookupError(f"Handler for queue '{queue}' is not registered") from exc

    def unregister(self, queue: str) -> None:  # pragma: no cover - not used in tests yet
        self._handlers.pop(queue, None)


registry = WorkerRegistry()


def register_handler(queue: str, handler: TaskHandler) -> None:
    """Регистрирует обработчик в реестре на уровне модуля."""

    registry.register(queue, handler)


def get_handler(queue: str) -> TaskHandler:
    """Возвращает обработчик из реестра или вызывает LookupError."""

    return registry.get(queue)


def enqueue_task(
    queue: str,
    *,
    payload: dict[str, Any] | None = None,
    priority: int = 0,
    scheduled_for: datetime | None = None,
    max_attempts: int | None = None,
    base_retry_delay: int | None = None,
    max_retry_delay: int | None = None,
) -> WorkerTask:
    """Создает задачу в очереди с настройками по умолчанию, взятыми из настроек очереди."""

    queue_name = queue.lower()
    settings = queue_settings(queue_name)
    attempts_limit = max_attempts or settings.max_attempts
    if attempts_limit < 1:
        raise ValueError("max_attempts must be >= 1")
    base_delay = base_retry_delay or settings.base_retry_delay
    if base_delay < 0:
        raise ValueError("base_retry_delay must be >= 0")
    max_delay = max_retry_delay or settings.max_retry_delay
    if max_delay < 0:
        raise ValueError("max_retry_delay must be >= 0")
    payload_data = dict(payload or {})
    correlation_id = payload_data.get("correlation_id") or current_correlation_id()
    if correlation_id:
        payload_data["correlation_id"] = correlation_id
    task = WorkerTask.objects.create(
        queue=queue_name,
        payload=payload_data,
        priority=priority,
        available_at=scheduled_for or timezone.now(),
        max_attempts=attempts_limit,
        base_retry_delay=base_delay,
        max_retry_delay=max_delay,
    )
    return task


def make_worker_id(queue: str) -> str:
    """Генерирует (относительно) детерминированный ID воркера, используя имя хоста и PID."""

    hostname = socket.gethostname().split(".")[0]
    pid = os.getpid()
    return f"{queue}-{hostname}-{pid}"


def make_runner(
    queue: str,
    handler: TaskHandler | None = None,
    *,
    worker_id: str | None = None,
    batch_size: int = 1,
    idle_sleep: float = 1.0,
) -> WorkerRunner:
    """Фабрика для сборки исполнителя, использующая реестр, если обработчик не предоставлен."""

    if handler is None:
        handler = get_handler(queue)
    settings = queue_settings(queue)
    return WorkerRunner(
        queue=queue,
        handler=handler,
        worker_id=worker_id,
        batch_size=batch_size,
        idle_sleep=idle_sleep,
        stale_lock_timeout=settings.stale_lock_timeout,
    )
