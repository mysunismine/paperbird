"""Views for listing and creating projects."""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import TemplateView
from django.views.generic import CreateView, ListView

from core.constants import IMAGE_PROVIDER_SETTINGS
from projects.models import Post, Project, Source
from projects.services.project_import import ProjectImportError, import_project_payload

from ..forms import ProjectCreateForm


class ProjectListView(LoginRequiredMixin, ListView):
    """Список проектов пользователя с краткой статистикой."""

    model = Project
    template_name = "projects/project_list.html"
    context_object_name = "projects"

    def get_queryset(self):
        """Возвращает queryset проектов текущего пользователя с аннотациями."""
        return (
            Project.objects.accessible_by(self.request.user)
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


class ProjectImportView(LoginRequiredMixin, View):
    """Импорт проекта из JSON/YAML файла."""

    template_name = "projects/project_import.html"

    def get(self, request, *args, **kwargs):
        return render(request, self.template_name)

    def post(self, request, *args, **kwargs):
        file = request.FILES.get("project_file")
        payload = (request.POST.get("project_payload") or "").strip()
        if file:
            payload = file.read().decode("utf-8", "replace").strip()
        if not payload:
            messages.error(request, "Загрузите файл или вставьте JSON/YAML проекта.")
            return redirect("projects:import")

        data = self._parse_payload(payload)
        if not isinstance(data, dict):
            messages.error(request, "Некорректный формат файла проекта.")
            return redirect("projects:import")

        try:
            result = import_project_payload(owner=request.user, payload=data)
        except ProjectImportError as exc:
            messages.error(request, f"Не удалось импортировать проект: {exc}")
            return redirect("projects:import")

        messages.success(
            request,
            f"Проект «{result.project.name}» импортирован. "
            f"Источников: {result.sources_created}, пресетов: {result.presets_imported}.",
        )
        return redirect("projects:settings", pk=result.project.pk)

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


class ProjectChatFeedView(LoginRequiredMixin, TemplateView):
    """Отдельный экран просмотра Telegram-чатов."""

    template_name = "projects/chat_feed.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.project = get_object_or_404(
            Project.objects.accessible_by(request.user), pk=kwargs["pk"]
        )
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        chat_sources = list(
            Source.objects.filter(
                project=self.project,
                type=Source.Type.TELEGRAM,
                telegram_kind=Source.TelegramKind.CHAT,
            )
            .annotate(messages_count=Count("posts"))
            .order_by("title", "id")
        )
        chat_id = self.request.GET.get("chat")
        active_chat = None
        if chat_id and chat_id.isdigit():
            active_chat = next(
                (chat for chat in chat_sources if chat.id == int(chat_id)),
                None,
            )
        if not active_chat and chat_sources:
            active_chat = chat_sources[0]

        q = (self.request.GET.get("q") or "").strip()
        limit_raw = self.request.GET.get("limit") or "50"
        limit = 50
        if str(limit_raw).isdigit():
            limit = max(10, min(200, int(limit_raw)))

        chat_messages = []
        if active_chat:
            posts_qs = Post.objects.filter(project=self.project, source=active_chat)
            if q:
                posts_qs = posts_qs.filter(message__icontains=q)
            posts = list(posts_qs.order_by("-posted_at")[:limit])
            posts.reverse()
            for post in posts:
                raw = post.raw if isinstance(post.raw, dict) else {}
                author = _render_author(raw)
                reply_to = _render_reply_preview(raw)
                post.author = author
                post.reply_to = reply_to
                chat_messages.append(post)
        thread_groups = _group_threads(chat_messages)
        context.update(
            {
                "project": self.project,
                "chat_sources": chat_sources,
                "active_chat": active_chat,
                "chat_messages": chat_messages,
                "thread_groups": thread_groups,
                "chat_limit": limit,
                "q": q,
            }
        )
        return context


def _render_author(raw: dict) -> str:
    if not isinstance(raw, dict):
        return "Участник"
    for key in ("sender_name", "from_name", "author", "sender"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("sender_id", "from_id"):
        value = raw.get(key)
        if value:
            return f"User {value}"
    return "Участник"


def _render_reply_preview(raw: dict) -> str | None:
    if not isinstance(raw, dict):
        return None
    reply_to = raw.get("reply_to")
    if isinstance(reply_to, dict):
        msg_id = reply_to.get("reply_to_msg_id") or reply_to.get("reply_to_message_id")
        if msg_id:
            return f"Сообщение #{msg_id}"
    return None


def _group_threads(messages: list[Post]) -> list[dict]:
    by_telegram_id = {post.telegram_id: post for post in messages if post.telegram_id}
    replies_map: dict[int, list[Post]] = {}
    roots: list[Post] = []

    for post in messages:
        reply_to_id = _extract_reply_to_id(post)
        if reply_to_id and reply_to_id in by_telegram_id:
            replies_map.setdefault(reply_to_id, []).append(post)
        else:
            roots.append(post)

    def sort_key(item: Post):
        return item.posted_at or 0

    roots.sort(key=sort_key)
    grouped: list[dict] = []
    for root in roots:
        replies = replies_map.get(root.telegram_id or 0, [])
        replies.sort(key=sort_key)
        grouped.append({"root": root, "replies": replies})
    return grouped


def _extract_reply_to_id(post: Post) -> int | None:
    raw = post.raw if isinstance(post.raw, dict) else {}
    reply_to = raw.get("reply_to")
    if isinstance(reply_to, dict):
        msg_id = reply_to.get("reply_to_msg_id") or reply_to.get("reply_to_message_id")
        if isinstance(msg_id, int):
            return msg_id
        if isinstance(msg_id, str) and msg_id.isdigit():
            return int(msg_id)
    return None
