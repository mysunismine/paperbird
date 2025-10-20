"""Формы для работы с сюжетами."""

from __future__ import annotations

from typing import Any

from django import forms

from projects.models import Post, Project
from stories.paperbird_stories.models import RewritePreset, Story


class StoryCreateForm(forms.Form):
    project = forms.ModelChoiceField(label="Проект", queryset=Project.objects.none())
    posts = forms.ModelMultipleChoiceField(
        label="Посты",
        queryset=Post.objects.none(),
        widget=forms.SelectMultiple(attrs={"size": 12}),
        help_text="Выберите один или несколько постов для сюжета.",
    )
    title = forms.CharField(label="Заголовок", max_length=255, required=False)
    editor_comment = forms.CharField(
        label="Комментарий редактора",
        required=False,
        widget=forms.Textarea(attrs={"rows": 4}),
        help_text="Опционально: добавьте инструкции для рерайта.",
    )

    def __init__(self, *args: Any, user, project_id: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        projects = Project.objects.filter(owner=user).order_by("name")
        self.fields["project"].queryset = projects
        if project_id:
            self.fields["project"].initial = projects.filter(id=project_id).first()

        posts_qs = Post.objects.filter(project__owner=user).order_by("-posted_at")
        if project_id:
            posts_qs = posts_qs.filter(project_id=project_id)
        self.fields["posts"].queryset = posts_qs

    def clean_posts(self):
        posts = self.cleaned_data["posts"]
        if not posts:
            raise forms.ValidationError("Нужно выбрать хотя бы один пост")
        project: Project = self.cleaned_data.get("project")
        if project and any(post.project_id != project.id for post in posts):
            raise forms.ValidationError("Все посты должны принадлежать выбранному проекту")
        return posts


class StoryRewriteForm(forms.Form):
    preset = forms.ModelChoiceField(
        label="Пресет",
        queryset=RewritePreset.objects.none(),
        required=False,
        empty_label="Без пресета",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    editor_comment = forms.CharField(
        label="Комментарий",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )

    def __init__(self, *args: Any, story: Story | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        presets = RewritePreset.objects.none()
        if story is not None:
            presets = story.project.rewrite_presets.filter(is_active=True).order_by("name")
            if story.last_rewrite_preset:
                self.fields["preset"].initial = story.last_rewrite_preset
        self.fields["preset"].queryset = presets


class StoryPublishForm(forms.Form):
    target = forms.CharField(
        label="Канал или чат", max_length=255, help_text="Например, @my_channel или ссылку"
    )
