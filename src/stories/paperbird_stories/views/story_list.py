"""Views for listing and quick creation of stories."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.views import View
from django.views.generic import ListView

from projects.models import Post, Project
from stories.paperbird_stories.models import Story
from stories.paperbird_stories.services import StoryCreationError, StoryFactory


class StoryListView(LoginRequiredMixin, ListView):
    """Список сюжетов пользователя."""

    model = Story
    template_name = "stories/story_list.html"
    context_object_name = "stories"

    def get_queryset(self):
        return Story.objects.filter(project__owner=self.request.user).select_related("project")


class StoryCreateView(LoginRequiredMixin, View):
    """Создание сюжета из выбранных постов без промежуточной страницы."""

    def post(self, request, *args, **kwargs):
        post_ids_raw = request.POST.getlist("posts")
        project_id = request.POST.get("project")
        if not post_ids_raw:
            messages.error(request, "Выберите посты для сюжета")
            return self._redirect_back(project_id)
        try:
            selected_ids = [int(value) for value in post_ids_raw]
        except ValueError:
            messages.error(request, "Некорректный список постов")
            return self._redirect_back(project_id)

        project = get_object_or_404(
            Project.objects.filter(owner=request.user),
            pk=project_id,
        )
        posts = list(
            Post.objects.filter(project=project, pk__in=selected_ids)
            .select_related("project")
        )
        if len(posts) == 0:
            messages.error(request, "Не удалось найти выбранные посты")
            return self._redirect_back(project.pk)
        order_map = {pk: index for index, pk in enumerate(selected_ids)}
        posts.sort(key=lambda post: order_map.get(post.pk, 0))
        try:
            story = StoryFactory(project=project).create(
                post_ids=[post.pk for post in posts],
                title="",
            )
        except StoryCreationError as exc:
            messages.error(request, str(exc))
            return self._redirect_back(project.pk)
        messages.success(request, "Сюжет создан. Добавьте комментарий и запустите рерайт.")
        return redirect("stories:detail", pk=story.pk)

    def _redirect_back(self, project_id: int | str | None):
        if project_id and str(project_id).isdigit():
            return redirect("feed-detail", int(project_id))
        first_project = self.request.user.projects.order_by("id").first()
        if first_project:
            return redirect("feed-detail", first_project.id)
        return redirect("projects:list")


class StoryDeleteView(LoginRequiredMixin, View):
    """Удаление сюжета пользователя."""

    def post(self, request, pk: int, *args, **kwargs):
        story = get_object_or_404(
            Story.objects.select_related("project"),
            pk=pk,
            project__owner=request.user,
        )
        title = story.title.strip() if story.title else ""
        story.delete()
        display = title or f"Сюжет #{pk}"
        messages.success(request, f"Сюжет «{display}» удалён.")
        return redirect("stories:list")
