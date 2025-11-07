"""Маршруты приложения projects."""

from django.urls import path

from .views import (
    ProjectCreateView,
    ProjectListView,
    ProjectPromptExportView,
    ProjectPromptsView,
    ProjectSettingsView,
    ProjectSourcesView,
    ProjectSourceUpdateView,
)

app_name = "projects"

urlpatterns = [
    path("", ProjectListView.as_view(), name="list"),
    path("create/", ProjectCreateView.as_view(), name="create"),
    path("<int:pk>/settings/", ProjectSettingsView.as_view(), name="settings"),
    path("<int:pk>/prompts/", ProjectPromptsView.as_view(), name="prompts"),
    path(
        "<int:pk>/prompts/export/",
        ProjectPromptExportView.as_view(),
        name="prompts-export",
    ),
    path("<int:pk>/sources/", ProjectSourcesView.as_view(), name="sources"),
    path(
        "<int:project_pk>/sources/<int:pk>/edit/",
        ProjectSourceUpdateView.as_view(),
        name="source-edit",
    ),
]
