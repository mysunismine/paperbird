"""Views for media library and media studio."""

from __future__ import annotations

import base64
import mimetypes
import uuid
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import TemplateView

from core.constants import IMAGE_PROVIDER_SETTINGS
from media_library.forms import MediaAssetUploadForm
from media_library.models import MediaAsset
from media_library.services import resolve_post_media
from projects.models import Post, Project
from stories.paperbird_stories.forms import StoryImageAttachForm, StoryImageGenerateForm
from stories.paperbird_stories.models import Story
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


class MediaLibraryView(LoginRequiredMixin, TemplateView):
    """Список медиа проекта и загрузка файлов в медиабиблиотеку."""

    template_name = "media_library/library.html"

    def get(self, request, *args, **kwargs):
        context = self.get_context_data()
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        project = self._get_project(request)
        if not project:
            messages.error(request, "Проект не найден.")
            return redirect("media_library:library")

        if action == "upload":
            return self._handle_upload(request, project)
        if action == "import_post":
            return self._handle_import_post(request, project)
        messages.error(request, "Неизвестное действие.")
        return redirect(f"{reverse('media_library:library')}?project={project.pk}")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        projects = Project.objects.accessible_by(self.request.user).order_by("name")
        project = self._get_project(self.request, projects=projects)
        assets = MediaAsset.objects.none()
        post_media: list[dict[str, Any]] = []
        if project:
            assets = MediaAsset.objects.filter(project=project).select_related("created_by")
            post_media = self._post_media_candidates(project)
        context.update(
            {
                "projects": projects,
                "project": project,
                "assets": assets,
                "post_media": post_media,
                "upload_form": MediaAssetUploadForm(),
            }
        )
        return context

    def _get_project(self, request, *, projects=None):
        project_id = request.GET.get("project") or request.POST.get("project")
        if projects is None:
            projects = Project.objects.accessible_by(request.user).order_by("name")
        if project_id and str(project_id).isdigit():
            project = projects.filter(pk=int(project_id)).first()
            if project:
                return project
        return projects.first()

    def _handle_upload(self, request, project: Project):
        form = MediaAssetUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            messages.error(request, "Не удалось загрузить файл.")
            return redirect(f"{request.path}?project={project.pk}")

        image_file = form.cleaned_data["image_file"]
        title = Path(image_file.name).stem
        MediaAsset.objects.create(
            project=project,
            story=None,
            created_by=request.user,
            image_file=image_file,
            title=title,
            source_kind=MediaAsset.SourceKind.UPLOAD,
        )
        messages.success(request, "Изображение добавлено в медиабиблиотеку.")
        return redirect(f"{request.path}?project={project.pk}")

    def _handle_import_post(self, request, project: Project):
        post_id = request.POST.get("post_id")
        if not post_id or not str(post_id).isdigit():
            messages.error(request, "Некорректный идентификатор поста.")
            return redirect(f"{request.path}?project={project.pk}")

        post = get_object_or_404(Post, pk=int(post_id), project=project)
        media = resolve_post_media(post)
        if not media:
            messages.error(request, "У поста нет доступного изображения.")
            return redirect(f"{request.path}?project={project.pk}")

        try:
            data = media["path"].read_bytes()
        except OSError:
            messages.error(request, "Не удалось прочитать файл медиа.")
            return redirect(f"{request.path}?project={project.pk}")

        filename = f"post_{post.pk}_{uuid.uuid4().hex}{media['path'].suffix}"
        asset = MediaAsset(
            project=project,
            story=None,
            created_by=request.user,
            title=f"Пост #{post.pk}",
            source_kind=MediaAsset.SourceKind.IMPORTED,
        )
        asset.image_file.save(filename, ContentFile(data), save=True)
        messages.success(request, "Медиа из поста добавлено в медиабиблиотеку.")
        return redirect(f"{request.path}?project={project.pk}")

    def _post_media_candidates(self, project: Project) -> list[dict[str, Any]]:
        media_items: list[dict[str, Any]] = []
        posts = (
            Post.objects.filter(project=project, has_media=True)
            .select_related("source")
            .order_by("-posted_at")[:12]
        )
        for post in posts:
            media = resolve_post_media(post)
            if media:
                media_items.append(media)
        return media_items


