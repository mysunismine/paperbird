from django.contrib.auth.mixins import LoginRequiredMixin
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
