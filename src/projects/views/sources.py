"""Views for managing project sources."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, FormView, TemplateView, UpdateView

from core.models import WorkerTask
from projects.models import Project, Source
from projects.services.collector_scheduler import ensure_collector_tasks

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

    def post(self, request, *args, **kwargs):
        """Поддерживает удаление источника со страницы списка."""
        if request.POST.get("action") != "delete":
            messages.error(request, "Неизвестное действие.")
            return redirect("projects:sources", pk=self.project.pk)

        source_id = request.POST.get("source_id")
        if not source_id or not source_id.isdigit():
            messages.error(request, "Некорректный идентификатор источника.")
            return redirect("projects:sources", pk=self.project.pk)

        source = get_object_or_404(Source, pk=int(source_id), project=self.project)
        source.delete()
        ensure_collector_tasks(self.project)
        messages.success(request, "Источник удалён.")
        return redirect("projects:sources", pk=self.project.pk)

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


class ProjectSourceDetailView(LoginRequiredMixin, DetailView):
    """Страница просмотра детальной информации об источнике."""

    model = Source
    template_name = "projects/project_source_detail.html"
    context_object_name = "source"

    def get_queryset(self):
        """Возвращает queryset с предзагрузкой связанных данных."""
        return (
            Source.objects.filter(
                project__owner=self.request.user,
                project_id=self.kwargs["project_pk"],
            )
            .select_related("project")
            .prefetch_related("posts", "sync_logs")
        )

    def get_context_data(self, **kwargs):
        """Формирует контекст для шаблона с дополнительными данными."""
        context = super().get_context_data(**kwargs)
        source = self.object
        context["project"] = source.project
        context["posts_count"] = source.posts.count()

        # Determine status
        status_display = "Активен"
        status_color = "success"
        if not source.is_active:
            status_display = "Приостановлен"
            status_color = "warning"
        else:
            latest_log = source.sync_logs.order_by("-started_at").first()
            if latest_log and latest_log.status == "failed":
                status_display = "Ошибка"
                status_color = "danger"

        context["status_display"] = status_display
        context["status_color"] = status_color
        return context


class ProjectSourceCreateView(LoginRequiredMixin, FormView):
    """Отдельная страница добавления нового источника."""

    template_name = "projects/project_source_form.html"
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
        kwargs["is_create"] = True
        return kwargs

    def get_context_data(self, **kwargs):
        """Формирует контекст для шаблона."""
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        context["source"] = None
        return context

    def form_valid(self, form):  # type: ignore[override]
        """Обрабатывает валидную форму, сохраняет источник и перенаправляет."""
        source = form.save()
        if source.type == Source.Type.WEB:
            self._schedule_web_source_collection(source)
        else:
            messages.success(self.request, "Источник добавлен к проекту.")
        ensure_collector_tasks(source.project)
        return redirect("projects:source-detail", project_pk=source.project_id, pk=source.pk)

    def form_invalid(self, form):
        """Обрабатывает невалидную форму, выводит сообщение об ошибке."""
        messages.error(self.request, "Исправьте ошибки формы и попробуйте снова.")
        return super().form_invalid(form)

    def _schedule_web_source_collection(self, source: Source) -> None:
        """Планирует сбор для веб-источника."""
        from projects.views import feed

        payload = {
            "project_id": source.project_id,
            "interval": max(source.project.collector_web_interval, 60),
            "source_id": source.pk,
        }
        try:
            feed.enqueue_task(
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
            self._ensure_web_collector_schedule(source)

    def _ensure_web_collector_schedule(self, source: Source) -> None:
        """Гарантирует, что для проекта запланирована регулярная веб-задача."""

        project = source.project
        if not project.collector_enabled:
            return
        already_scheduled = WorkerTask.objects.filter(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            status__in=[WorkerTask.Status.QUEUED, WorkerTask.Status.RUNNING],
            payload__project_id=project.pk,
            payload__source_id__isnull=True,
        ).exists()
        if already_scheduled:
            return
        from projects.views import feed

        feed.enqueue_task(
            WorkerTask.Queue.COLLECTOR_WEB,
            payload={
                "project_id": project.pk,
                "interval": max(project.collector_web_interval, 60),
            },
            scheduled_for=timezone.now(),
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

    def get_success_url(self):
        """Возвращает URL для перенаправления после успешного обновления."""
        return reverse_lazy("projects:sources", kwargs={"pk": self.object.project_id})

    def form_valid(self, form):
        """Обрабатывает валидную форму, сохраняет источник и выводит сообщение."""
        form.save()
        messages.success(self.request, "Источник обновлён.")
        ensure_collector_tasks(self.object.project)
        return redirect(self.get_success_url())


@require_POST
def delete_source(request, project_pk: int, pk: int):
    """Удаляет источник и перенаправляет на список источников."""
    project = get_object_or_404(Project, pk=project_pk, owner=request.user)
    source = get_object_or_404(Source, pk=pk, project=project)
    source.delete()
    ensure_collector_tasks(project)
    messages.success(request, "Источник удалён.")
    return redirect("projects:sources", pk=project_pk)
