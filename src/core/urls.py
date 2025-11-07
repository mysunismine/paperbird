from django.urls import path

from projects.views import ProjectPostListView

from .views import FeedView, HomeView

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("feed/", FeedView.as_view(), name="feed"),
    path("feed/<int:pk>/", ProjectPostListView.as_view(), name="feed-detail"),
]
