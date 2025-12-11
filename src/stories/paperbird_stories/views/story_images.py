"""Views for generating and attaching images to stories."""

from __future__ import annotations

import base64
import hashlib
import mimetypes
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import DetailView

from projects.models import Post
from stories.paperbird_stories.forms import (
    StoryImageAttachForm,
    StoryImageDeleteForm,
    StoryImageGenerateForm,
    StoryImageUploadForm,
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
            upload_form=StoryImageUploadForm(),
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
        if action == "upload":
            return self._handle_upload(request)
        messages.error(request, "Неизвестное действие")
        return redirect("stories:detail", pk=self.object.pk)

    def _handle_upload(self, request):
        form = StoryImageUploadForm(request.POST, request.FILES)
        if form.is_valid():
            image_file = form.cleaned_data["image_file"]
            try:
                self.object.attach_image(
                    prompt="",
                    data=image_file.read(),
                    mime_type=image_file.content_type,
                )
            except ValueError as exc:
                messages.error(request, f"Не удалось прикрепить изображение: {exc}")
            else:
                messages.success(request, "Изображение загружено и прикреплено к сюжету.")
                return redirect("stories:image", pk=self.object.pk)
        
        context = self.get_context_data(
            generate_form=self._generate_form_initial(),
            attach_form=None,
            delete_form=self._delete_form(),
            upload_form=form,
            source_media=self._source_media(),
        )
        return self.render_to_response(context)

    def _handle_generate(self, request):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        form = StoryImageGenerateForm(request.POST)
        
        if not form.is_valid():
            if is_ajax:
                return JsonResponse({"status": "error", "errors": form.errors}, status=400)
            # Fallback for non-AJAX
            return self.render_to_response(self.get_context_data(generate_form=form))

        prompt = form.cleaned_data["prompt"]
        model = form.cleaned_data["model"]
        size = form.cleaned_data["size"]
        safe_size = normalize_image_size(size)
        quality = normalize_image_quality(form.cleaned_data["quality"])
        generator = default_image_generator(model=model)

        try:
            result = generator.generate(
                prompt=prompt, model=model, size=safe_size, quality=quality
            )
        except ImageGenerationFailed as exc:
            if is_ajax:
                return JsonResponse({"status": "error", "message": str(exc)}, status=500)
            messages.error(request, f"Не удалось сгенерировать изображение: {exc}")
        except Exception as exc:
            if is_ajax:
                return JsonResponse({"status": "error", "message": str(exc)}, status=500)
            messages.error(request, f"Ошибка генерации изображения: {exc}")
        else:
            encoded = base64.b64encode(result.data).decode("ascii")
            preview_data = {
                "data": encoded,
                "mime": result.mime_type,
                "prompt": prompt,
                "model": model,
                "size": safe_size,
                "quality": quality,
            }
            if is_ajax:
                return JsonResponse({"status": "success", "preview": preview_data})
            
            # Fallback for non-AJAX
            if safe_size != size:
                messages.info(request, "Размер изображения автоматически скорректирован.")
            messages.success(request, "Изображение успешно сгенерировано.")
            attach_form = StoryImageAttachForm(initial=preview_data)
            context = self.get_context_data(
                generate_form=form,
                preview=preview_data,
                attach_form=attach_form,
                delete_form=self._delete_form(),
                upload_form=StoryImageUploadForm(),
                source_media=self._source_media(),
            )
            return self.render_to_response(context)

        # Error fallback for non-AJAX
        return self.render_to_response(self.get_context_data(generate_form=form))

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
        media = self._find_post_media(post, allow_download=True)
        if not media:
            messages.error(request, "У поста нет доступного медиафайла.")
            return redirect("stories:image", pk=self.object.pk)

        try:
            data = media["path"].read_bytes()
        except OSError:
            messages.error(request, "Не удалось прочитать файл медиа.")
            return redirect("stories:image", pk=self.object.pk)

        self.object.attach_image(prompt="", data=data, mime_type=media["mime"])
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
        seen_paths: set[str] = set()
        seen_hashes: set[str] = set()
        posts = self.object.ordered_posts().select_related("source")
        for post in posts:
            candidate = self._find_post_media(post)
            if not candidate:
                continue
            path_key = str(candidate["path"].resolve())
            if path_key in seen_paths:
                continue
            try:
                file_hash = hashlib.sha256(candidate["path"].read_bytes()).hexdigest()
            except OSError:
                continue
            if file_hash in seen_hashes:
                continue
            seen_paths.add(path_key)
            seen_hashes.add(file_hash)
            media.append(candidate)
        return media

    def _find_post_media(self, post: Post, *, allow_download: bool = False):
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

        media_prefix = (settings.MEDIA_URL or "").rstrip("/")
        relative_path = None
        try:
            relative_path = resolved.relative_to(root).as_posix()
        except ValueError:
            pass
        url = None
        if media_prefix and relative_path:
            url = f"{media_prefix.rstrip('/')}/{relative_path.lstrip('/')}"
        if not url:
            url = resolved.as_posix()

        return {
            "post": post,
            "path": resolved,
            "mime": mime or "image/jpeg",
            "url": url,
            "file_name": resolved.name,
        }

    def _candidate_media_path(self, post: Post) -> str | None:
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

    @staticmethod
    def _relative_media_path(url: str, media_prefix: str) -> str | None:
        return StoryImageView._normalize_media_path(url, media_prefix)

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

    def _first_external_image(self, post: Post) -> str | None:
        for item in getattr(post, "media_items", []):
            url = item.get("url") if isinstance(item, dict) else item
            if not url:
                continue
            parsed = urlparse(url)
            if parsed.scheme in {"http", "https"}:
                return url
        return None

    def _download_external_media(self, post: Post, url: str):
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
            "post": post,
            "path": absolute_path,
            "mime": mime or "image/jpeg",
            "url": post.media_url,
            "file_name": absolute_path.name,
        }
