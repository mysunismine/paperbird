"""Views related to story details, rewrites, and prompt previews."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.views.generic import DetailView, TemplateView

from projects.services.telethon_client import TelethonCredentialsMissingError
from stories.paperbird_stories.forms import (
    StoryContentForm,
    StoryPromptConfirmForm,
    StoryPublishForm,
    StoryRewriteForm,
)
from stories.paperbird_stories.models import Publication, RewritePreset, Story
from stories.paperbird_stories.services import (
    PublicationFailed,
    RewriteFailed,
    StoryPublisher,
    StoryRewriter,
    default_publisher_for_story,
    default_rewriter,
    make_prompt_messages,
)


class StoryPromptSnapshotView(LoginRequiredMixin, TemplateView):
    """Отображает последний промпт рерайта."""

    template_name = "stories/story_prompt_preview.html"

    def get(self, request, pk: int, *args, **kwargs):
        story = get_object_or_404(
            Story.objects.select_related("project"),
            pk=pk,
            project__owner=request.user,
        )
        if not story.prompt_snapshot:
            messages.info(request, "У сюжета ещё нет сохранённого промпта.")
            return redirect("stories:detail", pk=story.pk)
        prompt_messages = list(story.prompt_snapshot)
        prompt_form = StoryPromptConfirmForm(
            story=story,
            initial={
                "prompt_system": StoryDetailView._extract_message(prompt_messages, "system"),
                "prompt_user": StoryDetailView._extract_message(prompt_messages, "user"),
                "preset": story.last_rewrite_preset,
                "editor_comment": story.editor_comment or "",
            },
        )
        context = {
            "story": story,
            "prompt_form": prompt_form,
            "editor_comment": story.editor_comment or "",
            "preset": story.last_rewrite_preset,
            "preview_source": "latest",
        }
        return self.render_to_response(context)


class StoryDetailView(LoginRequiredMixin, DetailView):
    """Просмотр сюжета, запуск рерайта и публикации."""

    model = Story
    template_name = "stories/story_detail.html"
    context_object_name = "story"

    def get_queryset(self):
        return (
            Story.objects.filter(project__owner=self.request.user)
            .select_related("project")
            .prefetch_related(
                "story_posts__post",
                "story_posts__post__source",
                "rewrite_tasks",
                "publications",
            )
        )

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        requested_step = kwargs.pop("active_step", None)
        context.setdefault(
            "rewrite_form",
            StoryRewriteForm(
                story=self.object,
                initial={"editor_comment": self.object.editor_comment},
            ),
        )
        context.setdefault("content_form", StoryContentForm(instance=self.object))
        publish_initial = {}
        if self.object.project.publish_target:
            publish_initial["target"] = self.object.project.publish_target
        context.setdefault("publish_form", StoryPublishForm(initial=publish_initial))
        context["publish_blocked"] = not bool(self.object.project.publish_target)
        context["project_settings_url"] = reverse(
            "projects:settings", args=[self.object.project_id]
        )
        context["publications"] = self.object.publications.order_by("-created_at")
        context["last_task"] = self.object.rewrite_tasks.first()
        context["story_posts"] = self.object.story_posts.select_related("post", "post__source")
        context["can_edit_content"] = self.object.status in {
            Story.Status.READY,
            Story.Status.PUBLISHED,
        }
        context["media_url"] = settings.MEDIA_URL
        rewrite_form: StoryRewriteForm = context["rewrite_form"]
        context["prompt_preview"] = self._build_prompt_preview(
            editor_comment=self._form_editor_comment(rewrite_form),
            preset=self._form_preset(rewrite_form),
        )
        context["rewrite_meta"] = {
            "model_code": self.object.project.rewrite_model,
            "model_label": self.object.project.get_rewrite_model_display(),
            "preset": self._form_preset(rewrite_form) or self.object.last_rewrite_preset,
        }
        context["active_step"] = self._derive_step(requested_step)
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        action = request.POST.get("action")
        if action == "rewrite":
            return self._handle_rewrite(request)
        if action == "publish":
            return self._handle_publish(request)
        if action == "save":
            return self._handle_save(request)
            messages.error(request, "Неизвестное действие")
        return redirect(self.get_success_url())

    def _handle_rewrite(self, request):
        form = StoryRewriteForm(request.POST, story=self.object)
        if not form.is_valid():
            context = self.get_context_data(rewrite_form=form, active_step="prompt")
            self._add_rewrite_message(messages.ERROR, "Проверьте поля формы рерайта")
            return self.render_to_response(context, status=400)

        comment = form.cleaned_data.get("editor_comment") or ""
        preset: RewritePreset | None = form.cleaned_data.get("preset")
        if request.POST.get("prompt_confirm") == "1":
            prompt_form = StoryPromptConfirmForm(request.POST, story=self.object)
            if not prompt_form.is_valid():
                context = self._prompt_context(prompt_form=prompt_form)
                self._add_rewrite_message(messages.ERROR, "Проверьте значения промпта")
                return TemplateResponse(request, "stories/story_prompt_preview.html", context)

            comment = prompt_form.cleaned_data.get("editor_comment", "")
            preset = prompt_form.cleaned_data.get("preset")
            messages_override = [
                {"role": "system", "content": prompt_form.cleaned_data["prompt_system"]},
                {"role": "user", "content": prompt_form.cleaned_data["prompt_user"]},
            ]
            try:
                rewriter: StoryRewriter = default_rewriter(project=self.object.project)
                rewriter.rewrite(
                    self.object,
                    editor_comment=comment,
                    preset=preset,
                    messages_override=messages_override,
                )
            except RewriteFailed as exc:
                self._add_rewrite_message(messages.ERROR, f"Рерайт не удался: {exc}")
            except Exception as exc:  # pragma: no cover - подсказка пользователю
                self._add_rewrite_message(messages.ERROR, f"Не удалось запустить рерайт: {exc}")
            else:
                self._add_rewrite_message(
                    messages.SUCCESS,
                    "Рерайт выполнен. Проверьте сгенерированный текст.",
                )
            return redirect(self._build_success_url(step="rewrite"))

        if request.POST.get("preview") == "1":
            if (
                self.object.prompt_snapshot
                and comment == (self.object.editor_comment or "")
                and (
                    (preset is None and self.object.last_rewrite_preset is None)
                    or (
                        preset is not None
                        and self.object.last_rewrite_preset is not None
                        and preset.pk == self.object.last_rewrite_preset.pk
                    )
                )
            ):
                prompt_messages = list(self.object.prompt_snapshot)
            else:
                try:
                    prompt_messages, _ = make_prompt_messages(
                        self.object,
                        editor_comment=comment,
                        preset=preset,
                    )
                except RewriteFailed as exc:
                    self._add_rewrite_message(
                        messages.ERROR,
                        f"Не удалось подготовить промпт: {exc}",
                    )
                    return redirect(self._build_success_url(step="prompt"))

            prompt_form = StoryPromptConfirmForm(
                story=self.object,
                initial={
                    "prompt_system": self._extract_message(prompt_messages, "system"),
                    "prompt_user": self._extract_message(prompt_messages, "user"),
                    "preset": preset,
                    "editor_comment": comment,
                },
            )
            context = self._prompt_context(prompt_form=prompt_form, source="preview")
            return TemplateResponse(request, "stories/story_prompt_preview.html", context)

        try:
            rewriter: StoryRewriter = default_rewriter(project=self.object.project)
            rewriter.rewrite(
                self.object,
                editor_comment=comment,
                preset=preset,
            )
        except RewriteFailed as exc:
            self._add_rewrite_message(messages.ERROR, f"Рерайт не удался: {exc}")
        except Exception as exc:  # pragma: no cover - подсказка пользователю
            self._add_rewrite_message(messages.ERROR, f"Не удалось запустить рерайт: {exc}")
        else:
            self._add_rewrite_message(
                messages.SUCCESS,
                "Рерайт выполнен. Проверьте сгенерированный текст.",
            )
        return redirect(self._build_success_url(step="rewrite"))

    def _handle_save(self, request):
        form = StoryContentForm(request.POST, instance=self.object)
        if form.is_valid():
            form.save()
            messages.success(request, "Текст сюжета обновлён.")
            return redirect(self._build_success_url(step="rewrite"))
        context = self.get_context_data(content_form=form)
        return self.render_to_response(context, status=400)

    def _prompt_context(
        self,
        *,
        prompt_form: StoryPromptConfirmForm,
        source: str = "draft",
    ) -> dict[str, Any]:
        return {
            "story": self.object,
            "editor_comment": prompt_form.editor_comment_value,
            "preset": prompt_form.selected_preset,
            "prompt_form": prompt_form,
            "preview_source": source,
        }

    @staticmethod
    def _extract_message(messages: Sequence[dict[str, str]], role: str) -> str:
        for message in messages:
            if message.get("role") == role:
                return message.get("content", "")
        return ""

    def _handle_publish(self, request):
        if not self.object.project.publish_target:
            messages.error(
                request,
                "Укажите целевой канал в настройках проекта, прежде чем публиковать сюжет.",
            )
            return redirect(self._build_success_url(step="publish"))
        form = StoryPublishForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Укажите канал или чат для публикации")
            return redirect(self._build_success_url(step="publish"))

        if self.object.status not in {Story.Status.READY, Story.Status.PUBLISHED}:
            messages.error(request, "Сюжет ещё не готов к публикации")
            return redirect(self._build_success_url(step="publish"))

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
            return redirect("stories:publications")
        return redirect(self._build_success_url(step="publish"))

    def get_success_url(self) -> str:
        return reverse("stories:detail", kwargs={"pk": self.object.pk})

    def _add_rewrite_message(self, level: int, text: str) -> None:
        messages.add_message(
            self.request,
            level,
            text,
            extra_tags="inline rewrite",
        )

    def _derive_step(self, requested_step: str | None) -> str:
        available = {"sources", "prompt", "rewrite", "publish"}
        if requested_step in available:
            return requested_step
        if self.object.status in {Story.Status.READY, Story.Status.PUBLISHED} or self.object.body:
            return "rewrite"
        if self.object.status == Story.Status.REWRITING:
            return "prompt"
        return "sources"

    def _build_prompt_preview(
        self,
        *,
        editor_comment: str | None = None,
        preset: RewritePreset | None = None,
    ) -> dict[str, str | None]:
        selected_preset = preset or self.object.last_rewrite_preset
        try:
            messages_list, _ = make_prompt_messages(
                self.object,
                editor_comment=editor_comment,
                preset=selected_preset,
            )
        except RewriteFailed as exc:
            snapshot = list(self.object.prompt_snapshot)
            if snapshot:
                return {
                    "system": self._extract_message(snapshot, "system"),
                    "user": self._extract_message(snapshot, "user"),
                    "error": str(exc),
                }
            return {"system": "", "user": "", "error": str(exc)}
        return {
            "system": self._extract_message(messages_list, "system"),
            "user": self._extract_message(messages_list, "user"),
            "error": None,
        }

    def _form_editor_comment(self, form: StoryRewriteForm) -> str:
        if hasattr(form, "cleaned_data") and "editor_comment" in form.cleaned_data:
            return form.cleaned_data.get("editor_comment", "")
        if form.is_bound:
            return form.data.get("editor_comment", "")
        return form.initial.get("editor_comment") or self.object.editor_comment or ""

    def _form_preset(self, form: StoryRewriteForm) -> RewritePreset | None:
        if hasattr(form, "cleaned_data") and "preset" in form.cleaned_data:
            return form.cleaned_data.get("preset")
        if form.is_bound:
            raw = form.data.get("preset")
        else:
            raw = form.initial.get("preset")
        if not raw:
            return None
        try:
            return form.fields["preset"].queryset.get(pk=raw)
        except (RewritePreset.DoesNotExist, ValueError, TypeError):
            return None

    def _build_success_url(self, *, step: str | None = None) -> str:
        base = self.get_success_url()
        if not step:
            return base
        return f"{base}?step={step}"
