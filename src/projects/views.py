"""Представления приложения projects."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.generic import (
    CreateView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from core.models import WorkerTask
from core.services.worker import enqueue_task
from projects.models import Post, Project, Source, WebPreset
from projects.services.prompt_config import (
    PROMPT_SECTION_HINTS,
    PROMPT_SECTION_ORDER,
    ensure_prompt_config,
    render_prompt,
    tokens_help,
)
from projects.services.post_filters import (
    PostFilterOptions,
    apply_post_filters,
    collect_keyword_hits,
)
from .forms import (
    ProjectCreateForm,
    ProjectPromptConfigForm,
    SourceCreateForm,
    SourceUpdateForm,
)


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = parse_datetime(value)
    return parsed


class ProjectListView(LoginRequiredMixin, ListView):
    """Список проектов пользователя с краткой статистикой."""

    model = Project
    template_name = "projects/project_list.html"
    context_object_name = "projects"

    def get_queryset(self):
        return (
            Project.objects.filter(owner=self.request.user)
            .annotate(
                posts_total=Count("posts", distinct=True),
                stories_total=Count("stories", distinct=True),
            )
            .order_by("name")
        )


class ProjectPostListView(LoginRequiredMixin, TemplateView):
    """Отображает ленту постов проекта с базовыми фильтрами."""

    template_name = "projects/post_list.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(
            Project, pk=kwargs["pk"], owner=request.user
        )
        self._projects = list(
            Project.objects.filter(owner=request.user).order_by("name")
        )
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        if action == "collector_start":
            return self._start_collector()
        if action == "collector_stop":
            return self._stop_collector()
        messages.error(request, "Неизвестное действие")
        return redirect(request.path)

    def _build_options(self) -> PostFilterOptions:
        query = self.request.GET
        statuses = set(query.getlist("statuses"))
        source_ids = {
            int(value)
            for value in query.getlist("sources")
            if value.isdigit()
        }
        options = PostFilterOptions(
            statuses=statuses,
            search=query.get("search", ""),
            include_keywords=set(),
            exclude_keywords=set(),
            date_from=_parse_datetime(query.get("date_from")),
            date_to=_parse_datetime(query.get("date_to")),
            has_media=_parse_bool(query.get("has_media")),
            source_ids=source_ids,
            languages=set(),
        )
        return options

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        options = self._build_options()
        queryset = (
            Post.objects.filter(project=self.project)
            .select_related("source")
            .order_by("-collected_at", "-posted_at")
        )
        filtered = apply_post_filters(queryset, options)
        posts = list(filtered[:100])
        highlight_keywords = options.search_terms
        keyword_hits = collect_keyword_hits(posts, highlight_keywords)
        for post in posts:
            post.keyword_hits = keyword_hits.get(post.id, [])
        context.update(
            {
                "project": self.project,
                "projects": self._projects,
                "posts": posts,
                "options": options,
                "status_choices": Post.Status.choices,
                "total_posts": queryset.count(),
                "last_refreshed": timezone.now(),
                "collector": self._collector_context(),
            }
        )
        return context

    def render_to_response(self, context, **response_kwargs):
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return render(
                self.request,
                "projects/partials/post_table.html",
                context,
                **response_kwargs,
            )
        return super().render_to_response(context, **response_kwargs)

    def _collector_context(self) -> dict[str, Any]:
        next_task = (
            WorkerTask.objects.filter(
                queue=WorkerTask.Queue.COLLECTOR,
                status=WorkerTask.Status.QUEUED,
                payload__project_id=self.project.id,
            )
            .order_by("available_at")
            .first()
        )
        next_web_task = (
            WorkerTask.objects.filter(
                queue=WorkerTask.Queue.COLLECTOR_WEB,
                status=WorkerTask.Status.QUEUED,
                payload__project_id=self.project.id,
            )
            .order_by("available_at")
            .first()
        )
        has_telegram_sources = self._has_telegram_sources()
        has_web_sources = self._has_web_sources()
        return {
            "enabled": self.project.collector_enabled,
            "telegram_interval": self.project.collector_telegram_interval,
            "web_interval": self.project.collector_web_interval,
            "last_run": self.project.collector_last_run,
            "next_run": next_task.available_at if next_task else None,
            "next_web_run": next_web_task.available_at if next_web_task else None,
            "has_credentials": self.request.user.has_telethon_credentials,
            "requires_credentials": has_telegram_sources,
            "has_web_sources": has_web_sources,
            "has_telegram_sources": has_telegram_sources,
        }

    def _start_collector(self):
        project = self.project
        requires_telethon = self._has_telegram_sources()
        if requires_telethon and not self.request.user.has_telethon_credentials:
            messages.error(
                self.request,
                "Сначала добавьте Telethon-ключи в профиль, чтобы запустить сборщик.",
            )
            return redirect(self.request.path)
        if not requires_telethon and not self._has_web_sources():
            messages.error(
                self.request,
                "Добавьте хотя бы один источник, прежде чем запускать сборщик.",
            )
            return redirect(self.request.path)

        if project.collector_enabled:
            messages.info(self.request, "Сборщик уже запущен для этого проекта.")
        else:
            project.collector_enabled = True
            project.save(update_fields=["collector_enabled", "updated_at"])
            self._ensure_collector_task(delay=0)
            messages.success(
                self.request,
                "Сборщик запущен. Посты будут обновляться автоматически.",
            )
        return redirect(self.request.path)

    def _stop_collector(self):
        project = self.project
        if not project.collector_enabled:
            messages.info(self.request, "Сборщик уже остановлен.")
            return redirect(self.request.path)

        project.collector_enabled = False
        project.save(update_fields=["collector_enabled", "updated_at"])
        now = timezone.now()
        WorkerTask.objects.filter(
            queue__in=[WorkerTask.Queue.COLLECTOR, WorkerTask.Queue.COLLECTOR_WEB],
            payload__project_id=project.id,
            status=WorkerTask.Status.QUEUED,
        ).update(
            status=WorkerTask.Status.CANCELLED,
            finished_at=now,
            updated_at=now,
        )
        messages.warning(
            self.request,
            "Сборщик остановлен. Новые посты не будут собираться автоматически.",
        )
        return redirect(self.request.path)

    def _ensure_collector_task(self, *, delay: int) -> None:
        if self._has_telegram_sources():
            self._schedule_queue(
                WorkerTask.Queue.COLLECTOR,
                delay=delay,
                interval=self.project.collector_telegram_interval,
            )
        if self._has_web_sources():
            self._schedule_queue(
                WorkerTask.Queue.COLLECTOR_WEB,
                delay=delay,
                interval=self.project.collector_web_interval,
            )

    def _schedule_queue(self, queue: str, *, delay: int, interval: int) -> None:
        exists = WorkerTask.objects.filter(
            queue=queue,
            payload__project_id=self.project.id,
            status__in=[WorkerTask.Status.QUEUED, WorkerTask.Status.RUNNING],
        ).exists()
        if exists:
            return
        scheduled_for = timezone.now() + timedelta(seconds=max(delay, 0))
        enqueue_task(
            queue,
            payload={
                "project_id": self.project.id,
                "interval": interval,
            },
            scheduled_for=scheduled_for,
        )

    def _has_telegram_sources(self) -> bool:
        return self.project.sources.filter(is_active=True, type=Source.Type.TELEGRAM).exists()

    def _has_web_sources(self) -> bool:
        return self.project.sources.filter(
            is_active=True,
            type=Source.Type.WEB,
            web_preset__status=WebPreset.Status.ACTIVE,
        ).exists()


class ProjectCreateView(LoginRequiredMixin, CreateView):
    """Веб-форма для создания нового проекта."""

    form_class = ProjectCreateForm
    template_name = "projects/project_form.html"
    success_url = reverse_lazy("projects:list")

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["owner"] = self.request.user
        return kwargs

    def form_valid(self, form):  # type: ignore[override]
        response = super().form_valid(form)
        messages.success(
            self.request,
            f"Проект «{self.object.name}» создан.",
        )
        return response


class ProjectSettingsView(LoginRequiredMixin, UpdateView):
    """Настройки существующего проекта."""

    model = Project
    form_class = ProjectCreateForm
    template_name = "projects/project_settings.html"
    context_object_name = "project"
    success_url = reverse_lazy("projects:list")

    def get_queryset(self):
        return Project.objects.filter(owner=self.request.user)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["owner"] = self.request.user
        return kwargs

    def form_valid(self, form):  # type: ignore[override]
        response = super().form_valid(form)
        messages.success(
            self.request,
            f"Настройки проекта «{self.object.name}» обновлены.",
        )
        return response


class ProjectPromptsView(LoginRequiredMixin, FormView):
    """Отдельная страница управления основным промтом проекта."""

    template_name = "projects/project_prompts.html"
    form_class = ProjectPromptConfigForm

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(
            Project, pk=kwargs["pk"], owner=request.user
        )
        self.config = ensure_prompt_config(self.project)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.config
        return kwargs

    def form_valid(self, form):  # type: ignore[override]
        form.save()
        messages.success(
            self.request,
            f"Промт проекта «{self.project.name}» сохранён.",
        )
        return redirect("projects:prompts", pk=self.project.pk)

    def form_invalid(self, form):
        messages.error(
            self.request,
            "Исправьте ошибки в шаблоне промта и попробуйте снова.",
        )
        return super().form_invalid(form)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
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
        return sections


class ProjectPromptExportView(LoginRequiredMixin, View):
    """Формирует предпросмотр промтов в текстовом виде."""

    def get(self, request, *args, **kwargs):
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
        rendered = render_prompt(
            project=project,
            posts=[],
            preview_mode=True,
            editor_comment="",
        )
        return rendered.full_text


class ProjectSourcesView(LoginRequiredMixin, TemplateView):
    """Список источников проекта с действиями управления."""

    template_name = "projects/project_sources.html"

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(
            Project,
            pk=kwargs["pk"],
            owner=request.user,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "project": self.project,
                "sources": self.project.sources.order_by("type", "title", "telegram_id"),
                "create_url": reverse_lazy("projects:source-create", kwargs={"project_pk": self.project.pk}),
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        if request.POST.get("action") == "delete":
            return self._handle_delete(request)
        messages.error(request, "Неизвестное действие.")
        return redirect("projects:sources", pk=self.project.pk)

    def _handle_delete(self, request):
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
        self.project = get_object_or_404(
            Project,
            pk=kwargs["project_pk"],
            owner=request.user,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.project
        return kwargs

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["project"] = self.project
        return context

    def form_valid(self, form):  # type: ignore[override]
        source = form.save()
        if source.type == Source.Type.WEB:
            self._schedule_web_source_collection(source)
        else:
            messages.success(self.request, "Источник добавлен к проекту.")
        return redirect("projects:sources", pk=source.project_id)

    def form_invalid(self, form):
        messages.error(self.request, "Исправьте ошибки формы и попробуйте снова.")
        return super().form_invalid(form)

    def _schedule_web_source_collection(self, source: Source) -> None:
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
        return Source.objects.filter(
            project__owner=self.request.user,
            project_id=self.kwargs["project_pk"],
        ).select_related("project")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["project"] = self.object.project
        return context

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        kwargs["project"] = self.get_object().project
        return kwargs

    def form_valid(self, form):  # type: ignore[override]
        source = form.save()
        messages.success(self.request, "Источник обновлён.")
        return redirect("projects:sources", pk=source.project_id)


class ProjectCollectorQueueView(LoginRequiredMixin, TemplateView):
    """Отображает очередь задач коллектора для проекта."""

    template_name = "projects/project_queue.html"
    queues = [WorkerTask.Queue.COLLECTOR, WorkerTask.Queue.COLLECTOR_WEB]

    def dispatch(self, request, *args, **kwargs):
        self.project = get_object_or_404(
            Project,
            pk=kwargs["pk"],
            owner=request.user,
        )
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
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
        if task.status == WorkerTask.Status.RUNNING:
            messages.error(self.request, "Сначала остановите задачу, затем запустите снова.")
            return
        enqueue_task(
            task.queue,
            payload=task.payload,
            scheduled_for=timezone.now(),
        )
        messages.success(self.request, "Новая задача поставлена в очередь.")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        tasks = (
            WorkerTask.objects.filter(queue__in=self.queues, payload__project_id=self.project.id)
            .order_by("-available_at", "-id")
        )
        context.update(
            {
                "project": self.project,
                "tasks": tasks,
            }
        )
        return context
