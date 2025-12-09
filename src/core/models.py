"""Основные доменные модели: фоновые задачи и попытки их выполнения."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.contrib.postgres.indexes import GinIndex
from django.db import models, transaction
from django.db.models import F
from django.utils import timezone

from core.constants import (
    COLLECTOR_BASE_RETRY_DELAY,
    COLLECTOR_MAX_ATTEMPTS,
    COLLECTOR_MAX_RETRY_DELAY,
    COLLECTOR_STALE_TIMEOUT,
    COLLECTOR_WEB_STALE_TIMEOUT,
    DEFAULT_QUEUE_BASE_RETRY_DELAY,
    DEFAULT_QUEUE_MAX_ATTEMPTS,
    DEFAULT_QUEUE_MAX_RETRY_DELAY,
    DEFAULT_QUEUE_STALE_TIMEOUT,
    IMAGE_BASE_RETRY_DELAY,
    IMAGE_MAX_ATTEMPTS,
    IMAGE_MAX_RETRY_DELAY,
    MAINTENANCE_BASE_RETRY_DELAY,
    MAINTENANCE_MAX_ATTEMPTS,
    MAINTENANCE_MAX_RETRY_DELAY,
    PUBLISH_BASE_RETRY_DELAY,
    PUBLISH_MAX_ATTEMPTS,
    PUBLISH_MAX_RETRY_DELAY,
    REWRITE_BASE_RETRY_DELAY,
    REWRITE_MAX_ATTEMPTS,
    REWRITE_MAX_RETRY_DELAY,
    SOURCE_BASE_RETRY_DELAY,
    SOURCE_MAX_ATTEMPTS,
    SOURCE_MAX_RETRY_DELAY,
)


class WorkerTask(models.Model):
    """Персистентное представление фоновых задач, обрабатываемых воркерами."""

    class Queue(models.TextChoices):
        COLLECTOR = "collector", "Collector"
        COLLECTOR_WEB = "collector_web", "Collector Web"
        REWRITE = "rewrite", "Rewrite"
        PUBLISH = "publish", "Publish"
        IMAGE = "image", "Image"
        MAINTENANCE = "maintenance", "Maintenance"
        SOURCE = "source", "Source"
        DEFAULT = "default", "Default"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    queue = models.CharField("Очередь", max_length=50)
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
    )
    priority = models.SmallIntegerField(
        "Приоритет",
        default=0,
        help_text="Чем меньше значение, тем выше приоритет обработки.",
    )
    payload = models.JSONField("Данные задачи", default=dict, blank=True)
    result = models.JSONField("Результат", default=dict, blank=True)
    attempts = models.PositiveIntegerField("Выполненные попытки", default=0)
    max_attempts = models.PositiveIntegerField("Максимум попыток", default=3)
    available_at = models.DateTimeField(
        "Доступна после",
        default=timezone.now,
        help_text="Когда задачу можно брать в работу.",
    )
    locked_at = models.DateTimeField("Заблокирована в", blank=True, null=True)
    locked_by = models.CharField("Воркер", max_length=64, blank=True)
    started_at = models.DateTimeField("Начата", blank=True, null=True)
    finished_at = models.DateTimeField("Завершена", blank=True, null=True)
    last_error_code = models.CharField("Код ошибки", max_length=64, blank=True)
    last_error_message = models.TextField("Описание ошибки", blank=True)
    last_error_payload = models.JSONField("Детали ошибки", default=dict, blank=True)
    base_retry_delay = models.PositiveIntegerField(
        "Базовая задержка ретрая (сек)",
        default=10,
        help_text="Начальное значение экспоненциальной задержки для ретраев.",
    )
    max_retry_delay = models.PositiveIntegerField(
        "Максимальная задержка ретрая (сек)",
        default=3600,
        help_text="Верхняя граница задержки между попытками.",
    )
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлена", auto_now=True)

    class Meta:
        verbose_name = "Фоновая задача"
        verbose_name_plural = "Фоновые задачи"
        ordering = ("priority", "available_at", "id")
        indexes = [
            models.Index(fields=("queue", "status", "available_at")),
            models.Index(fields=("queue", "priority")),
            GinIndex(fields=("payload",)),
        ]

    def __str__(self) -> str:
        return f"Task#{self.pk}:{self.queue}:{self.status}"

    # --- создание и получение -------------------------------------------------

    @classmethod
    def reserve(cls, *, queue: str, worker_id: str, limit: int = 1) -> list[WorkerTask]:
        """Резервирует задачи для выполнения, блокируя записи."""

        now = timezone.now()
        with transaction.atomic():
            tasks = list(
                cls.objects.select_for_update(skip_locked=True)
                .filter(
                    queue=queue,
                    status=cls.Status.QUEUED,
                    available_at__lte=now,
                    attempts__lt=F("max_attempts"),
                )
                .order_by("priority", "available_at", "id")[:limit]
            )
            for task in tasks:
                task._mark_running_now(worker_id=worker_id, now=now)
        return tasks

    @classmethod
    def revive_stale(cls, *, queue: str, max_age_seconds: int) -> int:
        """Возвращает количество задач, возвращенных в очередь, если их блокировка истекла."""

        if max_age_seconds <= 0:
            return 0
        cutoff = timezone.now() - timedelta(seconds=max_age_seconds)
        updated = cls.objects.filter(
            queue=queue,
            status=cls.Status.RUNNING,
            locked_at__lt=cutoff,
        ).update(
            status=cls.Status.QUEUED,
            locked_at=None,
            locked_by="",
            started_at=None,
            available_at=timezone.now(),
        )
        return updated

    # --- статусы --------------------------------------------------------------

    def _mark_running_now(self, *, worker_id: str, now) -> None:
        """Внутренний метод для пометки задачи как выполняющейся внутри транзакции."""

        self.status = self.Status.RUNNING
        self.locked_by = worker_id
        self.locked_at = now
        self.started_at = now
        self.available_at = now
        self.attempts += 1
        self.save(
            update_fields=[
                "status",
                "locked_by",
                "locked_at",
                "started_at",
                "available_at",
                "attempts",
                "updated_at",
            ]
        )

    def mark_succeeded(self, *, result: dict[str, Any] | None = None) -> None:
        """Сохраняет успешный результат и логирует попытку."""

        now = timezone.now()
        started_at = self.started_at
        self.status = self.Status.SUCCEEDED
        self.result = result or {}
        self.finished_at = now
        self.locked_by = ""
        self.locked_at = None
        self.last_error_code = ""
        self.last_error_message = ""
        self.last_error_payload = {}
        self.save(
            update_fields=[
                "status",
                "result",
                "finished_at",
                "locked_by",
                "locked_at",
                "last_error_code",
                "last_error_message",
                "last_error_payload",
                "updated_at",
            ]
        )
        self.log_attempt(status=self.Status.SUCCEEDED, finished_at=now, started_at=started_at)

    def mark_for_retry(
        self,
        *,
        error_code: str,
        error_message: str,
        error_payload: dict[str, Any] | None = None,
        retry_in: timedelta | int | float | None = None,
    ) -> None:
        """Переводит задачу в очередь с экспоненциальной задержкой и логирует неудачную попытку."""

        delay = self._compute_retry_delay(retry_in=retry_in)
        now = timezone.now()
        started_at = self.started_at
        self.status = self.Status.QUEUED
        self.available_at = now + delay
        self.locked_at = None
        self.locked_by = ""
        self.started_at = None
        self.finished_at = None
        self.last_error_code = error_code
        self.last_error_message = error_message
        self.last_error_payload = error_payload or {}
        self.save(
            update_fields=[
                "status",
                "available_at",
                "locked_at",
                "locked_by",
                "started_at",
                "finished_at",
                "last_error_code",
                "last_error_message",
                "last_error_payload",
                "updated_at",
            ]
        )
        self.log_attempt(
            status=self.Status.FAILED,
            finished_at=now,
            error_code=error_code,
            error_message=error_message,
            error_payload=error_payload,
            will_retry=True,
            next_available_at=self.available_at,
            started_at=started_at,
        )

    def mark_failed(
        self,
        *,
        error_code: str,
        error_message: str,
        error_payload: dict[str, Any] | None = None,
    ) -> None:
        """Помечает задачу как окончательно проваленную."""

        now = timezone.now()
        started_at = self.started_at
        self.status = self.Status.FAILED
        self.finished_at = now
        self.locked_by = ""
        self.locked_at = None
        self.last_error_code = error_code
        self.last_error_message = error_message
        self.last_error_payload = error_payload or {}
        self.save(
            update_fields=[
                "status",
                "finished_at",
                "locked_by",
                "locked_at",
                "last_error_code",
                "last_error_message",
                "last_error_payload",
                "updated_at",
            ]
        )
        self.log_attempt(
            status=self.Status.FAILED,
            finished_at=now,
            error_code=error_code,
            error_message=error_message,
            error_payload=error_payload,
            will_retry=False,
            started_at=started_at,
        )

    # --- вспомогательные методы ----------------------------------------------

    def _compute_retry_delay(self, *, retry_in: timedelta | int | float | None) -> timedelta:
        if retry_in is not None:
            if isinstance(retry_in, timedelta):
                return retry_in
            seconds = float(retry_in)
            if seconds <= 0:
                return timedelta(seconds=0)
            return timedelta(seconds=int(seconds))
        base_delay = timedelta(seconds=self.base_retry_delay)
        exponent = max(self.attempts - 1, 0)
        delay_seconds = base_delay.total_seconds() * (2**exponent)
        max_seconds = self.max_retry_delay
        clamped_seconds = min(delay_seconds, max_seconds)
        return timedelta(seconds=int(clamped_seconds))

    def can_retry(self) -> bool:
        """Возвращает True, если у задачи остались попытки для повторного выполнения."""

        return self.attempts < self.max_attempts

    def log_attempt(
        self,
        *,
        status: str,
        finished_at,
        error_code: str | None = None,
        error_message: str | None = None,
        error_payload: dict[str, Any] | None = None,
        will_retry: bool = False,
        next_available_at=None,
        started_at=None,
    ) -> None:
        duration_ms = 0
        reference_start = started_at if started_at is not None else self.started_at
        if reference_start:
            duration_ms = max(
                0,
                int((finished_at - reference_start).total_seconds() * 1000),
            )
        WorkerTaskAttempt.objects.create(
            task=self,
            attempt_number=self.attempts,
            status=status,
            error_code=error_code or "",
            error_message=error_message or "",
            error_payload=error_payload or {},
            duration_ms=duration_ms,
            will_retry=will_retry,
            available_at=next_available_at,
        )


class WorkerTaskAttempt(models.Model):
    """Журнал аудита попыток выполнения фоновых задач."""

    task = models.ForeignKey(
        WorkerTask,
        on_delete=models.CASCADE,
        related_name="attempts_log",
        verbose_name="Задача",
    )
    attempt_number = models.PositiveIntegerField("Номер попытки")
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=WorkerTask.Status.choices,
    )
    error_code = models.CharField("Код ошибки", max_length=64, blank=True)
    error_message = models.TextField("Описание ошибки", blank=True)
    error_payload = models.JSONField("Детали ошибки", default=dict, blank=True)
    duration_ms = models.PositiveIntegerField("Длительность (мс)", default=0)
    will_retry = models.BooleanField("Повтор будет", default=False)
    available_at = models.DateTimeField("Следующая попытка", blank=True, null=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "Попытка выполнения задачи"
        verbose_name_plural = "Попытки выполнения задач"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Task#{self.task_id} attempt {self.attempt_number} ({self.status})"


# --- служебные структуры -----------------------------------------------------

@dataclass(frozen=True, slots=True)
class QueueSettings:
    """Декларативные настройки очередей."""

    max_attempts: int = DEFAULT_QUEUE_MAX_ATTEMPTS
    base_retry_delay: int = DEFAULT_QUEUE_BASE_RETRY_DELAY
    max_retry_delay: int = DEFAULT_QUEUE_MAX_RETRY_DELAY
    stale_lock_timeout: int = DEFAULT_QUEUE_STALE_TIMEOUT


QUEUE_DEFAULTS: dict[str, QueueSettings] = {
    WorkerTask.Queue.COLLECTOR: QueueSettings(
        max_attempts=COLLECTOR_MAX_ATTEMPTS,
        base_retry_delay=COLLECTOR_BASE_RETRY_DELAY,
        max_retry_delay=COLLECTOR_MAX_RETRY_DELAY,
        stale_lock_timeout=COLLECTOR_STALE_TIMEOUT,
    ),
    WorkerTask.Queue.COLLECTOR_WEB: QueueSettings(
        max_attempts=COLLECTOR_MAX_ATTEMPTS,
        base_retry_delay=COLLECTOR_BASE_RETRY_DELAY,
        max_retry_delay=COLLECTOR_MAX_RETRY_DELAY,
        stale_lock_timeout=COLLECTOR_WEB_STALE_TIMEOUT,
    ),
    WorkerTask.Queue.REWRITE: QueueSettings(
        max_attempts=REWRITE_MAX_ATTEMPTS,
        base_retry_delay=REWRITE_BASE_RETRY_DELAY,
        max_retry_delay=REWRITE_MAX_RETRY_DELAY,
    ),
    WorkerTask.Queue.PUBLISH: QueueSettings(
        max_attempts=PUBLISH_MAX_ATTEMPTS,
        base_retry_delay=PUBLISH_BASE_RETRY_DELAY,
        max_retry_delay=PUBLISH_MAX_RETRY_DELAY,
    ),
    WorkerTask.Queue.IMAGE: QueueSettings(
        max_attempts=IMAGE_MAX_ATTEMPTS,
        base_retry_delay=IMAGE_BASE_RETRY_DELAY,
        max_retry_delay=IMAGE_MAX_RETRY_DELAY,
    ),
    WorkerTask.Queue.MAINTENANCE: QueueSettings(
        max_attempts=MAINTENANCE_MAX_ATTEMPTS,
        base_retry_delay=MAINTENANCE_BASE_RETRY_DELAY,
        max_retry_delay=MAINTENANCE_MAX_RETRY_DELAY,
    ),
    WorkerTask.Queue.SOURCE: QueueSettings(
        max_attempts=SOURCE_MAX_ATTEMPTS,
        base_retry_delay=SOURCE_BASE_RETRY_DELAY,
        max_retry_delay=SOURCE_MAX_RETRY_DELAY,
    ),
    WorkerTask.Queue.DEFAULT: QueueSettings(),
}


def queue_settings(queue: str) -> QueueSettings:
    """Возвращает настройки для указанной очереди."""

    return QUEUE_DEFAULTS.get(queue, QueueSettings())
