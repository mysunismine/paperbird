"""Views package for projects app."""

from .collector import ProjectCollectorQueueView
from .export import ProjectExportView
from .feed import ProjectPostDetailView, ProjectPostListView
from .manual import (
    ManualPostCreateView,
    ManualPostUpdateView,
    ManualPostVersionDetailView,
)
from .projects import (
    ProjectChatFeedView,
    ProjectCreateView,
    ProjectImportView,
    ProjectListView,
)
from .prompts import ProjectPromptExportView, ProjectPromptImportView, ProjectPromptsView
from .settings import ProjectDeleteView, ProjectSettingsView
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
    "ProjectChatFeedView",
    "ProjectImportView",
    "ProjectExportView",
    "ProjectListView",
    "ProjectPostDetailView",
    "ProjectPostListView",
    "ManualPostCreateView",
    "ManualPostUpdateView",
    "ManualPostVersionDetailView",
    "ProjectPromptExportView",
    "ProjectPromptImportView",
    "ProjectPromptsView",
    "ProjectDeleteView",
    "ProjectSettingsView",
    "ProjectSourceCreateView",
    "ProjectSourceDetailView",
    "ProjectSourcesView",
    "ProjectSourceUpdateView",
    "delete_source",
]
