"""Tests for story image view and related helpers."""

from __future__ import annotations

import base64
import os
import shutil
import tempfile
from http import HTTPStatus
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.constants import IMAGE_DEFAULT_QUALITY
from projects.models import Post, Project, Source
from stories.paperbird_stories.forms import StoryImageGenerateForm
from stories.paperbird_stories.models import Story
from stories.paperbird_stories.services import GeneratedImage, default_image_generator

User = get_user_model()


class StoryImageViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("artist", password="pass")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Art")
        self.story = Story.objects.create(
            project=self.project,
            title="История",
            summary="Закат над морем",
        )

    def test_get_renders_form(self) -> None:
        response = self.client.get(reverse("stories:image", kwargs={"pk": self.story.pk}))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        generate_form = response.context["generate_form"]
        self.assertIsInstance(generate_form, StoryImageGenerateForm)
        self.assertEqual(generate_form.initial["model"], self.project.image_model)
        self.assertEqual(generate_form.initial["size"], self.project.image_size)
        self.assertEqual(generate_form.initial["quality"], self.project.image_quality)
        self.assertIn("Сгенерировать", response.content.decode("utf-8"))

    def test_initial_normalizes_legacy_quality(self) -> None:
        self.project.image_quality = "standard"
        self.project.save(update_fields=["image_quality"])
        response = self.client.get(reverse("stories:image", kwargs={"pk": self.story.pk}))
        form_quality = response.context["generate_form"].initial["quality"]
        self.assertEqual(form_quality, IMAGE_DEFAULT_QUALITY)

    @patch("stories.paperbird_stories.views.story_images.default_image_generator")
    def test_generate_action_displays_preview(self, mock_generator) -> None:
        stub_generator = MagicMock()
        stub_generator.generate.return_value = GeneratedImage(data=b"image", mime_type="image/png")
        mock_generator.return_value = stub_generator

        response = self.client.post(
            reverse("stories:image", kwargs={"pk": self.story.pk}),
            data={
                "action": "generate",
                "prompt": "Яркий закат",
                "model": self.project.image_model,
                "size": self.project.image_size,
                "quality": self.project.image_quality,
            },
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertIn("data:image/png;base64", response.content.decode("utf-8"))
        mock_generator.assert_called_once_with(model=self.project.image_model)
        stub_generator.generate.assert_called_once_with(
            prompt="Яркий закат",
            model=self.project.image_model,
            size=self.project.image_size,
            quality=self.project.image_quality,
        )

    def test_attach_action_saves_file(self) -> None:
        url = reverse("stories:image", kwargs={"pk": self.story.pk})
        encoded = base64.b64encode(b"fake-image").decode("ascii")
        media_root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(media_root, ignore_errors=True))

        with override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                url,
                data={
                    "action": "attach",
                    "prompt": "Летний пляж",
                    "image_data": encoded,
                    "mime_type": "image/png",
                },
            )

            self.assertEqual(response.status_code, HTTPStatus.FOUND)
            self.story.refresh_from_db()
            self.assertEqual(self.story.image_prompt, "Летний пляж")
            self.assertTrue(self.story.image_file.name)
            stored_path = os.path.join(settings.MEDIA_ROOT, self.story.image_file.name)
            self.assertTrue(os.path.exists(stored_path))

    def test_attach_source_uses_post_media(self) -> None:
        media_root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(media_root, ignore_errors=True))
        source = Source.objects.create(project=self.project, telegram_id=1)
        media_rel_path = os.path.join("uploads", "media", "photo.png")
        full_path = os.path.join(media_root, media_rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as handle:
            handle.write(b"original-image")

        post = Post.objects.create(
            project=self.project,
            source=source,
            telegram_id=123,
            message="Пост с фото",
            posted_at=timezone.now(),
            has_media=True,
            media_path=media_rel_path,
        )
        self.story.attach_posts([post])

        with override_settings(MEDIA_ROOT=media_root, MEDIA_URL="/media/"):
            response = self.client.post(
                reverse("stories:image", kwargs={"pk": self.story.pk}),
                data={
                    "action": "attach_source",
                    "post_id": post.pk,
                },
                follow=True,
            )

            self.assertEqual(response.status_code, HTTPStatus.OK)
            self.story.refresh_from_db()
            self.assertTrue(self.story.image_file)
            stored_path = os.path.join(media_root, self.story.image_file.name)
            self.assertTrue(os.path.exists(stored_path))
        with open(stored_path, "rb") as saved:
            self.assertEqual(saved.read(), b"original-image")
        self.assertIn("Оригинальное изображение", self.story.image_prompt)

    def test_remove_action_deletes_file(self) -> None:
        media_root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(media_root, ignore_errors=True))

        with override_settings(MEDIA_ROOT=media_root):
            self.story.attach_image(prompt="Preview", data=b"img", mime_type="image/png")
            stored_path = os.path.join(settings.MEDIA_ROOT, self.story.image_file.name)
            self.assertTrue(os.path.exists(stored_path))

            response = self.client.post(
                reverse("stories:image", kwargs={"pk": self.story.pk}),
                data={"action": "remove", "confirm": "True"},
                follow=True,
            )

            self.assertEqual(response.status_code, HTTPStatus.OK)
            self.story.refresh_from_db()
            self.assertFalse(self.story.image_file)
            self.assertFalse(os.path.exists(stored_path))


class YandexProviderRoutingTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("y-ops", password="pass")
        self.project = Project.objects.create(
            owner=self.user,
            name="Yandex",
            rewrite_model="yandexgpt-lite",
        )

    @override_settings(YANDEX_API_KEY="key", YANDEX_FOLDER_ID="folder")
    def test_default_rewriter_uses_yandex_provider(self) -> None:
        from stories.paperbird_stories.services import YandexGPTProvider, default_rewriter

        rewriter = default_rewriter(project=self.project)
        self.assertIsInstance(rewriter.provider, YandexGPTProvider)

    @override_settings(YANDEX_API_KEY="key", YANDEX_FOLDER_ID="folder")
    def test_default_image_generator_uses_yandex_provider(self) -> None:
        from stories.paperbird_stories.services import YandexArtProvider

        generator = default_image_generator(model="yandex-art")
        self.assertIsInstance(generator.provider, YandexArtProvider)
