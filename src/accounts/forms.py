"""Формы управления профилем пользователя."""

from django import forms

from accounts.models import User
from core.utils.telethon import normalize_session_value


class UserProfileForm(forms.ModelForm):
    """Форма обновления профиля и ключей Telethon."""

    telethon_session = forms.CharField(
        label="Telethon session",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
        help_text="Используйте строку из Telethon StringSession.",
    )

    class Meta:
        model = User
        fields = (
            "first_name",
            "last_name",
            "email",
            "telethon_api_id",
            "telethon_api_hash",
            "telethon_session",
        )
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "telethon_api_id": forms.NumberInput(attrs={"class": "form-control"}),
            "telethon_api_hash": forms.TextInput(attrs={"class": "form-control"}),
        }
        help_texts = {
            "telethon_api_id": "Выдаётся в кабинете my.telegram.org",
            "telethon_api_hash": "Секретный ключ приложения. Не делитесь им.",
        }

    def clean(self):
        data = super().clean()
        api_id = data.get("telethon_api_id")
        api_hash = data.get("telethon_api_hash")
        session = data.get("telethon_session")

        if api_id and not api_hash:
            raise forms.ValidationError("Укажите Telethon API hash.")
        if api_hash and not api_id:
            raise forms.ValidationError("Укажите Telethon API ID.")
        if session and not (api_id and api_hash):
            raise forms.ValidationError(
                "Для сохранения сессии заполните Telethon API ID и API hash."
            )
        return data

    def save(self, commit=True):
        user: User = super().save(commit=False)
        # Чистим пробельные символы, чтобы избежать случайных ошибок.
        if user.telethon_api_hash:
            user.telethon_api_hash = user.telethon_api_hash.strip()
        if user.telethon_session:
            user.telethon_session = normalize_session_value(user.telethon_session)
        if commit:
            user.save()
        return user


class TelethonSessionStartForm(forms.Form):
    """Форма запроса кода подтверждения для Telethon."""

    phone = forms.CharField(
        label="Номер телефона",
        help_text="Введите номер в международном формате, например +79990000000",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "+79990000000"}),
    )
    force_sms = forms.BooleanField(
        label="Запросить SMS",
        required=False,
        help_text="Если код не приходит в Telegram, можно принудительно отправить SMS",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean_phone(self) -> str:
        phone = self.cleaned_data["phone"].strip()
        if not phone.startswith("+"):
            raise forms.ValidationError(
                "Номер должен быть указан в международном формате, начиная с +"
            )
        if len(phone) < 10:
            raise forms.ValidationError("Проверьте длину номера телефона")
        return phone


class TelethonSessionCodeForm(forms.Form):
    """Форма подтверждения кода и пароля 2FA для Telethon."""

    code = forms.CharField(
        label="Код из Telegram",
        max_length=10,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Код из сообщения",
                "autocomplete": "one-time-code",
            }
        ),
    )
    password = forms.CharField(
        label="Пароль 2FA",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
        help_text="Заполните, если в Telegram включена двухфакторная аутентификация",
    )
