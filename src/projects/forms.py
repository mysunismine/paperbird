"""Form classes for project management UI."""

from __future__ import annotations

from django import forms

from core.constants import (
    IMAGE_MODEL_CHOICES,
    IMAGE_QUALITY_CHOICES,
    IMAGE_SIZE_CHOICES,
)
from projects.models import Project, Source
from projects.services.source_metadata import enqueue_source_refresh


class ProjectCreateForm(forms.ModelForm):
    """Form to create a project owned by the current user."""

    image_model = forms.ChoiceField(
        label="Модель генерации изображений",
        choices=IMAGE_MODEL_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Выберите модель, которую будет использовать генератор изображений.",
    )
    image_size = forms.ChoiceField(
        label="Размер изображения",
        choices=IMAGE_SIZE_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Paperbird генерирует квадратные изображения до 512x512 пикселей, чтобы их без проблем загружать в соцсети и Telegram.",
    )
    image_quality = forms.ChoiceField(
        label="Качество",
        choices=IMAGE_QUALITY_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Стандарт быстрее, HD даёт больше деталей.",
    )

    class Meta:
        model = Project
        fields = [
            "name",
            "description",
            "publish_target",
            "image_model",
            "image_size",
            "image_quality",
            "retention_days",
        ]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Рабочее название проекта",
                    "maxlength": Project._meta.get_field("name").max_length,
                }
            ),
            "description": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Опишите задачи проекта, чтобы коллегам было проще ориентироваться",
                }
            ),
            "publish_target": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "@channel или ссылка",
                    "maxlength": Project._meta.get_field("publish_target").max_length,
                }
            ),
            "retention_days": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "step": 1}
            ),
        }
        labels = {
            "name": "Название",
            "description": "Описание",
            "publish_target": "Целевой канал",
            "image_model": "Модель генерации изображений",
            "image_size": "Размер изображения",
            "image_quality": "Качество",
            "retention_days": "Срок хранения (дней)",
        }
        help_texts = {
            "name": "Название должно быть уникальным в рамках вашей команды",
            "description": "Необязательно, но помогает запомнить контекст и критерии сбора",
            "publish_target": "Используется по умолчанию при публикации сюжетов",
            "retention_days": "Посты старше этого значения будут автоматически удаляться",
        }

    def __init__(self, *args, owner, **kwargs):  # type: ignore[override]
        if owner is None:
            raise ValueError("ProjectCreateForm requires an owner instance")
        self.owner = owner
        super().__init__(*args, **kwargs)

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        queryset = Project.objects.filter(owner=self.owner, name__iexact=name)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError(
                "У вас уже есть проект с таким названием. Выберите другое."
            )
        return name

    def clean_retention_days(self) -> int:
        value = self.cleaned_data["retention_days"]
        if value < 1:
            raise forms.ValidationError("Срок хранения должен быть не меньше 1 дня")
        return value

    def save(self, commit: bool = True) -> Project:
        project = super().save(commit=False)
        project.owner = self.owner
        if commit:
            project.save()
        return project


class SourceBaseForm(forms.ModelForm):
    """Базовая форма управления источником."""

    class Meta:
        model = Source
        fields = [
            "title",
            "telegram_id",
            "username",
            "invite_link",
            "deduplicate_text",
            "deduplicate_media",
            "retention_days",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "Название (заполнится автоматически)"}),
            "telegram_id": forms.NumberInput(
                attrs={"class": "form-control", "placeholder": "ID канала (опционально)"}
            ),
            "username": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "@channel или https://t.me/..."}
            ),
            "invite_link": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "https://t.me/+..."}
            ),
            "deduplicate_text": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "deduplicate_media": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "retention_days": forms.NumberInput(attrs={"class": "form-control", "min": 1, "step": 1}),
        }
        labels = {
            "title": "Название",
            "telegram_id": "Telegram ID",
            "username": "Ссылка или @username",
            "invite_link": "Инвайт-ссылка",
            "deduplicate_text": "Дедупликация текста",
            "deduplicate_media": "Дедупликация медиа",
            "retention_days": "Срок хранения (дней)",
        }

    def __init__(self, *args, project: Project, **kwargs):
        self.project = project
        super().__init__(*args, **kwargs)
        self.fields["title"].required = False
        self.fields["username"].required = False
        self.fields["invite_link"].required = False
        self.fields["telegram_id"].required = False
        if not self.initial.get("retention_days"):
            self.fields["retention_days"].initial = project.retention_days

    def clean_telegram_id(self):
        value = self.cleaned_data.get("telegram_id")
        if value in ("", None):
            return None
        if value <= 0:
            raise forms.ValidationError("Telegram ID должен быть положительным")
        queryset = Source.objects.filter(project=self.project, telegram_id=value)
        if self.instance.pk:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise forms.ValidationError("Источник с таким Telegram ID уже добавлен")
        return value

    def clean_retention_days(self) -> int:
        value = self.cleaned_data.get("retention_days") or self.project.retention_days
        if value < 1:
            raise forms.ValidationError("Срок хранения должен быть не меньше 1 дня")
        return value

    def clean_username(self) -> str:
        raw = (self.cleaned_data.get("username") or "").strip()
        if not raw:
            return ""
        value = raw
        if value.startswith("http://") or value.startswith("https://"):
            if "t.me/" in value:
                value = value.split("t.me/", 1)[-1]
            value = value.strip("/")
            if value.startswith("s/"):
                value = value[2:]
            if value.startswith("joinchat/"):
                value = value[len("joinchat/") :]
        if value.startswith("@"):  # remove leading @
            value = value[1:]
        value = value.strip("/")
        if value.startswith("+"):
            # invite link detected, leave username empty
            return ""
        return value

    def clean(self):
        cleaned = super().clean()
        username = cleaned.get("username")
        invite = (cleaned.get("invite_link") or "").strip()
        telegram_id = cleaned.get("telegram_id")
        raw_username = (self.data.get("username") or "").strip()

        if not username and raw_username:
            lower = raw_username.lower()
            if lower.startswith("https://t.me/+") or lower.startswith("http://t.me/+") or "joinchat" in lower:
                cleaned["invite_link"] = raw_username
                invite = raw_username

        if not username and not invite and telegram_id is None:
            raise forms.ValidationError("Укажите @username, ссылку на канал или инвайт-ссылку.")
        return cleaned

    def save(self, commit: bool = True) -> Source:
        source: Source = super().save(commit=False)
        source.project = self.project
        if commit:
            source.save()
        enqueue_source_refresh(source)
        return source


class SourceCreateForm(SourceBaseForm):
    """Создание нового источника."""


class SourceUpdateForm(SourceBaseForm):
    """Редактирование существующего источника."""
