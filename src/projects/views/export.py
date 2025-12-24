"""Project export view."""

from __future__ import annotations

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views import View

from projects.models import Project
from projects.services.project_export import build_project_export

try:  # pragma: no cover - зависит от окружения
    import yaml
except ModuleNotFoundError:  # pragma: no cover - fallback если зависимость не установлена
    yaml = None


class ProjectExportView(LoginRequiredMixin, View):
    """Экспортирует настройки проекта и источников."""

    def get(self, request, *args, **kwargs):
        project = get_object_or_404(
            Project,
            pk=kwargs["pk"],
            owner=request.user,
        )
        export_payload = build_project_export(project)
        fmt = (request.GET.get("format") or "json").lower()
        if fmt in {"yaml", "yml"} and yaml:
            content = yaml.safe_dump(
                export_payload,
                allow_unicode=True,
                sort_keys=False,
            )
            filename = f"project-{project.pk}-export.yaml"
            content_type = "text/yaml; charset=utf-8"
        else:
            content = json.dumps(export_payload, ensure_ascii=False, indent=2)
            filename = f"project-{project.pk}-export.json"
            content_type = "application/json; charset=utf-8"
        response = HttpResponse(content, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
