"""Формы управления профилем пользователя."""

from django import forms

from accounts.models import User


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
            user.telethon_session = user.telethon_session.strip()
        if commit:
            user.save()
        return user
