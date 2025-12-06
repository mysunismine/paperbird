"""Регистрация моделей воркера в админ-панели."""

from __future__ import annotations

from django.contrib import admin

from .models import WorkerTask, WorkerTaskAttempt


@admin.register(WorkerTask)
class WorkerTaskAdmin(admin.ModelAdmin):
    """Настройки админ-панели для фоновых задач."""

    list_display = (
        "id",
        "queue",
        "status",
        "priority",
        "attempts",
        "max_attempts",
        "available_at",
        "locked_by",
        "last_error_code",
    )
    list_filter = ("queue", "status")
    search_fields = ("id", "queue", "locked_by", "last_error_code")
    ordering = ("queue", "priority", "available_at")
    readonly_fields = (
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "locked_at",
        "payload",
        "result",
        "last_error_payload",
    )
    fieldsets = (
        (None, {"fields": ("queue", "status", "priority", "payload", "result")}),
        (
            "Execution",
            {
                "fields": (
                    "attempts",
                    "max_attempts",
                    "available_at",
                    "locked_by",
                    "locked_at",
                    "started_at",
                    "finished_at",
                )
            },
        ),
        (
            "Retry policy",
            {
                "fields": (
                    "base_retry_delay",
                    "max_retry_delay",
                )
            },
        ),
        (
            "Error",
            {
                "fields": (
                    "last_error_code",
                    "last_error_message",
                    "last_error_payload",
                )
            },
        ),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(WorkerTaskAttempt)
class WorkerTaskAttemptAdmin(admin.ModelAdmin):
    """Настройки админ-панели для попыток выполнения задач."""

    list_display = (
        "id",
        "task",
        "attempt_number",
        "status",
        "duration_ms",
        "will_retry",
        "available_at",
        "created_at",
    )
    list_filter = ("status", "will_retry")
    search_fields = ("task__id", "error_code", "error_message")
    ordering = ("-created_at",)
    readonly_fields = (
        "task",
        "attempt_number",
        "status",
        "error_code",
        "error_message",
        "error_payload",
        "duration_ms",
        "will_retry",
        "available_at",
        "created_at",
    )
