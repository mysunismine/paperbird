"""Views related to story details, rewrites, and prompt previews."""

from __future__ import annotations

import mimetypes
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
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
        publish_initial.setdefault("media_order", "after")
        context.setdefault("publish_form", StoryPublishForm(initial=publish_initial))
        context["publish_blocked"] = not bool(self.object.project.publish_target)
        context["project_settings_url"] = reverse(
            "projects:settings", args=[self.object.project_id]
        )
        context["publications"] = self.object.publications.order_by("-created_at")
        context["last_task"] = self.object.rewrite_tasks.first()
        story_posts = list(self.object.story_posts.select_related("post", "post__source"))
        for story_post in story_posts:
            story_post.can_attach = self._can_attach_media(story_post.post)
        context["story_posts"] = story_posts
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
        if action == "attach_media":
            return self._handle_attach_media(request)
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
        media_order = form.cleaned_data.get("media_order") or "after"
        publish_at = form.cleaned_data.get("publish_at")
        try:
            publisher: StoryPublisher = default_publisher_for_story(self.object)
            publication = publisher.publish(
                self.object,
                target=target,
                scheduled_for=publish_at,
                media_order=media_order,
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

    def _handle_attach_media(self, request):
        """Прикрепляет медиа поста к сюжету для публикации."""
        selected_ids = request.POST.getlist("media_post_id")
        if not selected_ids:
            messages.error(request, "Выберите медиа, чтобы прикрепить его к сюжету.")
            return redirect(self._build_success_url(step="rewrite"))

        posts_qs = self.object.ordered_posts().select_related("source")
        posts = posts_qs.filter(id__in=[int(value) for value in selected_ids if value.isdigit()])
        for post in posts:
            media_info = self._find_post_media(post, allow_download=True)
            if not media_info:
                continue
            try:
                data = media_info["path"].read_bytes()
            except OSError:
                continue
            try:
                self.object.attach_image(
                    prompt="",
                    data=data,
                    mime_type=media_info["mime"],
                )
            except Exception as exc:
                messages.error(request, f"Не удалось прикрепить изображение: {exc}")
                return redirect(self._build_success_url(step="rewrite"))
            messages.success(request, "Медиа прикреплено: будет отправлено после текста.")
            return redirect(self._build_success_url(step="rewrite"))

        messages.error(request, "Не удалось найти локальный файл среди выбранных медиа.")
        return redirect(self._build_success_url(step="rewrite"))

    def _find_post_media(self, post, *, allow_download: bool = False):
        """Возвращает информацию о локальном медиафайле поста, если он доступен."""
        path_value = self._candidate_media_path(post)
        if not path_value and allow_download:
            external = self._first_external_image(post)
            if external:
                return self._download_external_media(post, external)
        if not path_value:
            return None

        root = Path(settings.MEDIA_ROOT or ".").resolve()
        media_path = Path(path_value)
        if not media_path.is_absolute():
            media_path = root / media_path
        try:
            resolved = media_path.resolve()
        except (OSError, RuntimeError):
            return None

        if root and not str(resolved).startswith(str(root)):
            return None
        if not resolved.exists() or not resolved.is_file():
            return None

        mime, _ = mimetypes.guess_type(str(resolved))
        if mime and not mime.startswith("image/"):
            return None

        return {
            "path": resolved,
            "mime": mime or "image/jpeg",
        }

    def _candidate_media_path(self, post) -> str | None:
        """Определяет путь к медиа поста или извлекает его из локального URL."""
        media_prefix = (settings.MEDIA_URL or "").rstrip("/")
        path_value = (post.media_path or "").strip()
        if path_value:
            normalized = self._normalize_media_path(path_value, media_prefix)
            if normalized:
                return normalized

        if not media_prefix:
            return None

        for item in getattr(post, "media_items", []):
            url = ""
            if isinstance(item, dict):
                url = (item.get("url") or "").strip()
            elif isinstance(item, str):
                url = item.strip()
            if not url:
                continue
            relative = self._relative_media_path(url, media_prefix)
            if relative:
                return relative
        return None

    def _can_attach_media(self, post) -> bool:
        if self._find_post_media(post, allow_download=False):
            return True
        return bool(self._first_external_image(post))

    @staticmethod
    def _first_external_image(post) -> str | None:
        for item in getattr(post, "media_items", []):
            url = item.get("url") if isinstance(item, dict) else item
            if not url:
                continue
            parsed = urlparse(url)
            if parsed.scheme in {"http", "https"}:
                return url
        return None

    def _download_external_media(self, post, url: str):
        timeout = float(getattr(settings, "OPENAI_IMAGE_TIMEOUT", 60))
        try:
            response = httpx.get(url, timeout=timeout)
        except Exception:
            return None
        if response.status_code != 200 or not response.content:
            return None

        content_type = response.headers.get("content-type") or ""
        mime = content_type.split(";")[0].strip() if content_type else ""
        if mime and not mime.startswith("image/"):
            return None

        extension = (
            mimetypes.guess_extension(mime)
            or Path(urlparse(url).path).suffix
            or ".jpg"
        )
        filename = f"{post.id}_{uuid.uuid4().hex}{extension}"
        relative_path = (
            Path("uploads")
            / "media"
            / str(post.project_id or "0")
            / str(post.source_id or "0")
            / filename
        )
        absolute_root = Path(settings.MEDIA_ROOT or ".")
        absolute_path = absolute_root / relative_path
        absolute_path.parent.mkdir(parents=True, exist_ok=True)
        absolute_path.write_bytes(response.content)

        post.media_path = relative_path.as_posix()
        if mime:
            post.media_type = mime
        post.save(update_fields=["media_path", "media_type", "updated_at"])

        return {
            "path": absolute_path,
            "mime": mime or "image/jpeg",
        }

    @staticmethod
    def _relative_media_path(url: str, media_prefix: str) -> str | None:
        return StoryDetailView._normalize_media_path(url, media_prefix)

    @staticmethod
    def _normalize_media_path(path_value: str, media_prefix: str) -> str | None:
        raw = (path_value or "").strip()
        if not raw:
            return None
        parsed = urlparse(raw)
        prefix = (media_prefix or "").rstrip("/")
        path_part = parsed.path if (parsed.scheme or parsed.netloc) else raw
        if prefix and path_part.startswith(prefix):
            trimmed = path_part[len(prefix) :].lstrip("/")
            return trimmed or None
        if parsed.scheme or parsed.netloc:
            return None
        return raw or None

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
