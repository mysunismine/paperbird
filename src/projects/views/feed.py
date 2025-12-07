"""Views for project feed and post details."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.generic import TemplateView

from core.models import WorkerTask
from core.services.worker import enqueue_task
from projects.models import Post, Project, Source, WebPreset
from projects.services.post_filters import (
    PostFilterOptions,
    apply_post_filters,
    collect_keyword_hits,
)


def _parse_bool(value: str | None) -> bool | None:
    """Парсит строковое значение в булево."""
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return None


def _parse_datetime(value: str | None):
    """Парсит строковое значение в datetime."""
    if not value:
        return None
    return parse_datetime(value)


class ProjectPostListView(LoginRequiredMixin, TemplateView):
    """Отображает ленту постов проекта с базовыми фильтрами."""

    template_name = "projects/post_list.html"

    def dispatch(self, request, *args, **kwargs):
        """Проверяет права доступа к проекту и инициализирует его."""
        self.project = get_object_or_404(
            Project, pk=kwargs["pk"], owner=request.user
        )
        self._projects = list(
            Project.objects.filter(owner=request.user).order_by("name")
        )
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        """Обрабатывает POST-запросы для управления сборщиком."""
        action = request.POST.get("action")
        if action == "collector_start":
            return self._start_collector()
        if action == "collector_stop":
            return self._stop_collector()
        messages.error(request, "Неизвестное действие")
        return redirect(request.path)

    def _build_options(self) -> PostFilterOptions:
        """Строит объект PostFilterOptions из параметров GET-запроса."""
        query = self.request.GET
        statuses = set(query.getlist("statuses"))
        source_ids = {
            int(value)
            for value in query.getlist("sources")
            if value.isdigit()
        }
        return PostFilterOptions(
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

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Формирует контекст для шаблона."""
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
        """Рендерит ответ для AJAX-запросов."""
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return render(
                self.request,
                "projects/partials/post_table.html",
                context,
                **response_kwargs,
            )
        return super().render_to_response(context, **response_kwargs)

    def _collector_context(self) -> dict[str, Any]:
        """Формирует контекст для отображения состояния сборщика."""
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
        """Запускает сборщик для проекта."""
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
        """Останавливает сборщик для проекта."""
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
        """Гарантирует, что задача сборщика поставлена в очередь."""
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
        """Планирует задачу для указанной очереди."""
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
        """Проверяет наличие активных Telegram-источников в проекте."""
        return self.project.sources.filter(is_active=True, type=Source.Type.TELEGRAM).exists()

    def _has_web_sources(self) -> bool:
        """Проверяет наличие активных веб-источников в проекте."""
        return self.project.sources.filter(
            is_active=True,
            type=Source.Type.WEB,
            web_preset__status=WebPreset.Status.ACTIVE,
        ).exists()


class ProjectPostDetailView(LoginRequiredMixin, TemplateView):
    """Отображает полный текст и медиа конкретного поста."""

    template_name = "projects/post_detail.html"

    def dispatch(self, request, *args, **kwargs):
        """Проверяет права доступа к проекту и посту."""
        self.project = get_object_or_404(
            Project, pk=kwargs["project_pk"], owner=request.user
        )
        self.post = get_object_or_404(
            Post.objects.select_related("source", "project"),
            pk=kwargs["post_pk"],
            project=self.project,
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        """Формирует контекст для шаблона."""
        context = super().get_context_data(**kwargs)
        context.update(
            {
                "project": self.project,
                "post": self.post,
                "media_items": self.post.media_items,
            }
        )
        return context
