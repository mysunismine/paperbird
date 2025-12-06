from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.views.generic import RedirectView, TemplateView

from projects.models import Project


class HomeView(TemplateView):
    template_name = "core/home.html"


class FeedView(LoginRequiredMixin, RedirectView):
    """Перенаправляет на ленту выбранного проекта."""

    permanent = False

    def get_redirect_url(self, *args, **kwargs):
        project_id = self.request.GET.get("project")
        projects = Project.objects.filter(owner=self.request.user).order_by("name")
        if project_id and project_id.isdigit():
            project = projects.filter(pk=int(project_id)).first()
            if project:
                return reverse("feed-detail", args=[project.pk])
        project = projects.first()
        if project:
            return reverse("feed-detail", args=[project.pk])
        return reverse("projects:list")


def server_error(request: HttpRequest) -> HttpResponse:
    """Отображает пользовательскую страницу ошибки 500."""

    correlation_id = getattr(request, "correlation_id", "")
    response = TemplateResponse(
        request,
        "errors/server_error.html",
        {"correlation_id": correlation_id},
        status=500,
    )
    response.render()
    if correlation_id:
        response["X-Correlation-ID"] = correlation_id
    return response
