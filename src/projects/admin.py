"""Админ-панель для проектов, источников и постов."""

from django.contrib import admin

from projects.models import Post, Project, Source, SourceSyncLog


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("name", "owner__username", "owner__email")
    readonly_fields = ("created_at", "updated_at")


@admin.register(Source)
class SourceAdmin(admin.ModelAdmin):
    list_display = (
        "project",
        "title",
        "username",
        "telegram_id",
        "is_active",
        "last_synced_at",
    )
    list_filter = ("is_active", "project")
    search_fields = ("title", "username", "telegram_id")
    readonly_fields = ("created_at", "updated_at", "last_synced_at", "last_synced_id")


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = (
        "telegram_id",
        "project",
        "source",
        "status",
        "posted_at",
        "collected_at",
        "has_media",
    )
    list_filter = ("status", "project", "source", "has_media")
    search_fields = ("telegram_id", "message")
    readonly_fields = ("collected_at", "updated_at", "text_hash", "media_hash")
    date_hierarchy = "posted_at"


@admin.register(SourceSyncLog)
class SourceSyncLogAdmin(admin.ModelAdmin):
    list_display = ("source", "status", "fetched_messages", "skipped_messages", "started_at")
    list_filter = ("status", "source__project")
    search_fields = ("source__title", "source__username")
    readonly_fields = ("started_at", "finished_at")
