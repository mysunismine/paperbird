"""Маршруты для работы с сюжетами."""

from django.urls import path

from .views import (
    PublicationListView,
    StoryCreateView,
    StoryDetailView,
    StoryImageView,
    StoryListView,
)

app_name = "stories"

urlpatterns = [
    path("", StoryListView.as_view(), name="list"),
    path("create/", StoryCreateView.as_view(), name="create"),
    path("<int:pk>/", StoryDetailView.as_view(), name="detail"),
    path("<int:pk>/image/", StoryImageView.as_view(), name="image"),
    path("publications/", PublicationListView.as_view(), name="publications"),
]
