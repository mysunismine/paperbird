"""Forms for manual project posts."""

from __future__ import annotations

from django import forms


class ManualPostForm(forms.Form):
    title = forms.CharField(
        label="Заголовок",
        required=False,
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Краткий заголовок (опционально)",
            }
        ),
    )
    body = forms.CharField(
        label="Текст",
        required=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 10,
                "placeholder": "Вставьте текст новости или заметки",
            }
        ),
    )

    def clean_body(self) -> str:
        body = self.cleaned_data["body"].strip()
        if not body:
            raise forms.ValidationError("Текст не может быть пустым.")
        return body
