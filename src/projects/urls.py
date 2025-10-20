"""Маршруты приложения projects."""

from django.urls import path

from .views import ProjectCreateView, ProjectListView, ProjectPostListView

app_name = "projects"

urlpatterns = [
    path("", ProjectListView.as_view(), name="list"),
    path("create/", ProjectCreateView.as_view(), name="create"),
    path("<int:pk>/posts/", ProjectPostListView.as_view(), name="post-list"),
]
