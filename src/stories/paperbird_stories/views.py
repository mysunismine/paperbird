"""Вьюхи для работы с сюжетами."""

from __future__ import annotations

import base64
from typing import Any, Sequence

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import DetailView, FormView, ListView
from django.template.response import TemplateResponse
from django.utils import timezone

from projects.models import Project
from projects.services.telethon_client import TelethonCredentialsMissingError
from stories.paperbird_stories.forms import (
    StoryCreateForm,
    StoryImageAttachForm,
    StoryImageDeleteForm,
    StoryImageGenerateForm,
    StoryPromptConfirmForm,
    StoryPublishForm,
    StoryRewriteForm,
)
from stories.paperbird_stories.models import Publication, RewritePreset, Story
from stories.paperbird_stories.services import (
    ImageGenerationFailed,
    PublicationFailed,
    RewriteFailed,
    StoryCreationError,
    StoryFactory,
    StoryPublisher,
    StoryRewriter,
    default_image_generator,
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

        comment = form.cleaned_data.get("editor_comment") or ""
        preset: RewritePreset | None = form.cleaned_data.get("preset")
        if request.POST.get("prompt_confirm") == "1":
            prompt_form = StoryPromptConfirmForm(request.POST, story=self.object)
            if not prompt_form.is_valid():
                context = self._prompt_context(prompt_form=prompt_form)
                return TemplateResponse(request, "stories/story_prompt_preview.html", context)

            comment = prompt_form.cleaned_data.get("editor_comment", "")
            preset = prompt_form.cleaned_data.get("preset")
            messages_override = [
                {"role": "system", "content": prompt_form.cleaned_data["prompt_system"]},
                {"role": "user", "content": prompt_form.cleaned_data["prompt_user"]},
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

        prompt_form = StoryPromptConfirmForm(
            story=self.object,
            initial={
                "prompt_system": self._extract_message(prompt_messages, "system"),
                "prompt_user": self._extract_message(prompt_messages, "user"),
                "preset": preset,
                "editor_comment": comment,
            },
        )
        context = self._prompt_context(prompt_form=prompt_form)
        return TemplateResponse(request, "stories/story_prompt_preview.html", context)

    def _prompt_context(
        self,
        *,
        prompt_form: StoryPromptConfirmForm,
    ) -> dict[str, Any]:
        return {
            "story": self.object,
            "editor_comment": prompt_form.editor_comment_value,
            "preset": prompt_form.selected_preset,
            "prompt_form": prompt_form,
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
        publish_at = form.cleaned_data.get("publish_at")
        try:
            publisher: StoryPublisher = default_publisher_for_story(self.object)
            publication = publisher.publish(
                self.object, target=target, scheduled_for=publish_at
            )
        except (PublicationFailed, TelethonCredentialsMissingError) as exc:
            messages.error(request, f"Публикация не удалась: {exc}")
        except Exception as exc:  # pragma: no cover
            messages.error(request, f"Ошибка публикации: {exc}")
        else:
            if publish_at:
                scheduled_time = timezone.localtime(publish_at)
                messages.success(
                    request,
                    "Публикация запланирована на "
                    f"{scheduled_time:%d.%m.%Y %H:%M}.",
                )
            elif publication.status == Publication.Status.PUBLISHED:
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


class StoryImageView(LoginRequiredMixin, DetailView):
    """Диалог генерации и прикрепления изображения."""

    model = Story
    template_name = "stories/story_image_modal.html"
    context_object_name = "story"

    def get_queryset(self):
        return Story.objects.filter(project__owner=self.request.user).select_related("project")

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        context = self.get_context_data(
            generate_form=self._generate_form_initial(),
            attach_form=None,
            delete_form=self._delete_form(),
        )
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        action = request.POST.get("action")
        if action == "generate":
            return self._handle_generate(request)
        if action == "attach":
            return self._handle_attach(request)
        if action == "remove":
            return self._handle_remove(request)
        messages.error(request, "Неизвестное действие")
        return redirect("stories:detail", pk=self.object.pk)

    def _handle_generate(self, request):
        form = StoryImageGenerateForm(request.POST)
        preview: dict[str, str] | None = None
        attach_form: StoryImageAttachForm | None = None
        if form.is_valid():
            prompt = form.cleaned_data["prompt"]
            generator = default_image_generator()
            try:
                result = generator.generate(prompt=prompt)
            except ImageGenerationFailed as exc:
                messages.error(request, f"Не удалось сгенерировать изображение: {exc}")
            except Exception as exc:  # pragma: no cover - непредвиденные ошибки
                messages.error(request, f"Ошибка генерации изображения: {exc}")
            else:
                encoded = base64.b64encode(result.data).decode("ascii")
                preview = {
                    "data": encoded,
                    "mime": result.mime_type,
                    "prompt": prompt,
                }
                attach_form = StoryImageAttachForm(
                    initial={
                        "prompt": prompt,
                        "image_data": encoded,
                        "mime_type": result.mime_type,
                    }
                )
        context = self.get_context_data(
            generate_form=form,
            preview=preview,
            attach_form=attach_form,
            delete_form=self._delete_form(),
        )
        return self.render_to_response(context)

    def _handle_attach(self, request):
        form = StoryImageAttachForm(request.POST)
        if form.is_valid():
            prompt = form.cleaned_data["prompt"]
            data = form.cleaned_data["image_data"]
            mime_type = form.cleaned_data["mime_type"]
            try:
                self.object.attach_image(prompt=prompt, data=data, mime_type=mime_type)
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                messages.success(request, "Изображение прикреплено к сюжету.")
                return redirect("stories:detail", pk=self.object.pk)

        encoded = request.POST.get("image_data", "")
        preview = None
        if encoded:
            preview = {
                "data": encoded,
                "mime": request.POST.get("mime_type", "image/png"),
                "prompt": request.POST.get("prompt", ""),
            }
        context = self.get_context_data(
            generate_form=self._generate_form_initial(prompt=preview["prompt"] if preview else None),
            attach_form=form,
            preview=preview,
            delete_form=self._delete_form(),
        )
        return self.render_to_response(context)

    def _handle_remove(self, request):
        form = StoryImageDeleteForm(request.POST)
        if form.is_valid():
            self.object.remove_image()
            messages.info(request, "Изображение удалено из сюжета.")
            return redirect("stories:detail", pk=self.object.pk)
        messages.error(request, "Не удалось удалить изображение")
        return redirect("stories:detail", pk=self.object.pk)

    def _generate_form_initial(self, prompt: str | None = None) -> StoryImageGenerateForm:
        initial_prompt = prompt or self.object.image_prompt or self.object.summary or self.object.title or ""
        return StoryImageGenerateForm(initial={"prompt": initial_prompt})

    def _delete_form(self) -> StoryImageDeleteForm | None:
        if self.object.image_file:
            return StoryImageDeleteForm()
        return None
