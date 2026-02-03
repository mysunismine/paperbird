"""Маршруты приложения projects."""

from django.urls import path

from .views import (
    ManualPostCreateView,
    ManualPostUpdateView,
    ManualPostVersionDetailView,
    ProjectCollectorQueueView,
    ProjectCreateView,
    ProjectDeleteView,
    ProjectExportView,
    ProjectListView,
    ProjectPromptExportView,
    ProjectPromptImportView,
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
    path("<int:pk>/delete/", ProjectDeleteView.as_view(), name="delete"),
    path("<int:pk>/export/", ProjectExportView.as_view(), name="export"),
    path("<int:pk>/prompts/", ProjectPromptsView.as_view(), name="prompts"),
    path(
        "<int:pk>/prompts/export/",
        ProjectPromptExportView.as_view(),
        name="prompts-export",
    ),
    path(
        "<int:pk>/prompts/import/",
        ProjectPromptImportView.as_view(),
        name="prompts-import",
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
    path(
        "<int:project_pk>/manual/",
        ManualPostCreateView.as_view(),
        name="manual-post",
    ),
    path(
        "<int:project_pk>/manual/<int:post_pk>/edit/",
        ManualPostUpdateView.as_view(),
        name="manual-post-edit",
    ),
    path(
        "<int:project_pk>/manual/<int:post_pk>/versions/<int:version_pk>/",
        ManualPostVersionDetailView.as_view(),
        name="manual-post-version",
    ),
]
