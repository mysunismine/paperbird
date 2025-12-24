"""Project settings view."""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import UpdateView

from core.constants import IMAGE_PROVIDER_SETTINGS
from projects.models import Project

from ..forms import ProjectCreateForm


class ProjectSettingsView(LoginRequiredMixin, UpdateView):
    """Настройки существующего проекта."""

    model = Project
    form_class = ProjectCreateForm
    template_name = "projects/project_settings.html"
    context_object_name = "project"
    success_url = reverse_lazy("projects:list")

    def get_queryset(self):
        """Возвращает queryset проектов текущего пользователя."""
        return Project.objects.filter(owner=self.request.user)

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
        """Обрабатывает валидную форму, сохраняет настройки и выводит сообщение."""
        response = super().form_valid(form)
        messages.success(
            self.request,
            f"Настройки проекта «{self.object.name}» обновлены.",
        )
        return response
