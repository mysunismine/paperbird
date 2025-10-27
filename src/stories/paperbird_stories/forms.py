"""Формы для работы с сюжетами."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from django import forms
from django.utils import timezone

from core.constants import (
    IMAGE_MODEL_CHOICES,
    IMAGE_QUALITY_CHOICES,
    IMAGE_SIZE_CHOICES,
)
from projects.models import Post, Project
from stories.paperbird_stories.models import RewritePreset, Story


class StoryCreateForm(forms.Form):
    project = forms.ModelChoiceField(label="Проект", queryset=Project.objects.none())
    posts = forms.ModelMultipleChoiceField(
        label="Посты",
        queryset=Post.objects.none(),
        widget=forms.SelectMultiple(attrs={"size": 12}),
        help_text="Выберите один или несколько постов для сюжета.",
    )
    title = forms.CharField(label="Заголовок", max_length=255, required=False)
    editor_comment = forms.CharField(
        label="Комментарий редактора",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Опционально: добавьте инструкции для рерайта.",
    )

    def __init__(self, *args: Any, user, project_id: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        projects = Project.objects.filter(owner=user).order_by("name")
        self.fields["project"].queryset = projects
        if project_id:
            self.fields["project"].initial = projects.filter(id=project_id).first()

        posts_qs = Post.objects.filter(project__owner=user).order_by("-posted_at")
        if project_id:
            posts_qs = posts_qs.filter(project_id=project_id)
        self.fields["posts"].queryset = posts_qs

    def clean_posts(self):
        posts = self.cleaned_data["posts"]
        if not posts:
            raise forms.ValidationError("Нужно выбрать хотя бы один пост")
        project: Project = self.cleaned_data.get("project")
        if project and any(post.project_id != project.id for post in posts):
            raise forms.ValidationError("Все посты должны принадлежать выбранному проекту")
        return posts


class StoryRewriteForm(forms.Form):
    preset = forms.ModelChoiceField(
        label="Пресет",
        queryset=RewritePreset.objects.none(),
        required=False,
        empty_label="Без пресета",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    editor_comment = forms.CharField(
        label="Комментарий",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )

    def __init__(self, *args: Any, story: Story | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        presets = RewritePreset.objects.none()
        if story is not None:
            presets = story.project.rewrite_presets.filter(is_active=True).order_by("name")
            if story.last_rewrite_preset:
                self.fields["preset"].initial = story.last_rewrite_preset
        self.fields["preset"].queryset = presets


class StoryPromptConfirmForm(forms.Form):
    """Форма подтверждения промпта перед отправкой на рерайт."""

    prompt_system = forms.CharField(
        label="System prompt",
        required=True,
        widget=forms.Textarea(attrs={"rows": 5, "class": "form-control"}),
        error_messages={"required": "Заполните system prompt"},
    )
    prompt_user = forms.CharField(
        label="User prompt",
        required=True,
        widget=forms.Textarea(attrs={"rows": 14, "class": "form-control"}),
        error_messages={"required": "Заполните user prompt"},
    )
    preset = forms.ModelChoiceField(
        label="Пресет",
        queryset=RewritePreset.objects.none(),
        required=False,
        widget=forms.HiddenInput(),
    )
    editor_comment = forms.CharField(required=False, widget=forms.HiddenInput())

    def __init__(self, *args: Any, story: Story | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        presets = RewritePreset.objects.none()
        if story is not None:
            presets = story.project.rewrite_presets.filter(is_active=True).order_by("name")
        self.fields["preset"].queryset = presets

    @property
    def selected_preset(self) -> RewritePreset | None:
        """Возвращает выбранный пресет даже при невалидной форме."""

        cleaned_data = getattr(self, "cleaned_data", {})
        if "preset" in cleaned_data:
            return cleaned_data["preset"]
        if self.is_bound:
            value = self.data.get("preset")
            if value:
                try:
                    return self.fields["preset"].queryset.get(pk=value)
                except (RewritePreset.DoesNotExist, ValueError, TypeError):
                    return None
        initial_value = self.initial.get("preset")
        if isinstance(initial_value, RewritePreset):
            return initial_value
        if initial_value:
            try:
                return self.fields["preset"].queryset.get(pk=initial_value)
            except (RewritePreset.DoesNotExist, ValueError, TypeError):
                return None
        return None

    @property
    def editor_comment_value(self) -> str:
        """Возвращает комментарий редактора для отображения на форме."""

        cleaned_data = getattr(self, "cleaned_data", {})
        if "editor_comment" in cleaned_data:
            return cleaned_data["editor_comment"]
        if self.is_bound:
            return self.data.get("editor_comment", "")
        return self.initial.get("editor_comment", "")


class StoryPublishForm(forms.Form):
    target = forms.CharField(
        label="Канал или чат",
        max_length=255,
        help_text="Например, @my_channel или ссылку",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    publish_at = forms.DateTimeField(
        label="Запланировать на",
        required=False,
        input_formats=["%Y-%m-%dT%H:%M"],
        help_text="Оставьте пустым, чтобы опубликовать сразу",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local", "class": "form-control"}
        ),
    )

    def clean_publish_at(self):
        publish_at = self.cleaned_data.get("publish_at")
        if publish_at is None:
            return None
        if timezone.is_naive(publish_at):
            publish_at = timezone.make_aware(
                publish_at, timezone.get_current_timezone()
            )
        publish_at = publish_at.astimezone(timezone.get_current_timezone())
        if publish_at <= timezone.now():
            raise forms.ValidationError("Укажите время в будущем")
        return publish_at


class StoryContentForm(forms.ModelForm):
    """Редактирование заголовка и текста сюжета."""

    class Meta:
        model = Story
        fields = ["title", "body"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Заголовок сюжета"}),
            "body": forms.Textarea(attrs={"class": "form-control", "rows": 12, "placeholder": "Переписанный текст"}),
        }
        labels = {
            "title": "Заголовок",
            "body": "Текст",
        }


class StoryImageGenerateForm(forms.Form):
    prompt = forms.CharField(
        label="Описание изображения",
        widget=forms.Textarea(attrs={"rows": 4, "class": "form-control"}),
        help_text="Опишите желаемое изображение",
        error_messages={"required": "Заполните описание"},
    )
    model = forms.ChoiceField(
        label="Модель",
        choices=IMAGE_MODEL_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    size = forms.ChoiceField(
        label="Размер",
        choices=IMAGE_SIZE_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Для безошибочной загрузки изображения ограничены значениями до 512x512.",
    )
    quality = forms.ChoiceField(
        label="Качество",
        choices=IMAGE_QUALITY_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def clean_prompt(self):
        prompt = self.cleaned_data["prompt"].strip()
        if not prompt:
            raise forms.ValidationError("Описание не может быть пустым")
        return prompt


class StoryImageAttachForm(forms.Form):
    prompt = forms.CharField(widget=forms.HiddenInput())
    image_data = forms.CharField(widget=forms.HiddenInput())
    mime_type = forms.CharField(widget=forms.HiddenInput())
    model = forms.ChoiceField(
        choices=IMAGE_MODEL_CHOICES,
        widget=forms.HiddenInput(),
        required=False,
    )
    size = forms.ChoiceField(
        choices=IMAGE_SIZE_CHOICES,
        widget=forms.HiddenInput(),
        required=False,
    )
    quality = forms.ChoiceField(
        choices=IMAGE_QUALITY_CHOICES,
        widget=forms.HiddenInput(),
        required=False,
    )

    def clean_prompt(self):
        value = self.cleaned_data["prompt"].strip()
        if not value:
            raise forms.ValidationError("Отсутствует описание для изображения")
        return value

    def clean_image_data(self):
        raw = self.cleaned_data["image_data"].strip()
        if not raw:
            raise forms.ValidationError("Отсутствуют данные изображения")
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise forms.ValidationError("Некорректные данные изображения") from exc
        if not data:
            raise forms.ValidationError("Отсутствуют данные изображения")
        return data

    def clean_mime_type(self):
        mime_type = self.cleaned_data["mime_type"].strip()
        if not mime_type.startswith("image/"):
            raise forms.ValidationError("Неподдерживаемый тип файла")
        return mime_type


class StoryImageDeleteForm(forms.Form):
    confirm = forms.BooleanField(required=True, widget=forms.HiddenInput(), initial=True)
