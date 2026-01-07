"""Forms for media library and studio."""

from __future__ import annotations

from django import forms


class MediaAssetUploadForm(forms.Form):
    image_file = forms.ImageField(
        label="Файл",
        required=True,
        widget=forms.ClearableFileInput(attrs={"class": "form-control"}),
        error_messages={"required": "Выберите изображение"},
    )
