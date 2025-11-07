"""Админ-панель для проектов, источников и постов."""

from django.contrib import admin

from projects.models import Post, Project, Source, SourceSyncLog, WebPreset


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
        "type",
        "username",
        "telegram_id",
        "web_preset",
        "is_active",
        "last_synced_at",
    )
    list_filter = ("is_active", "project", "type")
    search_fields = ("title", "username", "telegram_id", "web_preset__name")
    readonly_fields = (
        "created_at",
        "updated_at",
        "last_synced_at",
        "last_synced_id",
        "web_last_synced_at",
    )


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = (
        "origin_type",
        "telegram_id",
        "external_id",
        "project",
        "source",
        "status",
        "posted_at",
        "collected_at",
        "has_media",
    )
    list_filter = ("status", "project", "source", "has_media", "origin_type")
    search_fields = ("telegram_id", "message", "source_url", "canonical_url")
    readonly_fields = ("collected_at", "updated_at", "text_hash", "media_hash", "content_hash")
    date_hierarchy = "posted_at"


@admin.register(SourceSyncLog)
class SourceSyncLogAdmin(admin.ModelAdmin):
    list_display = ("source", "status", "fetched_messages", "skipped_messages", "started_at")
    list_filter = ("status", "source__project")
    search_fields = ("source__title", "source__username")
    readonly_fields = ("started_at", "finished_at")


@admin.register(WebPreset)
class WebPresetAdmin(admin.ModelAdmin):
    list_display = ("name", "version", "status", "schema_version", "updated_at")
    list_filter = ("status",)
    search_fields = ("name", "version", "description")
