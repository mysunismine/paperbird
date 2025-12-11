"""Forms for project sources."""

from __future__ import annotations

from django import forms

from projects.models import Project, Source, WebPreset
from projects.services.source_metadata import enqueue_source_refresh
from projects.services.web_preset_registry import PresetValidationError, WebPresetRegistry


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
            "web_retry_max_attempts",
            "web_retry_base_delay",
            "web_retry_max_delay",
        ]
        widgets = {
            "type": forms.Select(attrs={"class": "form-select"}),
            "title": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Название (заполнится автоматически)",
                }
            ),
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
            "retention_days": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "step": 1}
            ),
            "web_retry_max_attempts": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "step": 1}
            ),
            "web_retry_base_delay": forms.NumberInput(
                attrs={"class": "form-control", "min": 5, "step": 5}
            ),
            "web_retry_max_delay": forms.NumberInput(
                attrs={"class": "form-control", "min": 5, "step": 5}
            ),
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
            "web_retry_max_attempts": "Максимум попыток веб-задачи",
            "web_retry_base_delay": "Базовая задержка ретрая (сек.)",
            "web_retry_max_delay": "Максимальная задержка ретрая (сек.)",
        }
        help_texts = {
            "type": "Выберите тип источника. От этого зависит, какие поля нужно будет заполнить.",
        }

    def __init__(self, *args, project: Project, **kwargs):
        is_create = kwargs.pop("is_create", False)

        self.project = project
        self._preset_registry: WebPresetRegistry | None = None
        super().__init__(*args, **kwargs)

        # On create, add an empty choice to force user selection.
        if is_create:
            self.fields["type"].choices = [("", "---------")] + self.fields["type"].choices
            self.fields["type"].initial = ""

        # Fields metadata
        self.fields["title"].required = False
        self.fields["username"].required = False
        self.fields["invite_link"].required = False
        self.fields["telegram_id"].required = False
        self.fields["web_preset"].required = False

        # CSS classes and data attributes for JS
        self.fields["type"].widget.attrs["data-role"] = "source-type"
        self.fields["preset_file"].widget.attrs["data-role"] = "preset-file-input"

        # --- Telegram fields
        self.fields["telegram_id"].widget.attrs["class"] += " source-telegram-field"
        self.fields["username"].widget.attrs["class"] += " source-telegram-field"
        self.fields["invite_link"].widget.attrs["class"] += " source-telegram-field"

        # --- Web fields
        self.fields["web_preset"].widget.attrs["class"] += " source-web-field"
        self.fields["preset_payload"].widget.attrs["class"] += " source-web-field"
        self.fields["preset_file"].widget.attrs["class"] += " source-web-field"
        self.fields["web_retry_max_attempts"].widget.attrs["class"] += " source-web-field"
        self.fields["web_retry_base_delay"].widget.attrs["class"] += " source-web-field"
        self.fields["web_retry_max_delay"].widget.attrs["class"] += " source-web-field"

        # Initial values and querysets
        self.fields["web_preset"].queryset = WebPreset.objects.order_by("name", "version")
        if not self.initial.get("retention_days"):
            self.fields["retention_days"].initial = project.retention_days
        if not self.initial.get("web_retry_max_attempts"):
            self.fields["web_retry_max_attempts"].initial = 5
        if not self.initial.get("web_retry_base_delay"):
            self.fields["web_retry_base_delay"].initial = 30
        if not self.initial.get("web_retry_max_delay"):
            self.fields["web_retry_max_delay"].initial = 900

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
        if value.startswith("@"):
            value = value[1:]
        value = value.strip("/")
        if value.startswith("+"):
            return ""
        return value

    def clean(self):
        cleaned = super().clean()
        source_type = cleaned.get("type")

        # Ensure a source type is always selected.
        if not source_type:
            raise forms.ValidationError(
                "Необходимо выбрать тип источника. Вернитесь назад и выберите один из вариантов."
            )

        username = cleaned.get("username")
        invite = (cleaned.get("invite_link") or "").strip()
        telegram_id = cleaned.get("telegram_id")
        raw_username = (self.data.get("username") or "").strip()

        if not username and raw_username:
            lower = raw_username.lower()
            if (
                lower.startswith("https://t.me/+")
                or lower.startswith("http://t.me/+")
                or "joinchat" in lower
            ):
                cleaned["invite_link"] = raw_username
                invite = raw_username

        file_field = self.files.get("preset_file")
        if file_field:
            try:
                payload_text = file_field.read().decode("utf-8")
            except UnicodeDecodeError as exc:
                raise forms.ValidationError("Не удалось прочитать JSON из файла пресета.") from exc
            cleaned["preset_payload"] = payload_text

        if source_type == Source.Type.WEB:
            preset = cleaned.get("web_preset")
            payload = self.cleaned_data.get("preset_payload")
            if payload:
                try:
                    preset = self._get_registry().import_payload(payload)
                except PresetValidationError as exc:
                    self.add_error("preset_payload", str(exc))
                    raise forms.ValidationError("Пресет не прошёл валидацию.") from exc
            if not preset:
                raise forms.ValidationError("Выберите пресет или импортируйте JSON-файл.")
            cleaned["web_preset"] = preset
            cleaned["username"] = ""
            cleaned["invite_link"] = ""
            cleaned["telegram_id"] = None
        else:  # Telegram
            if not username and not invite and telegram_id is None:
                raise forms.ValidationError("Укажите @username, ссылку на канал или инвайт-ссылку.")
            cleaned["web_preset"] = None
            cleaned["preset_payload"] = ""
            cleaned["preset_file"] = None
            cleaned["web_retry_max_attempts"] = None
            cleaned["web_retry_base_delay"] = None
            cleaned["web_retry_max_delay"] = None

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
