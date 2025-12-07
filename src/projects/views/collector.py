"""Views for inspecting and updating collector queues."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.views.generic import TemplateView

from core.models import WorkerTask
from projects.models import Project


class ProjectCollectorQueueView(LoginRequiredMixin, TemplateView):
    """Отображает очередь задач коллектора для проекта."""

    template_name = "projects/project_queue.html"
    partial_template_name = "projects/partials/task_list.html"
    queues = [WorkerTask.Queue.COLLECTOR, WorkerTask.Queue.COLLECTOR_WEB]

    def get_template_names(self):
        """Возвращает частичный шаблон при AJAX-запросе."""
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return [self.partial_template_name]
        return [self.template_name]

    def dispatch(self, request, *args, **kwargs):
        """Проверяет права доступа к проекту и инициализирует его."""
        self.project = get_object_or_404(
            Project,
            pk=kwargs["pk"],
            owner=request.user,
        )
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """Обрабатывает POST-запросы для управления задачами в очереди."""
        action = request.POST.get("action")
        task_id = request.POST.get("task_id")
        if not task_id or not task_id.isdigit():
            messages.error(request, "Некорректный идентификатор задачи.")
            return redirect("projects:queue", pk=self.project.pk)
        task = WorkerTask.objects.filter(
            pk=int(task_id),
            queue__in=self.queues,
            payload__project_id=self.project.id,
        ).first()
        if not task:
            messages.error(request, "Задача не найдена или относится к другому проекту.")
            return redirect("projects:queue", pk=self.project.pk)

        if action == "cancel_task":
            self._cancel_task(task)
        elif action == "retry_task":
            self._retry_task(task)
        else:
            messages.error(request, "Неизвестное действие.")
        return redirect("projects:queue", pk=self.project.pk)

    def _cancel_task(self, task: WorkerTask) -> None:
        """Отменяет задачу в очереди."""
        if task.status not in {WorkerTask.Status.QUEUED, WorkerTask.Status.RUNNING}:
            messages.info(self.request, "Задачу уже нельзя отменить.")
            return
        now = timezone.now()
        WorkerTask.objects.filter(pk=task.pk).update(
            status=WorkerTask.Status.CANCELLED,
            finished_at=now,
            locked_at=None,
            locked_by="",
            updated_at=now,
        )
        messages.success(self.request, "Задача отменена.")

    def _retry_task(self, task: WorkerTask) -> None:
        """Повторно ставит задачу в очередь."""
        if task.status == WorkerTask.Status.RUNNING:
            messages.error(self.request, "Сначала остановите задачу, затем запустите снова.")
            return
        # Импортируем здесь, чтобы позволить тестам патчить projects.views.feed.enqueue_task
        from projects.views import feed

        feed.enqueue_task(
            task.queue,
            payload=task.payload,
            scheduled_for=timezone.now(),
        )
        messages.success(self.request, "Новая задача поставлена в очередь.")

    def get_context_data(self, **kwargs):
        """Формирует контекст для шаблона, включая агрегированную статистику."""
        context = super().get_context_data(**kwargs)
        tasks_qs = WorkerTask.objects.filter(
            queue__in=self.queues, payload__project_id=self.project.id
        )

        # Aggregate stats for the dashboard
        stats = tasks_qs.aggregate(
            failed=Count("id", filter=Q(status=WorkerTask.Status.FAILED)),
            running=Count("id", filter=Q(status=WorkerTask.Status.RUNNING)),
            queued=Count("id", filter=Q(status=WorkerTask.Status.QUEUED)),
            total=Count("id"),
        )
        overall_status = "ok" if stats["failed"] == 0 else "error"

        # Sort tasks to show failures first, then running, then queued
        tasks = tasks_qs.annotate(
            status_order=Case(
                When(status=WorkerTask.Status.FAILED, then=Value(1)),
                When(status=WorkerTask.Status.RUNNING, then=Value(2)),
                When(status=WorkerTask.Status.QUEUED, then=Value(3)),
                default=Value(4),
                output_field=IntegerField(),
            )
        ).order_by("status_order", "-available_at")

        context.update(
            {
                "project": self.project,
                "tasks": tasks,
                "stats": stats,
                "overall_status": overall_status,
                "last_refreshed": timezone.now(),
            }
        )
        return context
