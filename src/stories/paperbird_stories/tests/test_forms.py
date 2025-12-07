"""Form validation tests for stories app."""

from __future__ import annotations

import base64
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from projects.models import Project
from stories.paperbird_stories.forms import (
    StoryImageAttachForm,
    StoryImageGenerateForm,
    StoryPublishForm,
    StoryRewriteForm,
)
from stories.paperbird_stories.models import RewritePreset, Story

User = get_user_model()


class StoryRewriteFormTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("editor", password="pass")
        self.project = Project.objects.create(owner=self.user, name="Формы")
        self.story = Story.objects.create(project=self.project, title="Тестовая история")
        self.preset_a = RewritePreset.objects.create(
            project=self.project,
            name="Пресет А",
            style="деловой",
            editor_comment="Придерживайся фактов",
        )
        self.preset_b = RewritePreset.objects.create(
            project=self.project,
            name="Пресет Б",
            style="разговорный",
            editor_comment="Используй лёгкий тон",
        )
        self.story.last_rewrite_preset = self.preset_b
        self.story.save(update_fields=["last_rewrite_preset"])

    def test_form_limits_presets_to_story_project(self) -> None:
        form = StoryRewriteForm(story=self.story)
        preset_names = list(form.fields["preset"].queryset.values_list("name", flat=True))
        self.assertCountEqual(preset_names, ["Пресет А", "Пресет Б"])
        self.assertEqual(form.fields["preset"].initial, self.preset_b)


class StoryPublishFormTests(TestCase):
    def test_accepts_future_datetime(self) -> None:
        future = timezone.localtime(timezone.now() + timedelta(hours=2))
        form = StoryPublishForm(
            data={
                "target": "@channel",
                "publish_at": future.strftime("%Y-%m-%dT%H:%M"),
            }
        )

        self.assertTrue(form.is_valid())
        cleaned = form.cleaned_data["publish_at"]
        self.assertEqual(
            timezone.localtime(cleaned).replace(second=0, microsecond=0),
            future.replace(second=0, microsecond=0),
        )

    def test_rejects_past_datetime(self) -> None:
        past = timezone.localtime(timezone.now() - timedelta(minutes=5))
        form = StoryPublishForm(
            data={
                "target": "@channel",
                "publish_at": past.strftime("%Y-%m-%dT%H:%M"),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("publish_at", form.errors)

    def test_normalizes_target_links(self) -> None:
        form = StoryPublishForm(data={"target": "https://t.me/example"})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["target"], "@example")

    def test_requires_target(self) -> None:
        form = StoryPublishForm(data={"target": "   "})
        self.assertFalse(form.is_valid())
        self.assertIn("target", form.errors)


class StoryImageFormsTests(TestCase):
    def test_generate_form_requires_prompt(self) -> None:
        form = StoryImageGenerateForm(data={"prompt": "   "})
        self.assertFalse(form.is_valid())
        self.assertIn("prompt", form.errors)

    def test_attach_form_decodes_payload(self) -> None:
        encoded = base64.b64encode(b"binary").decode("ascii")
        form = StoryImageAttachForm(
            data={
                "prompt": "Sunset",
                "image_data": encoded,
                "mime_type": "image/png",
            }
        )
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["image_data"], b"binary")
