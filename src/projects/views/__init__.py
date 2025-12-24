"""Views package for projects app."""

from .collector import ProjectCollectorQueueView
from .export import ProjectExportView
from .feed import ProjectPostDetailView, ProjectPostListView
from .projects import ProjectCreateView, ProjectListView
from .prompts import ProjectPromptExportView, ProjectPromptImportView, ProjectPromptsView
from .settings import ProjectSettingsView
from .sources import (
    ProjectSourceCreateView,
    ProjectSourceDetailView,
    ProjectSourcesView,
    ProjectSourceUpdateView,
    delete_source,
)

__all__ = [
    "ProjectCollectorQueueView",
    "ProjectCreateView",
    "ProjectExportView",
    "ProjectListView",
    "ProjectPostDetailView",
    "ProjectPostListView",
    "ProjectPromptExportView",
    "ProjectPromptImportView",
    "ProjectPromptsView",
    "ProjectSettingsView",
    "ProjectSourceCreateView",
    "ProjectSourceDetailView",
    "ProjectSourcesView",
    "ProjectSourceUpdateView",
    "delete_source",
]
