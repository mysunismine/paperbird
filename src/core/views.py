from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.template.response import TemplateResponse
from django.views.generic import TemplateView

from projects.models import Post, Project


class HomeView(TemplateView):
    template_name = "core/home.html"


class FeedView(LoginRequiredMixin, TemplateView):
    """Отображает последние посты пользователя по всем проектам."""

    template_name = "core/feed.html"
    paginate_by = 50

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        project_id = self.request.GET.get("project")
        projects = Project.objects.filter(owner=self.request.user).order_by("name")
        posts = (
            Post.objects.filter(project__owner=self.request.user)
            .select_related("project", "source")
            .order_by("-posted_at")
        )
        selected_project_id = None
        if project_id and project_id.isdigit():
            selected_project_id = int(project_id)
            posts = posts.filter(project_id=selected_project_id)
        latest_posts = list(posts[: self.paginate_by])
        context.update(
            {
                "projects": projects,
                "selected_project_id": selected_project_id,
                "latest_posts": latest_posts,
                "limit": self.paginate_by,
            }
        )
        return context


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
