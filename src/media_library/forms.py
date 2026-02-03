"""Forms for media library and studio."""

from __future__ import annotations

from django import forms


class MediaAssetUploadForm(forms.Form):
    title = forms.CharField(
        label="Название",
        required=False,
        max_length=200,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Название для поиска"}
        ),
    )
    image_file = forms.FileField(
        label="Файл",
        required=True,
        widget=forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": "image/*,video/*"}
        ),
        error_messages={"required": "Выберите файл"},
    )
    tags = forms.CharField(
        label="Теги",
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Теги через запятую"}
        ),
    )
