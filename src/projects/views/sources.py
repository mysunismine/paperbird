"""Views for managing project sources."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import FormView, TemplateView, UpdateView

from core.models import WorkerTask
from core.services.worker import enqueue_task
from projects.models import Project, Source

from ..forms import SourceCreateForm, SourceUpdateForm


class ProjectSourcesView(LoginRequiredMixin, TemplateView):
    """Список источников проекта с действиями управления."""

    template_name = "projects/project_sources.html"

    def dispatch(self, request, *args, **kwargs):
        """Проверяет права доступа к проекту и инициализирует его."""
        self.project = get_object_or_404(
            Project,
            pk=kwargs["pk"],
            owner=request.user,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        """Формирует контекст для шаблона."""
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "project": self.project,
                "sources": self.project.sources.order_by("type", "title", "telegram_id"),
                "create_url": reverse_lazy(
                    "projects:source-create",
                    kwargs={"project_pk": self.project.pk},
                ),
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        """Обрабатывает POST-запросы для управления источниками."""
        if request.POST.get("action") == "delete":
            return self._handle_delete(request)
        messages.error(request, "Неизвестное действие.")
        return redirect("projects:sources", pk=self.project.pk)

    def _handle_delete(self, request):
        """Обрабатывает запрос на удаление источника."""
        source_id = request.POST.get("source_id")
        source = self.project.sources.filter(pk=source_id).first()
        if source is None:
            messages.error(request, "Источник не найден")
        else:
            source.delete()
            messages.success(request, "Источник удалён.")
        return redirect("projects:sources", pk=self.project.pk)


class ProjectSourceCreateView(LoginRequiredMixin, FormView):
    """Отдельная страница добавления нового источника."""

    template_name = "projects/project_source_create.html"
    form_class = SourceCreateForm

    def dispatch(self, request, *args, **kwargs):
        """Проверяет права доступа к проекту и инициализирует его."""
        self.project = get_object_or_404(
            Project,
            pk=kwargs["project_pk"],
            owner=request.user,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict:
        """Возвращает аргументы для формы, включая проект."""
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def get_context_data(self, **kwargs):
        """Формирует контекст для шаблона."""
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        return context

    def form_valid(self, form):  # type: ignore[override]
        """Обрабатывает валидную форму, сохраняет источник и перенаправляет."""
        source = form.save()
        if source.type == Source.Type.WEB:
            self._schedule_web_source_collection(source)
        else:
            messages.success(self.request, "Источник добавлен к проекту.")
        return redirect("projects:sources", pk=source.project_id)

    def form_invalid(self, form):
        """Обрабатывает невалидную форму, выводит сообщение об ошибке."""
        messages.error(self.request, "Исправьте ошибки формы и попробуйте снова.")
        return super().form_invalid(form)

    def _schedule_web_source_collection(self, source: Source) -> None:
        """Планирует сбор для веб-источника."""
        payload = {
            "project_id": source.project_id,
            "interval": max(source.project.collector_web_interval, 60),
            "source_id": source.pk,
        }
        try:
            enqueue_task(
                WorkerTask.Queue.COLLECTOR_WEB,
                payload=payload,
                scheduled_for=timezone.now(),
            )
        except Exception as exc:  # pragma: no cover - defensive logging
            messages.error(
                self.request,
                f"Источник добавлен, но не удалось запустить парсер: {exc}",
            )
        else:
            messages.info(
                self.request,
                "Источник добавлен. Запускаем парсер — посты скоро появятся в ленте.",
            )


class ProjectSourceUpdateView(LoginRequiredMixin, UpdateView):
    """Отдельная страница редактирования источника."""

    model = Source
    form_class = SourceUpdateForm
    template_name = "projects/project_source_form.html"
    context_object_name = "source"

    def get_queryset(self):
        """Возвращает queryset источников для текущего пользователя и проекта."""
        return Source.objects.filter(
            project__owner=self.request.user,
            project_id=self.kwargs["project_pk"],
        ).select_related("project")

    def get_context_data(self, **kwargs):
        """Формирует контекст для шаблона."""
        context = super().get_context_data(**kwargs)
        context["project"] = self.object.project
        return context

    def get_form_kwargs(self) -> dict:
        """Возвращает аргументы для формы, включая проект."""
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.get_object().project
        return kwargs

    def form_valid(self, form):  # type: ignore[override]
        """Обрабатывает валидную форму, сохраняет источник и выводит сообщение."""
        source = form.save()
        messages.success(self.request, "Источник обновлён.")
        return redirect("projects:sources", pk=source.project_id)
