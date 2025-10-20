"""Представления приложения projects."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_datetime
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView, TemplateView

from projects.models import Post, Project
from projects.services.post_filters import (
    PostFilterOptions,
    apply_post_filters,
    collect_keyword_hits,
    summarize_keyword_hits,
)
from .forms import ProjectCreateForm


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
        return super().dispatch(request, *args, **kwargs)

    def _build_options(self) -> PostFilterOptions:
        query = self.request.GET
        statuses = set(query.getlist("statuses"))
        include_keywords = set(_split_csv(query.get("include")))
        exclude_keywords = set(_split_csv(query.get("exclude")))
        source_ids = {
            int(value)
            for value in query.getlist("sources")
            if value.isdigit()
        }
        options = PostFilterOptions(
            statuses=statuses,
            search=query.get("search", ""),
            include_keywords=include_keywords,
            exclude_keywords=exclude_keywords,
            date_from=_parse_datetime(query.get("date_from")),
            date_to=_parse_datetime(query.get("date_to")),
            has_media=_parse_bool(query.get("has_media")),
            source_ids=source_ids,
        )
        return options

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        options = self._build_options()
        queryset = (
            Post.objects.filter(project=self.project)
            .select_related("source")
            .order_by("-posted_at")
        )
        filtered = apply_post_filters(queryset, options)
        posts = list(filtered[:100])
        highlight_keywords = list(options.include_keywords) + options.search_terms
        keyword_hits = collect_keyword_hits(posts, highlight_keywords)
        keyword_summary = summarize_keyword_hits(posts, highlight_keywords)
        for post in posts:
            post.keyword_hits = keyword_hits.get(post.id, [])
        context.update(
            {
                "project": self.project,
                "posts": posts,
                "options": options,
                "keyword_summary": keyword_summary,
                "status_choices": Post.Status.choices,
                "sources": self.project.sources.filter(is_active=True).order_by("title"),
            }
        )
        return context


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
