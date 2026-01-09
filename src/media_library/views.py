"""Views for media library and media studio."""

from __future__ import annotations

import json
import base64
import binascii
import hashlib
import mimetypes
import uuid
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from core.constants import IMAGE_PROVIDER_SETTINGS
from media_library.forms import MediaAssetUploadForm
from media_library.models import MediaAsset
from media_library.services import resolve_post_media
from projects.models import Post, Project
from stories.paperbird_stories.forms import (
    StoryImageAttachForm,
    StoryImageDeleteForm,
    StoryImageGenerateForm,
    StoryImageLibraryAttachForm,
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


class MediaLibraryView(LoginRequiredMixin, TemplateView):
    """Список медиа проекта и загрузка файлов в медиабиблиотеку."""

    template_name = "media_library/library.html"
    paginate_by = 24

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
        page_number = self.request.GET.get("page")
        if project:
            assets, filters = self._build_assets_queryset(project)
            tags_cloud = self._collect_tags(assets)
            paginator = Paginator(assets, self.paginate_by)
            assets = paginator.get_page(page_number)
            elided_pages = paginator.get_elided_page_range(assets.number)
            if filters["show_posts"]:
                post_media = self._post_media_candidates(project)
            base_query = self.request.GET.copy()
            base_query.pop("page", None)
            pagination_query = base_query.urlencode()
            tag_links, selected_tag_links = self._build_tag_links(
                base_query, filters["selected_tags"], tags_cloud
            )
        else:
            paginator = None
            tags_cloud = []
            elided_pages = []
            pagination_query = ""
            tag_links = {}
            selected_tag_links = {}
            filters = {
                "search_query": "",
                "media_type": "",
                "source_kind": "",
                "sort_value": "newest",
                "show_posts": False,
                "mine_only": False,
                "tags_value": "",
                "selected_tags": [],
            }
        total_count = paginator.count if paginator else 0
        context.update(
            {
                "projects": projects,
                "project": project,
                "assets": assets,
                "page_obj": assets if project else None,
                "post_media": post_media,
                "upload_form": MediaAssetUploadForm(),
                "search_query": filters["search_query"],
                "media_type": filters["media_type"],
                "source_kind": filters["source_kind"],
                "sort_value": filters["sort_value"],
                "show_posts": filters["show_posts"],
                "mine_only": filters["mine_only"],
                "tags_value": filters["tags_value"],
                "selected_tags": filters["selected_tags"],
                "pagination_query": pagination_query,
                "tags_cloud": tags_cloud,
                "tag_links": tag_links,
                "selected_tag_links": selected_tag_links,
                "paginator": paginator,
                "elided_pages": elided_pages,
                "is_paginated": bool(paginator and paginator.num_pages > 1),
                "total_count": total_count,
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
        messages.success(request, "Медиа добавлено в медиабиблиотеку.")
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

    @staticmethod
    def _collect_tags(assets) -> list[str]:
        """Собирает популярные теги для облака."""
        tag_counter: Counter[str] = Counter()
        for tags in assets.values_list("tags", flat=True):
            if not tags:
                continue
            for tag in tags:
                if isinstance(tag, str) and tag.strip():
                    tag_counter[tag.strip()] += 1
        return [tag for tag, _ in tag_counter.most_common(12)]

    @staticmethod
    def _apply_sort(assets, sort_value: str):
        """Применяет выбранную сортировку к queryset."""
        if sort_value == "oldest":
            return assets.order_by("created_at")
        if sort_value == "title":
            return assets.order_by("title", "-created_at")
        return assets.order_by("-created_at")

    def _build_assets_queryset(self, project: Project, *, include_tag_filters: bool = True):
        """Собирает queryset медиа с учетом фильтров из запроса."""
        search_query = (self.request.GET.get("q") or "").strip()
        media_type = (self.request.GET.get("type") or "").strip()
        source_kind = (self.request.GET.get("source") or "").strip()
        sort_value = (self.request.GET.get("sort") or "").strip() or "newest"
        show_posts = (self.request.GET.get("show_posts") or "").strip() == "1"
        mine_only = (self.request.GET.get("mine") or "").strip() == "1"
        tags_value = (self.request.GET.get("tags") or "").strip()
        selected_tags = self._parse_tags(tags_value)

        assets = MediaAsset.objects.filter(project=project).select_related("created_by")
        if search_query:
            assets = self._apply_search_query(assets, search_query)
        if include_tag_filters and selected_tags:
            assets = self._apply_tag_filters(assets, selected_tags)
        if media_type in MediaAsset.MediaType.values:
            assets = assets.filter(media_type=media_type)
        if source_kind in MediaAsset.SourceKind.values:
            assets = assets.filter(source_kind=source_kind)
        elif source_kind == "all":
            pass
        elif source_kind == "owned" or not source_kind:
            assets = assets.filter(
                source_kind__in=[
                    MediaAsset.SourceKind.UPLOAD,
                    MediaAsset.SourceKind.GENERATED,
                ]
            )
        if mine_only:
            assets = assets.filter(created_by=self.request.user)
        assets = self._apply_sort(assets, sort_value)
        return assets, {
            "search_query": search_query,
            "media_type": media_type,
            "source_kind": source_kind,
            "sort_value": sort_value,
            "show_posts": show_posts,
            "mine_only": mine_only,
            "tags_value": tags_value,
            "selected_tags": selected_tags,
        }

    def _apply_search_query(self, assets, search_query: str):
        """Применяет поиск по заголовку, промпту и тегам."""
        base = assets
        filtered = assets.filter(
            Q(title__icontains=search_query) | Q(prompt__icontains=search_query)
        )
        if self._supports_json_contains():
            return filtered | base.filter(tags__contains=[search_query])
        tag_ids = self._ids_with_tag(base, search_query)
        if not tag_ids:
            return filtered
        return (filtered | base.filter(id__in=tag_ids)).distinct()

    def _apply_tag_filters(self, assets, selected_tags: list[str]):
        """Фильтрует по списку тегов с учетом поддержки JSON contains."""
        if self._supports_json_contains():
            for tag in selected_tags:
                assets = assets.filter(tags__contains=[tag])
            return assets
        filtered_ids = None
        for tag in selected_tags:
            tag_ids = set(self._ids_with_tag(assets, tag))
            filtered_ids = tag_ids if filtered_ids is None else filtered_ids & tag_ids
        if not filtered_ids:
            return assets.none()
        return assets.filter(id__in=filtered_ids)

    @staticmethod
    def _supports_json_contains() -> bool:
        return getattr(connection.features, "supports_json_field_contains", False)

    @staticmethod
    def _ids_with_tag(assets, tag: str) -> list[int]:
        matched_ids: list[int] = []
        for asset_id, tags in assets.values_list("id", "tags"):
            if tags and isinstance(tags, list) and tag in tags:
                matched_ids.append(asset_id)
        return matched_ids

    @staticmethod
    def _parse_tags(value: str) -> list[str]:
        """Разбирает строку тегов в уникальный список."""
        tags: list[str] = []
        if not value:
            return tags
        for item in value.split(","):
            tag = item.strip()
            if not tag or tag in tags:
                continue
            tags.append(tag)
        return tags

    @staticmethod
    def _build_tag_links(base_query, selected_tags: list[str], tags_cloud: Iterable[str]):
        """Формирует ссылки для добавления/удаления тегов."""
        tag_links: dict[str, str] = {}
        selected_tag_links: dict[str, str] = {}
        for tag in tags_cloud:
            query = base_query.copy()
            tags_for_link = selected_tags[:]
            if tag not in tags_for_link:
                tags_for_link.append(tag)
            if tags_for_link:
                query["tags"] = ",".join(tags_for_link)
            else:
                query.pop("tags", None)
            tag_links[tag] = query.urlencode()
        for tag in selected_tags:
            query = base_query.copy()
            tags_for_link = [value for value in selected_tags if value != tag]
            if tags_for_link:
                query["tags"] = ",".join(tags_for_link)
            else:
                query.pop("tags", None)
            selected_tag_links[tag] = query.urlencode()
        return tag_links, selected_tag_links


class MediaLibraryTagSuggestionsView(LoginRequiredMixin, View):
    """JSON-эндпоинт для подсказок тегов."""

    def get(self, request, *args, **kwargs):
        projects = Project.objects.accessible_by(request.user).order_by("name")
        project = MediaLibraryView()._get_project(request, projects=projects)
        if not project:
            return JsonResponse({"items": []})

        view = MediaLibraryView()
        view.request = request
        assets, filters = view._build_assets_queryset(project, include_tag_filters=True)
        suggest_query = (request.GET.get("suggest") or "").strip()
        suggestions = self._collect_suggestions(
            assets,
            suggest_query=suggest_query,
            exclude=set(filters["selected_tags"]),
            limit=8,
        )
        return JsonResponse({"items": suggestions})

    @staticmethod
    def _collect_suggestions(
        assets,
        *,
        suggest_query: str,
        exclude: set[str],
        limit: int,
    ) -> list[str]:
        suggestions: Counter[str] = Counter()
        query = suggest_query.lower()
        for tags in assets.values_list("tags", flat=True):
            if not tags:
                continue
            for tag in tags:
                if not isinstance(tag, str):
                    continue
                cleaned = tag.strip()
                if not cleaned or cleaned in exclude:
                    continue
                if query and not cleaned.lower().startswith(query):
                    continue
                suggestions[cleaned] += 1
        return [tag for tag, _ in suggestions.most_common(limit)]


class MediaLibraryAssetsView(LoginRequiredMixin, View):
    """JSON-эндпоинт для подгрузки медиа по фильтрам."""

    paginate_by = 24

    def get(self, request, *args, **kwargs):
        projects = Project.objects.accessible_by(request.user).order_by("name")
        project = MediaLibraryView()._get_project(request, projects=projects)
        if not project:
            return JsonResponse({"items": [], "has_next": False, "next_page": None})

        view = MediaLibraryView()
        view.request = request
        assets, _filters = view._build_assets_queryset(project)
        paginator = Paginator(assets, self.paginate_by)
        page = paginator.get_page(request.GET.get("page"))
        items = [self._serialize_asset(asset) for asset in page.object_list]
        return JsonResponse(
            {
                "items": items,
                "has_next": page.has_next(),
                "next_page": page.next_page_number() if page.has_next() else None,
                "total_count": paginator.count,
            }
        )

    @staticmethod
    def _serialize_asset(asset: MediaAsset) -> dict[str, Any]:
        return {
            "id": asset.id,
            "title": asset.title or "Без названия",
            "url": asset.image_file.url,
            "media_type": asset.media_type,
            "media_type_label": asset.get_media_type_display(),
            "source_kind": asset.source_kind,
            "source_label": asset.get_source_kind_display(),
            "created_by": str(asset.created_by) if asset.created_by else "",
            "tags": asset.tags or [],
        }


class MediaStudioView(LoginRequiredMixin, TemplateView):
    """Генерация изображений (изолированная студия)."""

    template_name = "media_library/studio.html"

    def get(self, request, *args, **kwargs):
        # Pre-fill prompt from query params (e.g. from Story page)
        initial_prompt = request.GET.get("prompt", "")
        context = self.get_context_data(
            generate_form=self._generate_form_initial(prompt=initial_prompt),
            image_provider_settings=self._provider_settings(),
        )
        return self.render_to_response(context)

    def post(self, request, *args, **kwargs):
        action = request.POST.get("action")
        
        if action == "generate":
            return self._handle_generate(request)
        if action == "save_to_library":
            return self._handle_save_to_library(request)
            
        messages.error(request, "Неизвестное действие.")
        return redirect("media_library:studio")

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        context = super().get_context_data(**kwargs)
        # Fetch projects to populate the model choice if needed, 
        # or just use the user's first project for defaults.
        projects = Project.objects.accessible_by(self.request.user).order_by("name")
        project = projects.first()
        
        context.update(
            {
                "projects": projects,
                "project": project,
                "generate_form": kwargs.get("generate_form") or self._generate_form_initial(project=project),
                "image_provider_settings": self._provider_settings(),
            }
        )
        return context

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
        
        # Add style support info for OpenAI/DALL-E models
        if "dall-e-3" in settings_payload:
             settings_payload["dall-e-3"]["styles"] = ["vivid", "natural"]
             settings_payload["dall-e-3"]["default_style"] = "vivid"

        return json.dumps(settings_payload)

    def _generate_form_initial(
        self,
        *,
        prompt: str | None = None,
        project: Project | None = None,
    ) -> StoryImageGenerateForm:
        
        selected_model = ""
        if project:
            selected_model = project.image_model
            
        default_image_size = ""
        if selected_model == "gemini-3-pro-image-preview":
            default_image_size = getattr(settings, "GEMINI_IMAGE_SIZE", "")
            
        initial = {
            "prompt": prompt or "",
            "model": selected_model,
            "size": normalize_image_size(project.image_size if project else ""),
            "quality": normalize_image_quality(project.image_quality if project else ""),
            "style": "vivid", # Default assumption
            "aspect_ratio": getattr(settings, "GEMINI_IMAGE_ASPECT_RATIO", ""),
            "image_size": default_image_size,
        }
        return StoryImageGenerateForm(initial=initial)

    def _handle_generate(self, request):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        form = StoryImageGenerateForm(request.POST)
        
        if not form.is_valid():
            if is_ajax:
                return JsonResponse({"status": "error", "errors": form.errors}, status=400)
            return self.render_to_response(self.get_context_data(generate_form=form))

        prompt = form.cleaned_data["prompt"]
        model = form.cleaned_data["model"]
        size = form.cleaned_data["size"]
        quality = normalize_image_quality(form.cleaned_data["quality"])
        style = form.cleaned_data.get("style")
        aspect_ratio = (form.cleaned_data.get("aspect_ratio") or "").strip()
        image_size = (form.cleaned_data.get("image_size") or "").strip()
        
        generator = default_image_generator(model=model)
        use_gemini = _looks_like_gemini_model(model)
        
        if use_gemini:
            safe_size = ""
            quality = ""
            style = "" # Gemini doesn't support this style param yet via standard api in this context
        else:
            safe_size = normalize_image_size(size)
            
        try:
            result = generator.generate(
                prompt=prompt,
                model=model,
                size=safe_size,
                quality=quality,
                style=style,
                aspect_ratio=aspect_ratio if use_gemini else None,
                image_size=image_size if use_gemini else None,
            )
        except (ImageGenerationFailed, Exception) as exc:
            if is_ajax:
                return JsonResponse({"status": "error", "message": str(exc)}, status=500)
            messages.error(request, f"Ошибка генерации: {exc}")
            return redirect("media_library:studio")

        encoded = base64.b64encode(result.data).decode("ascii")
        preview = {
            "data": encoded,
            "mime": result.mime_type,
            "prompt": prompt,
            "model": model,
        }
        
        # Store in session for "Save to Library" action
        self._store_preview_session(request, preview)
        
        if is_ajax:
            return JsonResponse({"status": "success", "preview": preview})
            
        return self.render_to_response(self.get_context_data(generate_form=form))

    def _handle_save_to_library(self, request):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        
        # We expect the preview to be in the session
        preview_data = self._get_preview_session(request)
        if not preview_data:
            msg = "Изображение устарело. Сгенерируйте заново."
            if is_ajax:
                 return JsonResponse({"status": "error", "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("media_library:studio")

        project_id = request.POST.get("project_id")
        project = None
        if project_id:
             project = get_object_or_404(Project, pk=project_id)
        else:
             project = Project.objects.accessible_by(request.user).first()
             
        if not project:
            msg = "Не выбран проект для сохранения."
            if is_ajax:
                 return JsonResponse({"status": "error", "message": msg}, status=400)
            messages.error(request, msg)
            return redirect("media_library:studio")

        try:
            image_data = base64.b64decode(preview_data["data"])
            filename = f"generated_{uuid.uuid4().hex}.png" # Defaulting to png, assumption
            if "jpeg" in preview_data["mime"]:
                filename = filename.replace(".png", ".jpg")
            elif "webp" in preview_data["mime"]:
                filename = filename.replace(".png", ".webp")

            asset = MediaAsset.objects.create(
                project=project,
                created_by=request.user,
                title=preview_data["prompt"][:50] + "...",
                prompt=preview_data["prompt"],
                source_kind=MediaAsset.SourceKind.GENERATED,
            )
            asset.image_file.save(filename, ContentFile(image_data), save=True)
            
            # Clear session
            self._clear_preview_session(request)
            
            msg = "Изображение сохранено в библиотеку."
            if is_ajax:
                return JsonResponse({"status": "success", "message": msg})
            messages.success(request, msg)
            return redirect("media_library:studio")
            
        except Exception as exc:
             if is_ajax:
                 return JsonResponse({"status": "error", "message": str(exc)}, status=500)
             messages.error(request, f"Ошибка сохранения: {exc}")
             return redirect("media_library:studio")

    def _store_preview_session(self, request, data: dict):
        request.session["media_studio_preview_data"] = data
        request.session.modified = True

    def _get_preview_session(self, request) -> dict | None:
        return request.session.get("media_studio_preview_data")
        
    def _clear_preview_session(self, request):
        if "media_studio_preview_data" in request.session:
            del request.session["media_studio_preview_data"]
            request.session.modified = True


from django.views.generic import DetailView

class MediaAssetDetailView(LoginRequiredMixin, DetailView):
    """Детальный просмотр медиа-актива."""
    model = MediaAsset
    template_name = "media_library/asset_detail.html"
    context_object_name = "asset"

    def get_queryset(self):
        # Allow viewing assets from projects accessible to the user
        projects = Project.objects.accessible_by(self.request.user)
        return MediaAsset.objects.filter(project__in=projects)
