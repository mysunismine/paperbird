"""Public URL configuration for published stories."""

from django.urls import path

from .views import PublicIndexView, PublicProjectView, PublicPublicationView

app_name = "public"

urlpatterns = [
    path("", PublicIndexView.as_view(), name="index"),
    path("<int:project_id>/", PublicProjectView.as_view(), name="project"),
    path(
        "<int:project_id>/p/<int:publication_id>/",
        PublicPublicationView.as_view(),
        name="publication",
    ),
]
