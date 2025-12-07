"""Tests for story factory behaviour."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from projects.models import Post, Project, Source
from stories.paperbird_stories.services import StoryCreationError, StoryFactory

User = get_user_model()


class StoryFactoryTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("editor", password="pass")
        self.project = Project.objects.create(owner=self.user, name="News")
        self.source = Source.objects.create(project=self.project, telegram_id=1000, title="Source")
        base_time = datetime(2024, 1, 1, tzinfo=ZoneInfo("UTC"))
        self.post_a = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=1,
            message="Первый пост",
            posted_at=base_time,
        )
        self.post_b = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=2,
            message="Второй пост",
            posted_at=base_time,
        )

    def test_create_story_preserves_order(self) -> None:
        factory = StoryFactory(project=self.project)
        story = factory.create(post_ids=[self.post_b.id, self.post_a.id], title="Draft")

        ordered_ids = list(story.ordered_posts().values_list("id", flat=True))
        self.assertEqual([self.post_b.id, self.post_a.id], ordered_ids)
        self.assertEqual(story.title, "Draft")

    def test_factory_rejects_foreign_posts(self) -> None:
        other_project = Project.objects.create(owner=self.user, name="Other")
        other_source = Source.objects.create(project=other_project, telegram_id=2000)
        foreign_post = Post.objects.create(
            project=other_project,
            source=other_source,
            telegram_id=3,
            message="Чужой пост",
            posted_at=timezone.now(),
        )
        factory = StoryFactory(project=self.project)
        with self.assertRaises(StoryCreationError) as ctx:
            factory.create(post_ids=[self.post_a.id, foreign_post.id])
        self.assertIn("не найдены", str(ctx.exception))

    @patch("projects.services.media_downloader.httpx.get")
    def test_factory_downloads_external_manifest_media(self, mock_httpx_get) -> None:
        media_root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(media_root, ignore_errors=True))
        mock_httpx_get.return_value.status_code = 200
        mock_httpx_get.return_value.content = b"image-bytes"
        mock_httpx_get.return_value.headers = {"content-type": "image/jpeg"}
        external_url = "https://cdn.example.com/photo.jpg"

        post_with_manifest = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=99,
            message="Пост с внешним фото",
            posted_at=timezone.now(),
            images_manifest=[external_url],
        )
        factory = StoryFactory(project=self.project)
        with self.settings(MEDIA_ROOT=media_root, MEDIA_URL="/media/"):
            story = factory.create(post_ids=[post_with_manifest.id], title="With media")

        post_with_manifest.refresh_from_db()
        self.assertTrue(post_with_manifest.media_path)
        manifest_entry = post_with_manifest.images_manifest[0]
        self.assertIn("/media/uploads/", manifest_entry["url"])
        stored_path = os.path.join(media_root, post_with_manifest.media_path)
        self.assertTrue(os.path.exists(stored_path))
        self.assertEqual(story.posts.first(), post_with_manifest)
