"""Views package for projects app."""

from .collector import ProjectCollectorQueueView
from .feed import ProjectPostDetailView, ProjectPostListView
from .projects import ProjectCreateView, ProjectListView
from .prompts import ProjectPromptExportView, ProjectPromptsView
from .settings import ProjectSettingsView
from .sources import ProjectSourceCreateView, ProjectSourcesView, ProjectSourceUpdateView

__all__ = [
    "ProjectCollectorQueueView",
    "ProjectCreateView",
    "ProjectListView",
    "ProjectPostDetailView",
    "ProjectPostListView",
    "ProjectPromptExportView",
    "ProjectPromptsView",
    "ProjectSettingsView",
    "ProjectSourceCreateView",
    "ProjectSourcesView",
    "ProjectSourceUpdateView",
]
