"""Worker queue helpers: enqueue tasks, run workers, and handle errors."""

from __future__ import annotations

import logging
import os
import socket
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from django.utils import timezone

from core.models import WorkerTask, queue_settings

logger = logging.getLogger(__name__)


class TaskExecutionError(RuntimeError):
    """Structured error signalling how the worker should react."""

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
    """Callable signature that processes a task."""

    def __call__(self, task: WorkerTask) -> dict[str, Any] | None:
        """Process task in place and optionally return result payload."""
        ...


@dataclass(slots=True)
class WorkerRunner:
    """Simple loop-based runner for worker queues."""

    queue: str
    handler: TaskHandler
    worker_id: str | None = None
    batch_size: int = 1
    idle_sleep: float = 1.0

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if self.idle_sleep < 0:
            raise ValueError("idle_sleep must be >= 0")
        if not self.worker_id:
            self.worker_id = make_worker_id(self.queue)

    # --- public API ---------------------------------------------------------

    def run_once(self) -> int:
        """Fetch and process a batch of tasks once."""

        reserved = WorkerTask.reserve(
            queue=self.queue,
            worker_id=self.worker_id,
            limit=self.batch_size,
        )
        for task in reserved:
            self._process_task(task)
        return len(reserved)

    def run_forever(self) -> None:  # pragma: no cover - requires long-running loop
        """Continuously poll the queue until interrupted."""

        logger.info("Worker %s started for queue '%s'", self.worker_id, self.queue)
        try:
            while True:
                processed = self.run_once()
                if processed == 0 and self.idle_sleep:
                    time.sleep(self.idle_sleep)
        except KeyboardInterrupt:  # pragma: no cover - manual interruption
            logger.info("Worker %s interrupted", self.worker_id)

    # --- internals ----------------------------------------------------------

    def _process_task(self, task: WorkerTask) -> None:
        start = timezone.now()
        try:
            result = self.handler(task)
        except TaskExecutionError as exc:
            self._handle_task_error(task, exc)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception(
                "Unexpected error in worker %s for task %s",
                self.worker_id,
                task.pk,
                exc_info=exc,
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
                "Worker %s processed task %s successfully in %.2fs",
                self.worker_id,
                task.pk,
                (timezone.now() - start).total_seconds(),
            )

    def _handle_task_error(self, task: WorkerTask, exc: TaskExecutionError) -> None:
        logger.warning(
            "Worker %s raised %s for task %s: %s",
            self.worker_id,
            exc.code,
            task.pk,
            exc,
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
                "Task %s requeued (attempt %s/%s) available at %s",
                task.pk,
                task.attempts,
                task.max_attempts,
                task.available_at,
            )
            return
        task.mark_failed(
            error_code=error_code,
            error_message=error_message,
            error_payload=error_payload,
        )
        logger.error(
            "Task %s failed permanently after %s attempts", task.pk, task.attempts
        )


@dataclass(slots=True)
class WorkerRegistry:
    """Keeps a mapping of queue names to handlers."""

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
    """Register handler in module-level registry."""

    registry.register(queue, handler)


def get_handler(queue: str) -> TaskHandler:
    """Return handler from registry or raise LookupError."""

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
    """Create a queued task with defaults derived from queue settings."""

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
    task = WorkerTask.objects.create(
        queue=queue_name,
        payload=payload or {},
        priority=priority,
        available_at=scheduled_for or timezone.now(),
        max_attempts=attempts_limit,
        base_retry_delay=base_delay,
        max_retry_delay=max_delay,
    )
    return task


def make_worker_id(queue: str) -> str:
    """Generate deterministic-ish worker id using hostname and pid."""

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
    """Convenience factory to build runner using registry when handler not provided."""

    if handler is None:
        handler = get_handler(queue)
    return WorkerRunner(
        queue=queue,
        handler=handler,
        worker_id=worker_id,
        batch_size=batch_size,
        idle_sleep=idle_sleep,
    )
