"""Form classes for project management UI."""

from __future__ import annotations

from django import forms

from core.constants import (
    IMAGE_MODEL_CHOICES,
    IMAGE_QUALITY_CHOICES,
    IMAGE_SIZE_CHOICES,
    REWRITE_MODEL_CHOICES,
)
from projects.models import Project, ProjectPromptConfig, Source, WebPreset
from projects.services.source_metadata import enqueue_source_refresh
from projects.services.web_preset_registry import PresetValidationError, WebPresetRegistry


class ProjectCreateForm(forms.ModelForm):
    """Form to create a project owned by the current user."""

    rewrite_model = forms.ChoiceField(
        label="Модель рерайта",
        choices=REWRITE_MODEL_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Определяет, какая модель GPT будет использоваться для переписывания текста сюжетов.",
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
            "rewrite_model",
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
            "rewrite_model": "Модель рерайта",
            "image_model": "Модель генерации изображений",
            "image_size": "Размер изображения",
            "image_quality": "Качество",
            "retention_days": "Срок хранения (дней)",
        }
        help_texts = {
            "name": "Название должно быть уникальным в рамках вашей команды",
            "description": "Необязательно, но помогает запомнить контекст и критерии сбора",
            "publish_target": "Используется по умолчанию при публикации сюжетов",
            "rewrite_model": "Меняйте модель, если требуется более точный или быстрый рерайт.",
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

    preset_payload = forms.CharField(
        label="Импорт JSON пресета",
        widget=forms.Textarea(
            attrs={
                "class": "form-control font-monospace",
                "rows": 6,
                "placeholder": "{\n  \"name\": \"my_site\",\n  ...\n}",
            }
        ),
        required=False,
        help_text="Вставьте содержимое preset.json, если хотите добавить новый сайт.",
    )

    preset_file = forms.FileField(
        label="Импорт пресета (файл)",
        required=False,
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": ".json,application/json",
            }
        ),
        help_text="Выберите JSON-файл с описанием пресета.",
    )

    class Meta:
        model = Source
        fields = [
            "type",
            "title",
            "telegram_id",
            "username",
            "invite_link",
            "web_preset",
            "preset_payload",
            "preset_file",
            "deduplicate_text",
            "deduplicate_media",
            "retention_days",
        ]
        widgets = {
            "type": forms.Select(attrs={"class": "form-select"}),
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
            "web_preset": forms.Select(attrs={"class": "form-select"}),
            "deduplicate_text": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "deduplicate_media": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "retention_days": forms.NumberInput(attrs={"class": "form-control", "min": 1, "step": 1}),
        }
        labels = {
            "type": "Тип источника",
            "title": "Название",
            "telegram_id": "Telegram ID",
            "username": "Ссылка или @username",
            "invite_link": "Инвайт-ссылка",
            "web_preset": "Пресет веб-парсера",
            "deduplicate_text": "Дедупликация текста",
            "deduplicate_media": "Дедупликация медиа",
            "retention_days": "Срок хранения (дней)",
        }

    def __init__(self, *args, project: Project, **kwargs):
        self.project = project
        self._preset_registry: WebPresetRegistry | None = None
        super().__init__(*args, **kwargs)
        self.fields["title"].required = False
        self.fields["username"].required = False
        self.fields["invite_link"].required = False
        self.fields["telegram_id"].required = False
        self.fields["web_preset"].required = False
        self.fields["type"].widget.attrs["data-role"] = "source-type"
        self.fields["preset_file"].widget.attrs["data-role"] = "preset-file-input"
        self.fields["web_preset"].queryset = WebPreset.objects.order_by("name", "version")
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

        file_field = self.files.get("preset_file")
        if file_field:
            try:
                payload_text = file_field.read().decode("utf-8")
            except UnicodeDecodeError as exc:
                raise forms.ValidationError("Не удалось прочитать JSON из файла пресета.") from exc
            cleaned["preset_payload"] = payload_text

        source_type = cleaned.get("type") or Source.Type.TELEGRAM
        if source_type == Source.Type.WEB:
            preset = cleaned.get("web_preset")
            payload = self.cleaned_data.get("preset_payload")
            if payload:
                try:
                    preset = self._get_registry().import_payload(payload)
                except PresetValidationError as exc:
                    self.add_error("preset_payload", str(exc))
                    raise forms.ValidationError("Пресет не прошёл валидацию.")
            if not preset:
                raise forms.ValidationError("Выберите пресет или импортируйте JSON-файл.")
            cleaned["web_preset"] = preset
            cleaned["username"] = ""
            cleaned["invite_link"] = ""
            cleaned["telegram_id"] = None
        else:
            if not username and not invite and telegram_id is None:
                raise forms.ValidationError("Укажите @username, ссылку на канал или инвайт-ссылку.")
        return cleaned

    def save(self, commit: bool = True) -> Source:
        source: Source = super().save(commit=False)
        source.project = self.project
        if source.type == Source.Type.WEB and source.web_preset:
            source.web_preset_snapshot = source.web_preset.config
            source.username = ""
            source.invite_link = ""
            source.telegram_id = None
        if not (source.title or "").strip():
            source.title = self._generate_title(source)
        if commit:
            source.save()
        if source.type == Source.Type.TELEGRAM:
            enqueue_source_refresh(source)
        return source

    def _generate_title(self, source: Source) -> str:
        if source.type == Source.Type.WEB:
            if source.web_preset and source.web_preset.title:
                return source.web_preset.title
            if source.web_preset:
                return source.web_preset.name
            return "Web-источник"
        username = (source.username or "").strip()
        if username:
            if not username.startswith("@"):
                return f"@{username}"
            return username
        telegram_id = source.telegram_id
        if telegram_id:
            return f"Канал {telegram_id}"
        invite = (source.invite_link or "").strip()
        if invite:
            return invite
        return "Источник"

    def _get_registry(self) -> WebPresetRegistry:
        if self._preset_registry is None:
            self._preset_registry = WebPresetRegistry()
        return self._preset_registry


class SourceCreateForm(SourceBaseForm):
    """Создание нового источника."""


class SourceUpdateForm(SourceBaseForm):
    """Редактирование существующего источника."""


class ProjectPromptConfigForm(forms.ModelForm):
    """Редактирование основного шаблона промтов проекта."""

    class Meta:
        model = ProjectPromptConfig
        fields = [
            "system_role",
            "task_instruction",
            "documents_intro",
            "style_requirements",
            "output_format",
            "output_example",
            "editor_comment_note",
        ]
        widgets = {
            field: forms.Textarea(
                attrs={
                    "class": "form-control font-monospace",
                    "rows": 4 if field not in {"output_format", "output_example"} else 8,
                }
            )
            for field in fields
        }
        labels = {
            "system_role": "Системная роль",
            "task_instruction": "Задание",
            "documents_intro": "Источники / документы",
            "style_requirements": "Требования к стилю",
            "output_format": "Формат ответа (JSON)",
            "output_example": "Пример корректного вывода",
            "editor_comment_note": "Комментарий редактора",
        }
        help_texts = {
            "system_role": "Например: «Ты — редактор ... {{PROJECT_NAME}}».",
            "documents_intro": "Вставьте {{POSTS}}, чтобы книга новостей появилась на месте шаблона.",
            "editor_comment_note": "Используйте {{EDITOR_COMMENT}}, чтобы подставить текст редактора.",
        }
