"""Маршруты приложения projects."""

from django.urls import path

from .views import (
    ProjectCollectorQueueView,
    ProjectCreateView,
    ProjectListView,
    ProjectPromptExportView,
    ProjectPromptsView,
    ProjectSettingsView,
    ProjectSourceCreateView,
    ProjectSourceDetailView,
    ProjectSourcesView,
    ProjectSourceUpdateView,
    delete_source,
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
        "<int:project_pk>/sources/create/",
        ProjectSourceCreateView.as_view(),
        name="source-create",
    ),
    path(
        "<int:project_pk>/sources/<int:pk>/",
        ProjectSourceDetailView.as_view(),
        name="source-detail",
    ),
    path(
        "<int:project_pk>/sources/<int:pk>/edit/",
        ProjectSourceUpdateView.as_view(),
        name="source-edit",
    ),
    path(
        "<int:project_pk>/sources/<int:pk>/delete/",
        delete_source,
        name="sources-delete",
    ),
    path("<int:pk>/queues/", ProjectCollectorQueueView.as_view(), name="queue"),
]
