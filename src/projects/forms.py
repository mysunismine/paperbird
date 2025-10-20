"""Form classes for project management UI."""

from __future__ import annotations

from django import forms

from projects.models import Project


class ProjectCreateForm(forms.ModelForm):
    """Form to create a project owned by the current user."""

    class Meta:
        model = Project
        fields = ["name", "description"]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "Рабочее название проекта",
                "maxlength": Project._meta.get_field("name").max_length,
            }),
            "description": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Опишите задачи проекта, чтобы коллегам было проще ориентироваться",
            }),
        }
        labels = {
            "name": "Название",
            "description": "Описание",
        }
        help_texts = {
            "name": "Название должно быть уникальным в рамках вашей команды",
            "description": "Необязательно, но помогает запомнить контекст и критерии сбора",
        }

    def __init__(self, *args, owner, **kwargs):  # type: ignore[override]
        if owner is None:
            raise ValueError("ProjectCreateForm requires an owner instance")
        self.owner = owner
        super().__init__(*args, **kwargs)

    def clean_name(self) -> str:
        name = self.cleaned_data["name"].strip()
        if Project.objects.filter(owner=self.owner, name__iexact=name).exists():
            raise forms.ValidationError(
                "У вас уже есть проект с таким названием. Выберите другое."
            )
        return name

    def save(self, commit: bool = True) -> Project:
        project = super().save(commit=False)
        project.owner = self.owner
        if commit:
            project.save()
        return project
