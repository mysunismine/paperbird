"""Маршруты для работы с сюжетами."""

from django.urls import path

from .views import StoryCreateView, StoryDetailView, StoryListView

app_name = "stories"

urlpatterns = [
    path("", StoryListView.as_view(), name="list"),
    path("create/", StoryCreateView.as_view(), name="create"),
    path("<int:pk>/", StoryDetailView.as_view(), name="detail"),
]
