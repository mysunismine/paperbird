"""Views for generating and attaching images to stories."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
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

from core.constants import IMAGE_PROVIDER_SETTINGS
from projects.models import Post
from stories.paperbird_stories.forms import (
    StoryImageAttachForm,
    StoryImageDeleteForm,
    StoryImageGenerateForm,
    StoryImageUploadForm,
)
from stories.paperbird_stories.models import Story, StoryImage
from stories.paperbird_stories.services import (
    ImageGenerationFailed,
    default_image_generator,
    normalize_image_quality,
    normalize_image_size,
)
from stories.paperbird_stories.services.helpers import _looks_like_gemini_model
from stories.paperbird_stories.services.image_prompt import (
    ImagePromptSuggestionFailed,
    suggest_image_prompt,
)


class StoryImageView(LoginRequiredMixin, DetailView):
    """Диалог генерации и прикрепления изображения."""

    model = Story
    template_name = "stories/story_image_modal.html"
    context_object_name = "story"

    def get_queryset(self):
        return Story.objects.filter(project__owner=self.request.user).select_related("project")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        provider_settings = json.loads(json.dumps(IMAGE_PROVIDER_SETTINGS))
        gemini_aspect_ratio = getattr(settings, "GEMINI_IMAGE_ASPECT_RATIO", "").strip()
        gemini_image_size = getattr(settings, "GEMINI_IMAGE_SIZE", "").strip()
        for model_key in ("gemini-2.5-flash-image", "gemini-3-pro-image-preview"):
            if model_key not in provider_settings:
                continue
            if gemini_aspect_ratio:
                provider_settings[model_key]["default_aspect_ratio"] = gemini_aspect_ratio
            if gemini_image_size and model_key == "gemini-3-pro-image-preview":
                provider_settings[model_key]["default_image_size"] = gemini_image_size
        context["image_provider_settings"] = json.dumps(provider_settings)
        return context

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
        if action == "suggest_prompt":
            return self._handle_suggest_prompt(request)
        messages.error(request, "Неизвестное действие")
        return redirect("stories:detail", pk=self.object.pk)

    def _handle_upload(self, request):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        form = StoryImageUploadForm(request.POST, request.FILES)

        if not form.is_valid():
            if is_ajax:
                return JsonResponse({"status": "error", "errors": form.errors}, status=400)
            return self.render_to_response(self.get_context_data(upload_form=form))

        image_file = form.cleaned_data["image_file"]
        prompt = Path(image_file.name).stem
        mime_type = image_file.content_type

        try:
            image_data = image_file.read()
        except Exception as exc:
            if is_ajax:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": f"Не удалось прочитать файл: {exc}",
                    },
                    status=500,
                )
            messages.error(request, f"Не удалось прочитать файл: {exc}")
            return self.render_to_response(self.get_context_data(upload_form=form))

        if is_ajax:
            encoded = base64.b64encode(image_data).decode("ascii")
            preview_data = {
                "data": encoded,
                "mime": mime_type,
                "prompt": prompt,
            }
            preview_token = self._store_preview(request, preview_data)
            preview_data["preview_token"] = preview_token
            return JsonResponse({"status": "success", "preview": preview_data})

        # Fallback for non-AJAX
        try:
            self.object.attach_image(
                prompt=prompt,
                data=image_data,
                mime_type=mime_type,
                source_kind=StoryImage.SourceKind.UPLOAD,
            )
        except ValueError as exc:
            messages.error(request, f"Не удалось прикрепить изображение: {exc}")
        else:
            messages.success(request, "Изображение загружено и прикреплено к сюжету.")
        return redirect("stories:image", pk=self.object.pk)

    def _handle_suggest_prompt(self, request):
        if request.headers.get("X-Requested-With") != "XMLHttpRequest":
            messages.error(request, "Запрос рекомендованного промпта доступен только через AJAX.")
            return redirect("stories:image", pk=self.object.pk)
        try:
            prompt = suggest_image_prompt(self.object)
        except ImagePromptSuggestionFailed as exc:
            return JsonResponse(
                {"status": "error", "message": str(exc)},
                status=500,
            )
        return JsonResponse({"status": "success", "prompt": prompt})

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
        quality = normalize_image_quality(form.cleaned_data["quality"])
        aspect_ratio = (form.cleaned_data.get("aspect_ratio") or "").strip()
        image_size = (form.cleaned_data.get("image_size") or "").strip()
        generator = default_image_generator(model=model)
        use_gemini = _looks_like_gemini_model(model)
        if use_gemini:
            safe_size = ""
            quality = ""
        else:
            safe_size = normalize_image_size(size)

        try:
            result = generator.generate(
                prompt=prompt,
                model=model,
                size=safe_size,
                quality=quality,
                aspect_ratio=aspect_ratio if use_gemini else None,
                image_size=image_size if use_gemini else None,
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
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
            }
            preview_token = self._store_preview(request, preview_data)
            preview_data["preview_token"] = preview_token
            if is_ajax:
                return JsonResponse({"status": "success", "preview": preview_data})
            
            # Fallback for non-AJAX
            if not use_gemini and safe_size != size:
                messages.info(request, "Размер изображения автоматически скорректирован.")
            messages.success(request, "Изображение успешно сгенерировано.")
            attach_initial = {
                "prompt": preview_data["prompt"],
                "mime_type": preview_data["mime"],
                "model": preview_data["model"],
                "size": preview_data["size"],
                "quality": preview_data["quality"],
                "aspect_ratio": preview_data["aspect_ratio"],
                "image_size": preview_data["image_size"],
                "preview_token": preview_token,
            }
            attach_form = StoryImageAttachForm(initial=attach_initial)
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
            token = (form.cleaned_data.get("preview_token") or "").strip()
            if not data and token:
                preview = self._load_preview(request, token)
                if not preview:
                    messages.error(
                        request,
                        "Предпросмотр устарел. Сгенерируйте изображение заново.",
                    )
                    return redirect("stories:image", pk=self.object.pk)
                data = preview["data"]
                mime_type = preview["mime"]
            if not data:
                messages.error(request, "Не удалось прикрепить изображение: отсутствуют данные.")
                return redirect("stories:image", pk=self.object.pk)
            try:
                self.object.attach_image(
                    prompt=prompt,
                    data=data,
                    mime_type=mime_type,
                    source_kind=StoryImage.SourceKind.GENERATED,
                )
            except ValueError as exc:
                form.add_error(None, str(exc))
            else:
                self._clear_preview(request)
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
                "aspect_ratio": request.POST.get("aspect_ratio", ""),
                "image_size": request.POST.get("image_size", ""),
            }
        else:
            token = (request.POST.get("preview_token") or "").strip()
            if token:
                stored = self._load_preview(request, token)
                if stored:
                    preview = {
                        "data": base64.b64encode(stored["data"]).decode("ascii"),
                        "mime": stored["mime"],
                        "prompt": request.POST.get("prompt", ""),
                        "model": request.POST.get("model", ""),
                        "size": normalize_image_size(request.POST.get("size", "")),
                        "quality": normalize_image_quality(request.POST.get("quality", "")),
                        "preview_token": token,
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

    def _preview_session_key(self) -> str:
        return f"story_image_preview:{self.object.pk}"

    def _store_preview(self, request, preview: dict[str, Any]) -> str:
        token = uuid.uuid4().hex
        request.session[self._preview_session_key()] = {
            "token": token,
            "data": preview.get("data", ""),
            "mime": preview.get("mime", "image/png"),
            "prompt": preview.get("prompt", ""),
        }
        request.session.modified = True
        return token

    def _load_preview(self, request, token: str) -> dict[str, Any] | None:
        stored = request.session.get(self._preview_session_key())
        if not isinstance(stored, dict):
            return None
        if stored.get("token") != token:
            return None
        data = stored.get("data") or ""
        if not data:
            return None
        try:
            decoded = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError):
            return None
        if not decoded:
            return None
        return {
            "data": decoded,
            "mime": stored.get("mime") or "image/png",
            "prompt": stored.get("prompt") or "",
        }

    def _clear_preview(self, request) -> None:
        key = self._preview_session_key()
        if key in request.session:
            request.session.pop(key, None)
            request.session.modified = True

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

        self.object.attach_image(
            prompt="",
            data=data,
            mime_type=media["mime"],
            source_kind=StoryImage.SourceKind.SOURCE,
        )
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
        aspect_ratio: str | None = None,
        image_size: str | None = None,
    ) -> StoryImageGenerateForm:
        project = self.object.project
        initial_prompt = (
            prompt
            or self.object.image_prompt
            or self.object.body
            or self.object.title
            or ""
        )
        selected_model = (model or project.image_model or "").strip()
        default_image_size = ""
        if selected_model == "gemini-3-pro-image-preview":
            default_image_size = getattr(settings, "GEMINI_IMAGE_SIZE", "")
        initial = {
            "prompt": initial_prompt,
            "model": model or project.image_model,
            "size": normalize_image_size(size or project.image_size),
            "quality": normalize_image_quality(quality or project.image_quality),
            "aspect_ratio": aspect_ratio or getattr(settings, "GEMINI_IMAGE_ASPECT_RATIO", ""),
            "image_size": image_size or default_image_size,
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
