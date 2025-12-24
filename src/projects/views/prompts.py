"""Views for managing project prompt configuration."""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import FormView, View

from projects.models import Project
from projects.services.prompt_config import (
    IMAGE_PROMPT_SECTION,
    PROMPT_SECTION_HINTS,
    PROMPT_SECTION_ORDER,
    ensure_prompt_config,
    render_prompt,
    tokens_help,
)

from ..forms import ProjectPromptConfigForm


class ProjectPromptsView(LoginRequiredMixin, FormView):
    """Отдельная страница управления основным промтом проекта."""

    template_name = "projects/project_prompts.html"
    form_class = ProjectPromptConfigForm

    def dispatch(self, request, *args, **kwargs):
        """Проверяет права доступа к проекту и инициализирует конфигурацию промта."""
        self.project = get_object_or_404(
            Project, pk=kwargs["pk"], owner=request.user
        )
        self.config = ensure_prompt_config(self.project)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict:
        """Возвращает аргументы для формы, включая инстанс конфигурации промта."""
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.config
        return kwargs

    def form_valid(self, form):  # type: ignore[override]
        """Обрабатывает валидную форму, сохраняет конфигурацию промта и выводит сообщение."""
        form.save()
        messages.success(
            self.request,
            f"Промт проекта «{self.project.name}» сохранён.",
        )
        return redirect("projects:prompts", pk=self.project.pk)

    def form_invalid(self, form):
        """Обрабатывает невалидную форму, выводит сообщение об ошибке."""
        messages.error(
            self.request,
            "Исправьте ошибки в шаблоне промта и попробуйте снова.",
        )
        return super().form_invalid(form)

    def get_context_data(self, **kwargs):
        """Формирует контекст для шаблона."""
        context = super().get_context_data(**kwargs)
        form = context.get("form") or self.get_form()
        context.update(
            {
                "project": self.project,
                "sections": self._build_sections(form),
                "token_help": tokens_help(),
            }
        )
        return context

    def _build_sections(self, form):
        """Строит список секций промта для отображения в форме."""
        sections = []
        for field_name, heading in PROMPT_SECTION_ORDER:
            field = form[field_name]
            sections.append(
                {
                    "heading": heading,
                    "field": field,
                    "hint": PROMPT_SECTION_HINTS.get(field_name, ""),
                }
            )
        image_field, image_heading = IMAGE_PROMPT_SECTION
        sections.append(
            {
                "heading": image_heading,
                "field": form[image_field],
                "hint": PROMPT_SECTION_HINTS.get(image_field, ""),
            }
        )
        return sections


class ProjectPromptExportView(LoginRequiredMixin, View):
    """Формирует предпросмотр промтов в текстовом виде."""

    def get(self, request, *args, **kwargs):
        """Генерирует и возвращает текстовый файл с экспортом промтов."""
        project = get_object_or_404(
            Project, pk=kwargs["pk"], owner=request.user
        )
        ensure_prompt_config(project)
        content = self._render_export(project)
        filename = f"project-{project.pk}-prompt.txt"
        response = HttpResponse(
            content,
            content_type="text/plain; charset=utf-8",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    def _render_export(self, project: Project) -> str:
        """Рендерит промты для экспорта."""
        rendered = render_prompt(
            project=project,
            posts=[],
            preview_mode=True,
            editor_comment="",
        )
        return rendered.full_text


class ProjectPromptImportView(LoginRequiredMixin, View):
    """Импортирует конфигурацию промптов проекта из JSON/YAML."""

    def post(self, request, *args, **kwargs):
        project = get_object_or_404(
            Project,
            pk=kwargs["pk"],
            owner=request.user,
        )
        file = request.FILES.get("prompt_file")
        payload = (request.POST.get("prompt_payload") or "").strip()
        if file:
            payload = file.read().decode("utf-8", "replace").strip()
        if not payload:
            messages.error(request, "Загрузите файл или вставьте JSON/YAML промпта.")
            return redirect("projects:prompts", pk=project.pk)

        data = self._parse_payload(payload)
        if not isinstance(data, dict):
            messages.error(request, "Некорректный формат промпта.")
            return redirect("projects:prompts", pk=project.pk)

        prompt_data = data.get("prompt_config", data)
        if not isinstance(prompt_data, dict):
            messages.error(request, "Некорректный формат промпта.")
            return redirect("projects:prompts", pk=project.pk)

        config = ensure_prompt_config(project)
        updated_fields = []
        for field in ProjectPromptConfigForm.Meta.fields:
            if field in prompt_data:
                setattr(config, field, prompt_data.get(field) or "")
                updated_fields.append(field)
        if not updated_fields:
            messages.error(request, "Не нашли подходящих полей промпта для импорта.")
            return redirect("projects:prompts", pk=project.pk)
        config.save(update_fields=updated_fields + ["updated_at"])
        messages.success(request, "Промпт проекта обновлён из файла.")
        return redirect("projects:prompts", pk=project.pk)

    @staticmethod
    def _parse_payload(payload: str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            pass
        try:
            import yaml
        except ModuleNotFoundError:
            return None
        try:
            return yaml.safe_load(payload)
        except yaml.YAMLError:
            return None
