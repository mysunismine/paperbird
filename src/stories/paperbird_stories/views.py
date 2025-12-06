"""Вьюхи для работы с сюжетами."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any, Sequence

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView
from django.template.response import TemplateResponse
from django.utils import timezone

from projects.models import Post, Project
from projects.services.telethon_client import TelethonCredentialsMissingError
from stories.paperbird_stories.forms import (
    PublicationManageForm,
    StoryContentForm,
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
    normalize_image_quality,
    normalize_image_size,
)


class StoryListView(LoginRequiredMixin, ListView):
    """Список сюжетов пользователя."""

    model = Story
    template_name = "stories/story_list.html"
    context_object_name = "stories"

    def get_queryset(self):
        return Story.objects.filter(project__owner=self.request.user).select_related("project")


class StoryCreateView(LoginRequiredMixin, View):
    """Создание сюжета из выбранных постов без промежуточной страницы."""

    def post(self, request, *args, **kwargs):
        post_ids_raw = request.POST.getlist("posts")
        project_id = request.POST.get("project")
        if not post_ids_raw:
            messages.error(request, "Выберите посты для сюжета")
            return self._redirect_back(project_id)
        try:
            selected_ids = [int(value) for value in post_ids_raw]
        except ValueError:
            messages.error(request, "Некорректный список постов")
            return self._redirect_back(project_id)

        project = get_object_or_404(
            Project.objects.filter(owner=request.user),
            pk=project_id,
        )
        posts = list(
            Post.objects.filter(project=project, pk__in=selected_ids)
            .select_related("project")
        )
        if len(posts) == 0:
            messages.error(request, "Не удалось найти выбранные посты")
            return self._redirect_back(project.pk)
        order_map = {pk: index for index, pk in enumerate(selected_ids)}
        posts.sort(key=lambda post: order_map.get(post.pk, 0))
        try:
            story = StoryFactory(project=project).create(
                post_ids=[post.pk for post in posts],
                title="",
            )
        except StoryCreationError as exc:
            messages.error(request, str(exc))
            return self._redirect_back(project.pk)
        messages.success(request, "Сюжет создан. Добавьте комментарий и запустите рерайт.")
        return redirect("stories:detail", pk=story.pk)

    def _redirect_back(self, project_id: int | str | None):
        if project_id and str(project_id).isdigit():
            return redirect("feed-detail", int(project_id))
        first_project = self.request.user.projects.order_by("id").first()
        if first_project:
            return redirect("feed-detail", first_project.id)
        return redirect("projects:list")


class StoryDeleteView(LoginRequiredMixin, View):
    """Удаление сюжета пользователя."""

    def post(self, request, pk: int, *args, **kwargs):
        story = get_object_or_404(
            Story.objects.select_related("project"),
            pk=pk,
            project__owner=request.user,
        )
        title = story.title.strip() if story.title else ""
        story.delete()
        display = title or f"Сюжет #{pk}"
        messages.success(request, f"Сюжет «{display}» удалён.")
        return redirect("stories:list")


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
            context = self.get_context_data(rewrite_form=form)
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
                self._add_rewrite_message(messages.SUCCESS, "Рерайт выполнен. Проверьте сгенерированный текст.")
            return redirect(self.get_success_url())

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
                    self._add_rewrite_message(messages.ERROR, f"Не удалось подготовить промпт: {exc}")
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
            self._add_rewrite_message(messages.SUCCESS, "Рерайт выполнен. Проверьте сгенерированный текст.")
        return redirect(self.get_success_url())

    def _handle_save(self, request):
        form = StoryContentForm(request.POST, instance=self.object)
        if form.is_valid():
            form.save()
            messages.success(request, "Текст сюжета обновлён.")
            return redirect(self.get_success_url())
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
            return redirect(self.get_success_url())
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

    def _add_rewrite_message(self, level: int, text: str) -> None:
        messages.add_message(
            self.request,
            level,
            text,
            extra_tags="inline rewrite",
        )


class PublicationListView(LoginRequiredMixin, ListView):
    """Отображает публикации пользователя."""

    model = Publication
    template_name = "stories/publication_list.html"
    context_object_name = "publications"
    paginate_by = 25
    _page_override: str | None = None

    def get_queryset(self):
        return (
            Publication.objects.filter(story__project__owner=self.request.user)
            .select_related("story", "story__project")
            .order_by("-created_at")
        )

    def paginate_queryset(self, queryset, page_size):
        paginator = self.get_paginator(
            queryset,
            page_size,
            allow_empty_first_page=self.get_allow_empty(),
        )
        page_number = (
            self.kwargs.get(self.page_kwarg)
            or self.request.GET.get(self.page_kwarg)
            or self._page_override
        )
        page_obj = paginator.get_page(page_number)
        # сбрасываем переопределённую страницу, чтобы не влиять на следующие запросы
        self._page_override = None
        return paginator, page_obj, page_obj.object_list, page_obj.has_other_pages()

    def get_context_data(self, **kwargs: Any) -> dict[str, Any]:
        bound_form = kwargs.pop("bound_form", None)
        context = super().get_context_data(**kwargs)
        publications = context.get("publications", [])
        context["publication_forms"] = self._build_forms(publications, bound_form=bound_form)
        context["projects"] = (
            Project.objects.filter(owner=self.request.user)
            .order_by("name")
        )
        return context

    def post(self, request, *args, **kwargs):
        publication = self._get_publication(request.POST.get("publication_id"))
        page = request.POST.get("page") or ""
        submit_action = request.POST.get("submit_action", "save")
        if submit_action == "delete":
            title = publication.story.title or f"Сюжет #{publication.story_id}"
            publication.delete()
            messages.success(request, f"Публикация сюжета «{title}» удалена.")
            return self._redirect_to_page(page)

        form = PublicationManageForm(
            request.POST,
            instance=publication,
            prefix=self._form_prefix(publication),
        )
        if form.is_valid():
            updated = form.save()
            display_title = updated.story.title or f"Сюжет #{updated.story_id}"
            messages.success(request, f"Настройки публикации для сюжета «{display_title}» сохранены.")
            return self._redirect_to_page(page)

        messages.error(request, "Исправьте ошибки в форме публикации.")
        self.object_list = self.get_queryset()
        self._page_override = page or None
        context = self.get_context_data(bound_form=form)
        return self.render_to_response(context)

    def _form_prefix(self, publication: Publication) -> str:
        return f"publication-{publication.pk}"

    def _build_forms(
        self,
        publications: Sequence[Publication],
        *,
        bound_form: PublicationManageForm | None = None,
    ) -> list[tuple[Publication, PublicationManageForm]]:
        forms: list[tuple[Publication, PublicationManageForm]] = []
        bound_pk = bound_form.instance.pk if bound_form is not None else None
        for publication in publications:
            if bound_pk == publication.pk and bound_form is not None:
                forms.append((publication, bound_form))
            else:
                forms.append(
                    (
                        publication,
                        PublicationManageForm(
                            instance=publication,
                            prefix=self._form_prefix(publication),
                        ),
                    )
                )
        return forms

    def _get_publication(self, identifier: str | None) -> Publication:
        if not identifier or not str(identifier).isdigit():
            raise Http404("Публикация не найдена")
        return get_object_or_404(
            Publication.objects.select_related("story", "story__project"),
            pk=int(identifier),
            story__project__owner=self.request.user,
        )

    def _redirect_to_page(self, page: str | None):
        url = self.request.path
        if page and page not in {"", "1"}:
            return redirect(f"{url}?{self.page_kwarg}={page}")
        return redirect(url)


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
                        "Размер изображения автоматически скорректирован до поддерживаемого значения, "
                        "чтобы его можно было без ошибок загрузить в Paperbird.",
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

    def _find_post_media(self, post):
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