class MediaStudioView(LoginRequiredMixin, TemplateView):
    """Генерация изображений в отдельной медиа-студии."""

    template_name = "media_library/studio.html"

    def get(self, request, *args, **kwargs):
        context = self.get_context_data()
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        story = self._get_story(request)
        if not story:
            messages.error(request, "Сюжет не найден.")
            return redirect("media_library:studio")
        if action == "generate":
            return self._handle_generate(request, story)
        if action == "save":
            return self._handle_save(request, story)
        messages.error(request, "Неизвестное действие.")
        return redirect("media_library:studio_story", story_id=story.pk)

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        projects = Project.objects.accessible_by(self.request.user).order_by("name")
        story = self._get_story(self.request, projects=projects)
        story_list = Story.objects.none()
        suggested_prompt = None
        prompt_error = None
        generate_form = None
        if story:
            story_list = Story.objects.filter(project=story.project).order_by("-created_at")
            try:
                suggested_prompt = suggest_image_prompt(story)
            except ImagePromptSuggestionFailed as exc:
                prompt_error = str(exc)
            initial_prompt = (
                suggested_prompt
                or story.image_prompt
                or story.body
                or story.title
                or ""
            )
            generate_form = StoryImageGenerateForm(
                initial=self._build_generate_initial(story, prompt=initial_prompt)
            )
        context.update(
            {
                "projects": projects,
                "story": story,
                "story_list": story_list,
                "suggested_prompt": suggested_prompt,
                "suggested_prompt_error": prompt_error,
                "generate_form": generate_form,
                "preview": kwargs.get("preview"),
                "attach_form": kwargs.get("attach_form"),
                "image_provider_settings": self._provider_settings(),
            }
        )
        return context

    def _get_story(self, request, *, projects=None):
        story_id = (
            self.kwargs.get("story_id")
            or request.GET.get("story")
            or request.POST.get("story")
        )
        if projects is None:
            projects = Project.objects.accessible_by(request.user).order_by("name")
        stories = Story.objects.filter(project__in=projects)
        if story_id and str(story_id).isdigit():
            story = stories.filter(pk=int(story_id)).select_related("project").first()
            if story:
                return story
        latest = stories.select_related("project").order_by("-created_at").first()
        return latest

    def _build_generate_initial(self, story: Story, *, prompt: str) -> dict[str, Any]:
        project = story.project
        selected_model = (project.image_model or "").strip()
        default_image_size = ""
        if selected_model == "gemini-3-pro-image-preview":
            default_image_size = getattr(settings, "GEMINI_IMAGE_SIZE", "")
        return {
            "prompt": prompt,
            "model": project.image_model,
            "size": normalize_image_size(project.image_size),
            "quality": normalize_image_quality(project.image_quality),
            "aspect_ratio": getattr(settings, "GEMINI_IMAGE_ASPECT_RATIO", ""),
            "image_size": default_image_size,
        }

    def _provider_settings(self) -> str:
        settings_payload = IMAGE_PROVIDER_SETTINGS.copy()
        gemini_aspect_ratio = getattr(settings, "GEMINI_IMAGE_ASPECT_RATIO", "").strip()
        gemini_image_size = getattr(settings, "GEMINI_IMAGE_SIZE", "").strip()
        for model_key in ("gemini-2.5-flash-image", "gemini-3-pro-image-preview"):
            if model_key not in settings_payload:
                continue
            if gemini_aspect_ratio:
                settings_payload[model_key]["default_aspect_ratio"] = gemini_aspect_ratio
            if gemini_image_size and model_key == "gemini-3-pro-image-preview":
                settings_payload[model_key]["default_image_size"] = gemini_image_size
        return json_dumps(settings_payload)

    def _handle_generate(self, request, story: Story):
        form = StoryImageGenerateForm(request.POST)
        if not form.is_valid():
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
            messages.error(request, f"Не удалось сгенерировать изображение: {exc}")
            return redirect("media_library:studio_story", story_id=story.pk)
        except Exception as exc:
            messages.error(request, f"Ошибка генерации изображения: {exc}")
            return redirect("media_library:studio_story", story_id=story.pk)

        encoded = base64.b64encode(result.data).decode("ascii")
        preview = {
            "data": encoded,
            "mime": result.mime_type,
            "prompt": prompt,
            "model": model,
            "size": safe_size,
            "quality": quality,
            "aspect_ratio": aspect_ratio,
            "image_size": image_size,
        }
        attach_form = StoryImageAttachForm(
            initial={
                "prompt": prompt,
                "mime_type": result.mime_type,
                "image_data": encoded,
                "model": model,
                "size": safe_size,
                "quality": quality,
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
            }
        )
        context = self.get_context_data(preview=preview, attach_form=attach_form)
        return self.render_to_response(context)

    def _handle_save(self, request, story: Story):
        form = StoryImageAttachForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Не удалось сохранить изображение.")
            return redirect("media_library:studio_story", story_id=story.pk)

        prompt = form.cleaned_data["prompt"]
        data = form.cleaned_data["image_data"]
        mime_type = form.cleaned_data["mime_type"]
        extension = self._extension_from_mime(mime_type)
        filename = f"studio_{story.pk}_{uuid.uuid4().hex}.{extension}"
        asset = MediaAsset(
            project=story.project,
            story=story,
            created_by=request.user,
            title=story.title or "",
            prompt=prompt,
            source_kind=MediaAsset.SourceKind.GENERATED,
        )
        asset.image_file.save(filename, ContentFile(data), save=True)
        messages.success(request, "Изображение сохранено в медиабиблиотеке.")
        return redirect(f"{reverse('media_library:library')}?project={story.project_id}")

    @staticmethod
    def _extension_from_mime(mime_type: str) -> str:
        default_extension = "png"
        if not mime_type:
            return default_extension
        extension = mimetypes.guess_extension(mime_type) or ""
        extension = extension.lstrip(".")
        if extension:
            return extension
        if mime_type == "image/jpeg":
            return "jpg"
        return default_extension


def json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload)
