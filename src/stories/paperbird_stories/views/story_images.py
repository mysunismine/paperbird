"""Views for generating and attaching images to stories."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import DetailView

from projects.models import Post
from stories.paperbird_stories.forms import (
    StoryImageAttachForm,
    StoryImageDeleteForm,
    StoryImageGenerateForm,
)
from stories.paperbird_stories.models import Story
from stories.paperbird_stories.services import (
    ImageGenerationFailed,
    default_image_generator,
    normalize_image_quality,
    normalize_image_size,
)


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
            source_media=self._source_media(),
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
        if action == "attach_source":
            return self._handle_attach_source(request)
        messages.error(request, "Неизвестное действие")
        return redirect("stories:detail", pk=self.object.pk)

    def _handle_generate(self, request):
        form = StoryImageGenerateForm(request.POST)
        preview: dict[str, str] | None = None
        attach_form: StoryImageAttachForm | None = None
        if form.is_valid():
            prompt = form.cleaned_data["prompt"]
            model = form.cleaned_data["model"]
            size = form.cleaned_data["size"]
            safe_size = normalize_image_size(size)
            quality = normalize_image_quality(form.cleaned_data["quality"])
            generator = default_image_generator(model=model)
            try:
                result = generator.generate(
                    prompt=prompt,
                    model=model,
                    size=safe_size,
                    quality=quality,
                )
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
                    "model": model,
                    "size": safe_size,
                    "quality": quality,
                }
                if safe_size != size:
                    messages.info(
                        request,
                        (
                            "Размер изображения автоматически скорректирован до "
                            "поддерживаемого значения, чтобы его можно было без ошибок "
                            "загрузить в Paperbird."
                        ),
                    )
                messages.success(request, "Изображение успешно сгенерировано.")
                attach_form = StoryImageAttachForm(
                    initial={
                        "prompt": prompt,
                        "image_data": encoded,
                        "mime_type": result.mime_type,
                        "model": model,
                        "size": safe_size,
                        "quality": quality,
                    }
                )
        context = self.get_context_data(
            generate_form=form,
            preview=preview,
            attach_form=attach_form,
            delete_form=self._delete_form(),
            source_media=self._source_media(),
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
            safe_size = normalize_image_size(request.POST.get("size", ""))
            safe_quality = normalize_image_quality(request.POST.get("quality", ""))
            preview = {
                "data": encoded,
                "mime": request.POST.get("mime_type", "image/png"),
                "prompt": request.POST.get("prompt", ""),
                "model": request.POST.get("model", ""),
                "size": safe_size,
                "quality": safe_quality,
            }
        context = self.get_context_data(
            generate_form=self._generate_form_initial(
                prompt=preview["prompt"] if preview else None,
                model=preview["model"] if preview else None,
                size=preview["size"] if preview else None,
                quality=preview["quality"] if preview else None,
            ),
            attach_form=form,
            preview=preview,
            delete_form=self._delete_form(),
            source_media=self._source_media(),
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

    def _handle_attach_source(self, request):
        post_id = request.POST.get("post_id")
        if not post_id or not str(post_id).isdigit():
            messages.error(request, "Некорректный идентификатор поста.")
            return redirect("stories:image", pk=self.object.pk)

        post = get_object_or_404(
            self.object.ordered_posts().select_related("source"),
            pk=int(post_id),
        )
        media = self._find_post_media(post)
        if not media:
            messages.error(request, "У поста нет доступного медиафайла.")
            return redirect("stories:image", pk=self.object.pk)

        try:
            data = media["path"].read_bytes()
        except OSError:
            messages.error(request, "Не удалось прочитать файл медиа.")
            return redirect("stories:image", pk=self.object.pk)

        prompt = f"Оригинальное изображение из поста #{post.id}"
        self.object.attach_image(prompt=prompt, data=data, mime_type=media["mime"])
        messages.success(
            request,
            f"Изображение из поста «{post}» прикреплено к сюжету.",
        )
        return redirect("stories:image", pk=self.object.pk)

    def _generate_form_initial(
        self,
        *,
        prompt: str | None = None,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
    ) -> StoryImageGenerateForm:
        project = self.object.project
        initial_prompt = (
            prompt
            or self.object.image_prompt
            or self.object.body
            or self.object.title
            or ""
        )
        initial = {
            "prompt": initial_prompt,
            "model": model or project.image_model,
            "size": normalize_image_size(size or project.image_size),
            "quality": normalize_image_quality(quality or project.image_quality),
        }
        return StoryImageGenerateForm(initial=initial)

    def _delete_form(self) -> StoryImageDeleteForm | None:
        if self.object.image_file:
            return StoryImageDeleteForm()
        return None

    def _source_media(self) -> list[dict[str, Any]]:
        media: list[dict[str, Any]] = []
        posts = self.object.ordered_posts().select_related("source")
        for post in posts:
            candidate = self._find_post_media(post)
            if candidate:
                media.append(candidate)
        return media

    def _find_post_media(self, post: Post):
        path_value = (post.media_path or "").strip()
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
            "post": post,
            "path": resolved,
            "mime": mime or "image/jpeg",
            "url": post.media_url,
            "file_name": resolved.name,
        }
