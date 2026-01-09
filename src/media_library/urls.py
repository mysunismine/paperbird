"""URL configuration for media library."""

from django.urls import path

from media_library.views import (
    MediaLibraryAssetsView,
    MediaLibraryTagSuggestionsView,
    MediaLibraryView,
    MediaStudioView,
    MediaAssetDetailView,
)

app_name = "media_library"

urlpatterns = [
    path("library/", MediaLibraryView.as_view(), name="library"),
    path("library/assets/", MediaLibraryAssetsView.as_view(), name="library_assets"),
    path("library/tags/", MediaLibraryTagSuggestionsView.as_view(), name="library_tags"),
    path("library/asset/<int:pk>/", MediaAssetDetailView.as_view(), name="asset_detail"),
    path("studio/", MediaStudioView.as_view(), name="studio"),
    path("studio/sandbox/", MediaStudioView.as_view(), kwargs={"story_id": "sandbox"}, name="studio_sandbox"),
    path("studio/<int:story_id>/", MediaStudioView.as_view(), name="studio_story"),
]
