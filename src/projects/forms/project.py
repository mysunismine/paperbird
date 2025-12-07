"""Forms related to project configuration."""

from __future__ import annotations

from zoneinfo import available_timezones

from django import forms

from core.constants import (
    IMAGE_MODEL_CHOICES,
    IMAGE_QUALITY_CHOICES,
    IMAGE_SIZE_CHOICES,
    REWRITE_MODEL_CHOICES,
)
from projects.models import Project
from projects.services.time_preferences import is_timezone_valid

TIMEZONE_CHOICES = [("UTC", "UTC")]
TIMEZONE_CHOICES.extend(sorted((tz, tz) for tz in available_timezones() if tz != "UTC"))


class ProjectCreateForm(forms.ModelForm):
    """Форма для создания проекта, принадлежащего текущему пользователю."""

    rewrite_model = forms.ChoiceField(
        label="Модель рерайта",
        choices=REWRITE_MODEL_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text=(
            "Определяет, какая модель GPT будет использоваться для переписывания текста "
            "сюжетов."
        ),
    )
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
        help_text=(
            "Поддерживаются 1024x1024 и вертикальные/горизонтальные 1024×1536/1536×1024; "
            "auto доверяет выбору модели."
        ),
    )
    image_quality = forms.ChoiceField(
        label="Качество",
        choices=IMAGE_QUALITY_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text=(
            "Низкое экономит токены, высокое даёт больше деталей; auto доверяет выбору "
            "модели."
        ),
    )
    time_zone = forms.CharField(
        label="Часовой пояс",
        widget=forms.Select(attrs={"class": "form-select"}, choices=TIMEZONE_CHOICES),
        help_text="Выберите часовой пояс проекта — он влияет на подсказки и расписание.",
        required=False,
        initial="UTC",
    )

    class Meta:
        model = Project
        fields = [
            "name",
            "description",
            "publish_target",
            "locale",
            "time_zone",
            "rewrite_model",
            "image_model",
            "image_size",
            "image_quality",
            "retention_days",
            "collector_telegram_interval",
            "collector_web_interval",
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
                    "placeholder": (
                        "Опишите задачи проекта, чтобы коллегам было проще ориентироваться"
                    ),
                }
            ),
            "publish_target": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "@channel или ссылка",
                    "maxlength": Project._meta.get_field("publish_target").max_length,
                }
            ),
            "locale": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "ru_RU или en_US",
                    "maxlength": Project._meta.get_field("locale").max_length,
                }
            ),
            "retention_days": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "step": 1}
            ),
            "collector_telegram_interval": forms.NumberInput(
                attrs={"class": "form-control", "min": 30, "step": 5}
            ),
            "collector_web_interval": forms.NumberInput(
                attrs={"class": "form-control", "min": 60, "step": 5}
            ),
        }
        labels = {
            "name": "Название",
            "description": "Описание",
            "publish_target": "Целевой канал",
            "locale": "Локаль",
            "time_zone": "Часовой пояс",
            "rewrite_model": "Модель рерайта",
            "image_model": "Модель генерации изображений",
            "image_size": "Размер изображения",
            "image_quality": "Качество",
            "retention_days": "Срок хранения (дней)",
            "collector_telegram_interval": "Интервал Telegram (сек)",
            "collector_web_interval": "Интервал веб-парсера (сек)",
        }
        help_texts = {
            "name": "Название должно быть уникальным в рамках вашей команды",
            "description": "Необязательно, но помогает запомнить контекст и критерии сбора",
            "publish_target": "Используется по умолчанию при публикации сюжетов",
            "locale": "Определяет язык формата даты для подсказок (например, ru_RU).",
            "time_zone": "Используется для расчёта текущей даты/времени в промтах.",
            "rewrite_model": "Меняйте модель, если требуется более точный или быстрый рерайт.",
            "retention_days": "Посты старше этого значения будут автоматически удаляться",
            "collector_telegram_interval": "Минимум 30 секунд, чтобы не получить лимиты Telegram.",
            "collector_web_interval": "Минимум 60 секунд, чтобы не нагружать сайт-источник.",
        }

    def __init__(self, *args, owner, **kwargs):  # type: ignore[override]
        if owner is None:
            raise ValueError("ProjectCreateForm requires an owner instance")
        self.owner = owner
        super().__init__(*args, **kwargs)
        for field_name in ("locale", "time_zone"):
            if field_name in self.fields:
                self.fields[field_name].required = False
        if not self.instance.pk:
            for field_name in ("collector_telegram_interval", "collector_web_interval"):
                if field_name in self.fields and not self.fields[field_name].initial:
                    model_field = Project._meta.get_field(field_name)
                    self.fields[field_name].initial = model_field.default

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

    def clean_collector_telegram_interval(self) -> int:
        value = self.cleaned_data["collector_telegram_interval"]
        if value < 30:
            raise forms.ValidationError("Интервал Telegram не может быть меньше 30 секунд")
        return value

    def clean_collector_web_interval(self) -> int:
        value = self.cleaned_data["collector_web_interval"]
        if value < 60:
            raise forms.ValidationError("Интервал веб-парсера не может быть меньше 60 секунд")
        return value

    def clean_locale(self) -> str:
        locale = (self.cleaned_data.get("locale") or "ru_RU").strip()
        if not locale:
            raise forms.ValidationError("Укажите локаль, например ru_RU или en_US.")
        return locale

    def clean_time_zone(self) -> str:
        tz_value = (self.cleaned_data.get("time_zone") or "UTC").strip()
        if not is_timezone_valid(tz_value):
            raise forms.ValidationError("Выберите корректный часовой пояс из списка.")
        return tz_value

    def save(self, commit: bool = True) -> Project:
        project = super().save(commit=False)
        project.owner = self.owner
        if commit:
            project.save()
        return project
