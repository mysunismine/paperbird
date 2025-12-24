"""Views for listing and creating projects."""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView

from core.constants import IMAGE_PROVIDER_SETTINGS
from projects.models import Project

from ..forms import ProjectCreateForm


class ProjectListView(LoginRequiredMixin, ListView):
    """Список проектов пользователя с краткой статистикой."""

    model = Project
    template_name = "projects/project_list.html"
    context_object_name = "projects"

    def get_queryset(self):
        """Возвращает queryset проектов текущего пользователя с аннотациями."""
        return (
            Project.objects.filter(owner=self.request.user)
            .annotate(
                posts_total=Count("posts", distinct=True),
                stories_total=Count("stories", distinct=True),
            )
            .order_by("name")
        )


class ProjectCreateView(LoginRequiredMixin, CreateView):
    """Веб-форма для создания нового проекта."""

    form_class = ProjectCreateForm
    template_name = "projects/project_form.html"
    success_url = reverse_lazy("projects:list")

    def get_form_kwargs(self) -> dict:
        """Возвращает аргументы для формы, включая владельца."""
        kwargs = super().get_form_kwargs()
        kwargs["owner"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["image_provider_settings"] = json.dumps(IMAGE_PROVIDER_SETTINGS)
        return context

    def form_valid(self, form):  # type: ignore[override]
        """Обрабатывает валидную форму, сохраняет проект и выводит сообщение."""
        response = super().form_valid(form)
        messages.success(
            self.request,
            f"Проект «{self.object.name}» создан.",
        )
        return response
