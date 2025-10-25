"""Вьюхи для работы с сюжетами."""

from __future__ import annotations

from typing import Any, Sequence

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import DetailView, FormView, ListView
from django.template.response import TemplateResponse

from projects.models import Project
from projects.services.telethon_client import TelethonCredentialsMissingError
from stories.paperbird_stories.forms import StoryCreateForm, StoryPublishForm, StoryRewriteForm
from stories.paperbird_stories.models import Publication, RewritePreset, Story
from stories.paperbird_stories.services import (
    PublicationFailed,
    RewriteFailed,
    StoryCreationError,
    StoryFactory,
    StoryPublisher,
    StoryRewriter,
    default_publisher_for_story,
    default_rewriter,
    make_prompt_messages,
)


class StoryListView(LoginRequiredMixin, ListView):
    """Список сюжетов пользователя."""

    model = Story
    template_name = "stories/story_list.html"
    context_object_name = "stories"

    def get_queryset(self):
        return Story.objects.filter(project__owner=self.request.user).select_related("project")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["projects"] = Project.objects.filter(owner=self.request.user).order_by("name")
        return context


class StoryCreateView(LoginRequiredMixin, FormView):
    """Создание нового сюжета из выбранных постов."""

    template_name = "stories/story_form.html"
    form_class = StoryCreateForm

    def get_form_kwargs(self) -> dict[str, Any]:
        kwargs = super().get_form_kwargs()
        project_id = self.request.GET.get("project") or self.request.POST.get("project")
        kwargs["user"] = self.request.user
        kwargs["project_id"] = int(project_id) if project_id else None
        return kwargs

    def form_valid(self, form: StoryCreateForm):
        project: Project = form.cleaned_data["project"]
        posts = form.cleaned_data["posts"]
        title = form.cleaned_data.get("title", "")
        editor_comment = form.cleaned_data.get("editor_comment", "")
        try:
            story = StoryFactory(project=project).create(
                post_ids=list(posts.values_list("id", flat=True)),
                title=title,
                editor_comment=editor_comment,
            )
        except StoryCreationError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, "Сюжет создан. Запустите рерайт, чтобы получить текст.")
        self.created_story_pk = story.pk
        return super().form_valid(form)

    def get_success_url(self) -> str:  # noqa: D401 - нужен доступ к созданному pk
        return reverse("stories:detail", kwargs={"pk": self.created_story_pk})


class StoryDetailView(LoginRequiredMixin, DetailView):
    """Просмотр сюжета, запуск рерайта и публикации."""

    model = Story
    template_name = "stories/story_detail.html"
    context_object_name = "story"

    def get_queryset(self):
        return (
            Story.objects.filter(project__owner=self.request.user)
            .select_related("project")
            .prefetch_related("story_posts__post", "rewrite_tasks", "publications")
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context.setdefault(
            "rewrite_form",
            StoryRewriteForm(
                story=self.object,
                initial={"editor_comment": self.object.editor_comment},
            ),
        )
        context.setdefault("publish_form", StoryPublishForm())
        context["publications"] = self.object.publications.order_by("-created_at")
        context["last_task"] = self.object.rewrite_tasks.first()
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        action = request.POST.get("action")
        if action == "rewrite":
            return self._handle_rewrite(request)
        if action == "publish":
            return self._handle_publish(request)
        messages.error(request, "Неизвестное действие")
        return redirect(self.get_success_url())

    def _handle_rewrite(self, request):
        form = StoryRewriteForm(request.POST, story=self.object)
        if not form.is_valid():
            messages.error(request, "Проверьте поля формы рерайта")
            return redirect(self.get_success_url())

        comment = form.cleaned_data.get("editor_comment")
        preset: RewritePreset | None = form.cleaned_data.get("preset")
        if request.POST.get("prompt_confirm") == "1":
            prompt_system = (request.POST.get("prompt_system") or "").strip()
            prompt_user = (request.POST.get("prompt_user") or "").strip()
            if not prompt_system or not prompt_user:
                messages.error(request, "Заполните обе части промпта")
                context = self._prompt_context(
                    comment=comment or "",
                    preset=preset,
                    prompt_system=prompt_system,
                    prompt_user=prompt_user,
                )
                return TemplateResponse(request, "stories/story_prompt_preview.html", context)

            messages_override = [
                {"role": "system", "content": prompt_system},
                {"role": "user", "content": prompt_user},
            ]
            try:
                rewriter: StoryRewriter = default_rewriter()
                rewriter.rewrite(
                    self.object,
                    editor_comment=comment,
                    preset=preset,
                    messages_override=messages_override,
                )
            except RewriteFailed as exc:
                messages.error(request, f"Рерайт не удался: {exc}")
            except Exception as exc:  # pragma: no cover - подсказка пользователю
                messages.error(request, f"Не удалось запустить рерайт: {exc}")
            else:
                messages.success(request, "Рерайт выполнен. Проверьте сгенерированный текст.")
            return redirect(self.get_success_url())

        try:
            prompt_messages, _ = make_prompt_messages(
                self.object,
                editor_comment=comment,
                preset=preset,
            )
        except RewriteFailed as exc:
            messages.error(request, f"Не удалось подготовить промпт: {exc}")
            return redirect(self.get_success_url())

        context = self._prompt_context(
            comment=comment or "",
            preset=preset,
            prompt_system=self._extract_message(prompt_messages, "system"),
            prompt_user=self._extract_message(prompt_messages, "user"),
        )
        return TemplateResponse(request, "stories/story_prompt_preview.html", context)

    def _prompt_context(
        self,
        *,
        comment: str,
        preset: RewritePreset | None,
        prompt_system: str,
        prompt_user: str,
    ) -> dict[str, Any]:
        return {
            "story": self.object,
            "editor_comment": comment,
            "preset": preset,
            "prompt_system": prompt_system,
            "prompt_user": prompt_user,
        }

    @staticmethod
    def _extract_message(messages: Sequence[dict[str, str]], role: str) -> str:
        for message in messages:
            if message.get("role") == role:
                return message.get("content", "")
        return ""

    def _handle_publish(self, request):
        form = StoryPublishForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Укажите канал или чат для публикации")
            return redirect(self.get_success_url())

        if self.object.status not in {Story.Status.READY, Story.Status.PUBLISHED}:
            messages.error(request, "Сюжет ещё не готов к публикации")
            return redirect(self.get_success_url())

        target = form.cleaned_data["target"]
        try:
            publisher: StoryPublisher = default_publisher_for_story(self.object)
            publication = publisher.publish(self.object, target=target)
        except (PublicationFailed, TelethonCredentialsMissingError) as exc:
            messages.error(request, f"Публикация не удалась: {exc}")
        except Exception as exc:  # pragma: no cover
            messages.error(request, f"Ошибка публикации: {exc}")
        else:
            if publication.status == Publication.Status.PUBLISHED:
                messages.success(request, "Сюжет опубликован в Telegram.")
            else:
                messages.info(request, "Публикация запланирована и будет выполнена позже.")
        return redirect(self.get_success_url())

    def get_success_url(self) -> str:
        return reverse("stories:detail", kwargs={"pk": self.object.pk})


class PublicationListView(LoginRequiredMixin, ListView):
    """Отображает публикации пользователя."""

    model = Publication
    template_name = "stories/publication_list.html"
    context_object_name = "publications"
    paginate_by = 25

    def get_queryset(self):
        return (
            Publication.objects.filter(story__project__owner=self.request.user)
            .select_related("story", "story__project")
            .order_by("-created_at")
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        context["projects"] = (
            Project.objects.filter(owner=self.request.user)
            .order_by("name")
        )
        return context
