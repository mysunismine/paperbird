"""Админка для управления сюжетами и задачами рерайта."""

from django.contrib import admin

from .models import Publication, RewritePreset, RewriteTask, Story, StoryPost


class StoryPostInline(admin.TabularInline):
    model = StoryPost
    extra = 0
    readonly_fields = ("post", "position", "added_at")


@admin.register(Story)
class StoryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "project",
        "status",
        "last_rewrite_preset",
        "updated_at",
    )
    list_filter = ("status", "project")
    search_fields = ("title", "project__name")
    inlines = [StoryPostInline]
    readonly_fields = (
        "prompt_snapshot",
        "last_rewrite_payload",
        "last_rewrite_at",
        "last_rewrite_preset",
        "created_at",
        "updated_at",
    )


@admin.register(RewriteTask)
class RewriteTaskAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "story",
        "status",
        "provider",
        "preset",
        "created_at",
    )
    list_filter = ("status", "provider", "preset")
    search_fields = ("story__title", "response_id")
    readonly_fields = (
        "prompt_messages",
        "result",
        "error_message",
        "attempts",
        "started_at",
        "finished_at",
        "preset",
        "created_at",
        "updated_at",
    )


@admin.register(StoryPost)
class StoryPostAdmin(admin.ModelAdmin):
    list_display = ("id", "story", "post", "position", "added_at")
    list_filter = ("story__project",)
    search_fields = ("story__title", "post__message")
    readonly_fields = ("added_at",)


@admin.register(Publication)
class PublicationAdmin(admin.ModelAdmin):
    list_display = ("id", "story", "target", "status", "published_at")
    list_filter = ("status", "target")
    search_fields = ("story__title", "target")
    readonly_fields = (
        "result_text",
        "message_ids",
        "error_message",
        "attempts",
        "raw_response",
        "created_at",
        "updated_at",
    )


@admin.register(RewritePreset)
class RewritePresetAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "project",
        "name",
        "style",
        "max_length_tokens",
        "is_active",
        "updated_at",
    )
    list_filter = ("project", "is_active")
    search_fields = ("name", "project__name", "description", "style")
    ordering = ("project", "name")
