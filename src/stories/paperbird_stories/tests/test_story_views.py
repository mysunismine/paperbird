"""Integration tests for story-related views."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import timedelta
from http import HTTPStatus
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from projects.models import Post, Project, Source
from stories.paperbird_stories.models import Publication, Story
from stories.paperbird_stories.services import StoryFactory

User = get_user_model()


class StoryViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("viewer", password="pass")
        self.client.force_login(self.user)
        self.project = Project.objects.create(
            owner=self.user,
            name="Newsroom",
            publish_target="@newsroom",
        )
        self.source = Source.objects.create(project=self.project, telegram_id=500)
        self.post = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=100,
            message="Текст для истории",
            posted_at=timezone.now(),
        )
        self.story = StoryFactory(project=self.project).create(
            post_ids=[self.post.id],
            title="Story",
        )
        self.story.apply_rewrite(
            title="Story",
            summary="",
            body="Text",
            hashtags=["news"],
            sources=["source"],
            payload={},
        )

    def test_story_list_view(self) -> None:
        response = self.client.get(reverse("stories:list"))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        html = response.content.decode("utf-8")
        self.assertIn("Сюжеты", html)
        self.assertIn("Удалить", html)
        self.assertNotIn('<h2 class="h5">Проекты</h2>', html)
        self.assertNotIn("Создать сюжет", html)

    def test_story_detail_has_no_back_to_list_button(self) -> None:
        response = self.client.get(reverse("stories:detail", args=[self.story.pk]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, self.story.title)
        self.assertNotContains(response, "К списку")

    def test_publish_blocked_without_project_target(self) -> None:
        self.project.publish_target = ""
        self.project.save(update_fields=["publish_target"])
        url = reverse("stories:detail", args=[self.story.pk])
        response = self.client.get(url)
        self.assertContains(response, "Укажите целевой канал", status_code=HTTPStatus.OK)
        publish_response = self.client.post(
            url,
            data={"action": "publish", "target": "@custom"},
            follow=True,
        )
        self.assertEqual(publish_response.status_code, HTTPStatus.OK)
        self.assertContains(
            publish_response,
            "Укажите целевой канал в настройках проекта",
        )

    def test_create_story_via_selection(self) -> None:
        url = reverse("stories:create")
        response = self.client.post(
            url,
            data={"project": self.project.id, "posts": [self.post.id]},
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        story = Story.objects.order_by("-created_at").first()
        assert story is not None
        self.assertEqual(story.project, self.project)
        self.assertEqual(list(story.ordered_posts().values_list("id", flat=True)), [self.post.id])

    def test_delete_story_from_list(self) -> None:
        delete_url = reverse("stories:delete", kwargs={"pk": self.story.pk})
        response = self.client.post(delete_url, follow=True)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertFalse(Story.objects.filter(pk=self.story.pk).exists())
        self.assertContains(response, "Сюжет «Story» удалён.")

    def test_delete_story_requires_owner(self) -> None:
        delete_url = reverse("stories:delete", kwargs={"pk": self.story.pk})
        other = User.objects.create_user("outsider", password="pass")
        self.client.logout()
        self.client.force_login(other)
        response = self.client.post(delete_url)
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)
        self.assertTrue(Story.objects.filter(pk=self.story.pk).exists())

    @patch("stories.paperbird_stories.views.story_detail.default_rewriter")
    def test_rewrite_preview_shows_prompt(self, mock_rewriter) -> None:
        mock_instance = MagicMock()
        mock_rewriter.return_value = mock_instance
        url = reverse("stories:detail", kwargs={"pk": self.story.pk})
        response = self.client.post(
            url,
            data={"action": "rewrite", "editor_comment": "", "preview": "1"},
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        content = response.content.decode("utf-8")
        self.assertIn("Проверьте промпт перед отправкой", content)
        self.assertIn("1. [СИСТЕМНАЯ РОЛЬ]", content)
        self.assertIn("НОВОСТЬ #1", content)
        self.assertIn("Текст для истории", content)
        mock_rewriter.assert_not_called()
        mock_instance.rewrite.assert_not_called()

    def test_preview_shows_last_prompt_snapshot(self) -> None:
        self.story.prompt_snapshot = [
            {"role": "system", "content": "System snapshot"},
            {"role": "user", "content": "User snapshot"},
        ]
        self.story.editor_comment = "Сохрани факты"
        self.story.save(update_fields=["prompt_snapshot", "editor_comment", "updated_at"])
        self.story.refresh_from_db()

        url = reverse("stories:detail", kwargs={"pk": self.story.pk})
        response = self.client.post(
            url,
            data={
                "action": "rewrite",
                "editor_comment": "Сохрани факты",
                "preview": "1",
            },
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        html = response.content.decode("utf-8")
        self.assertIn("System snapshot", html)
        self.assertIn("User snapshot", html)

    @patch("stories.paperbird_stories.views.story_detail.default_rewriter")
    def test_rewrite_without_preview_runs_immediately(self, mock_rewriter) -> None:
        mock_instance = MagicMock()
        mock_rewriter.return_value = mock_instance
        url = reverse("stories:detail", kwargs={"pk": self.story.pk})
        response = self.client.post(
            url,
            data={"action": "rewrite", "editor_comment": ""},
            follow=True,
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        mock_rewriter.assert_called_once_with(project=self.story.project)
        mock_instance.rewrite.assert_called_once()
        args, kwargs = mock_instance.rewrite.call_args
        self.assertEqual(args[0], self.story)
        self.assertEqual(kwargs.get("editor_comment"), "")
        self.assertIsNone(kwargs.get("preset"))

    @patch("stories.paperbird_stories.views.story_detail.default_rewriter")
    def test_rewrite_confirm_triggers_call(self, mock_rewriter) -> None:
        mock_instance = MagicMock()
        mock_rewriter.return_value = mock_instance
        url = reverse("stories:detail", kwargs={"pk": self.story.pk})
        response = self.client.post(
            url,
            data={
                "action": "rewrite",
                "editor_comment": "",
                "prompt_confirm": "1",
                "prompt_system": "system",
                "prompt_user": "user",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        mock_rewriter.assert_called_once_with(project=self.story.project)
        mock_instance.rewrite.assert_called_once()
        _args, kwargs = mock_instance.rewrite.call_args
        self.assertEqual(
            kwargs.get("messages_override"),
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
        )

    def test_save_action_updates_story(self) -> None:
        url = reverse("stories:detail", kwargs={"pk": self.story.pk})
        response = self.client.post(
            url,
            data={
                "action": "save",
                "title": "Новый заголовок",
                "body": "Основной текст",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.story.refresh_from_db()
        self.assertEqual(self.story.title, "Новый заголовок")
        self.assertEqual(self.story.body, "Основной текст")

    @patch("stories.paperbird_stories.views.story_detail.default_publisher_for_story")
    def test_publish_action(self, mock_publisher_factory) -> None:
        mock_publisher = MagicMock()
        mock_publisher.publish.return_value.status = Publication.Status.PUBLISHED
        mock_publisher.publish.return_value.message_ids = [1]
        mock_publisher.publish.return_value.target = "@ch"
        mock_publisher_factory.return_value = mock_publisher
        url = reverse("stories:detail", kwargs={"pk": self.story.pk})
        response = self.client.post(url, data={"action": "publish", "target": "@ch"}, follow=True)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        mock_publisher.publish.assert_called_once()

    @patch("stories.paperbird_stories.views.story_detail.default_publisher_for_story")
    def test_publish_action_with_schedule(self, mock_publisher_factory) -> None:
        mock_publisher = MagicMock()
        publication = MagicMock()
        publication.status = Publication.Status.SCHEDULED
        mock_publisher.publish.return_value = publication
        mock_publisher_factory.return_value = mock_publisher
        url = reverse("stories:detail", kwargs={"pk": self.story.pk})
        future = timezone.localtime(timezone.now() + timedelta(hours=1))
        response = self.client.post(
            url,
            data={
                "action": "publish",
                "target": "@ch",
                "publish_at": future.strftime("%Y-%m-%dT%H:%M"),
            },
            follow=True,
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        mock_publisher.publish.assert_called_once()
        _, kwargs = mock_publisher.publish.call_args
        self.assertIn("scheduled_for", kwargs)
        self.assertIsNotNone(kwargs["scheduled_for"])
        self.assertTrue(timezone.is_aware(kwargs["scheduled_for"]))

    def test_publication_list_view(self) -> None:
        Publication.objects.create(
            story=self.story,
            target="@channel",
            status=Publication.Status.PUBLISHED,
            message_ids=[1],
        )
        response = self.client.get(reverse("stories:publications"))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        content = response.content.decode("utf-8")
        self.assertIn("Публикации", content)
        self.assertIn("Сохранить", content)

    @patch("stories.paperbird_stories.views.story_detail.httpx.get")
    def test_attach_media_downloads_external_image(self, mock_httpx_get) -> None:
        media_root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(media_root, ignore_errors=True))
        mock_httpx_get.return_value = MagicMock(
            status_code=200,
            content=b"external-image",
            headers={"content-type": "image/jpeg"},
        )
        external_url = "https://example.com/photo.jpg"
        post = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=321,
            message="Пост с внешним изображением",
            posted_at=timezone.now(),
            has_media=True,
            images_manifest=[external_url],
        )
        self.story.attach_posts([post])

        with self.settings(MEDIA_ROOT=media_root, MEDIA_URL="/media/"):
            url = reverse("stories:detail", kwargs={"pk": self.story.pk})
            response = self.client.post(
                url,
                data={"action": "attach_media", "media_post_id": [post.id]},
                follow=True,
            )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        mock_httpx_get.assert_called_once_with(external_url, timeout=60.0)
        self.story.refresh_from_db()
        post.refresh_from_db()
        self.assertTrue(post.media_path)
        self.assertTrue(self.story.image_file)
        stored_path = os.path.join(media_root, self.story.image_file.name)
        self.assertTrue(os.path.exists(stored_path))

    def test_story_detail_enables_local_manifest_media(self) -> None:
        media_root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(media_root, ignore_errors=True))
        rel_path = os.path.join("uploads", "media", "manifest.jpg")
        full_path = os.path.join(media_root, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as handle:
            handle.write(b"img")

        with self.settings(MEDIA_ROOT=media_root, MEDIA_URL="/media/"):
            post = Post.objects.create(
                project=self.project,
                source=self.source,
                telegram_id=222,
                message="Пост с локальным манифестом",
                posted_at=timezone.now(),
                has_media=True,
                images_manifest=[f"/media/{rel_path}"],
            )
            story = StoryFactory(project=self.project).create(
                post_ids=[post.id],
                title="Manifest story",
            )
            response = self.client.get(reverse("stories:detail", args=[story.pk]))

        html = response.content.decode("utf-8")
        self.assertIn('id="media-0-0"', html)
        self.assertNotIn("Файл недоступен", html)
