"""Admin configuration for media library."""

from django.contrib import admin

from media_library.models import MediaAsset


@admin.register(MediaAsset)
class MediaAssetAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "title", "media_type", "source_kind", "created_at")
    list_filter = ("media_type", "source_kind", "project")
    search_fields = ("title", "prompt")
