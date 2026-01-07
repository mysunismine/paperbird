"""URL configuration for media library."""

from django.urls import path

from media_library.views import MediaLibraryView, MediaStudioView

app_name = "media_library"

urlpatterns = [
    path("library/", MediaLibraryView.as_view(), name="library"),
    path("studio/", MediaStudioView.as_view(), name="studio"),
    path("studio/<int:story_id>/", MediaStudioView.as_view(), name="studio_story"),
]
