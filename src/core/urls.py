"""URL-конфигурация для приложения `core`."""

from django.urls import path

from projects.views import ProjectPostDetailView, ProjectPostListView

from .views import FeedView, HomeView

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("feed/", FeedView.as_view(), name="feed"),
    path("feed/<int:pk>/", ProjectPostListView.as_view(), name="feed-detail"),
    path(
        "feed/<int:project_pk>/posts/<int:post_pk>/",
        ProjectPostDetailView.as_view(),
        name="feed-post-detail",
    ),
]
