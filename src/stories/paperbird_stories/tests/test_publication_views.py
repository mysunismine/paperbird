"""Tests for publication management view."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from projects.models import Post, Project, Source
from stories.paperbird_stories.models import Publication
from stories.paperbird_stories.services import StoryFactory

User = get_user_model()


class PublicationListManageTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("publisher", password="pass")
        self.client.force_login(self.user)
        self.project = Project.objects.create(
            owner=self.user,
            name="Контент",
            publish_target="@mainchannel",
        )
        self.source = Source.objects.create(project=self.project, telegram_id=500, title="Новости")
        post = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=42,
            message="Новость",
            posted_at=timezone.now(),
        )
        self.story = StoryFactory(project=self.project).create(post_ids=[post.id], title="Бриф")
        self.story.apply_rewrite(
            title="Бриф",
            summary="",
            body="Текст публикации",
            hashtags=[],
            sources=[],
            payload={},
        )
        self.publication = self._create_publication()

    def _create_publication(self, **override) -> Publication:
        defaults: dict = {
            "story": self.story,
            "target": "@fallback",
            "status": Publication.Status.SCHEDULED,
            "result_text": "Исходный текст",
            "scheduled_for": timezone.now() + timedelta(hours=1),
        }
        defaults.update(override)
        return Publication.objects.create(**defaults)

    def _prefix(self, publication: Publication | None = None) -> str:
        publication = publication or self.publication
        return f"publication-{publication.pk}"

    def _base_post_data(self, publication: Publication | None = None) -> dict[str, str]:
        publication = publication or self.publication
        prefix = self._prefix(publication)
        return {
            "publication_id": str(publication.pk),
            "page": "1",
            f"{prefix}-id": str(publication.pk),
        }

    def test_update_publication_manages_fields(self) -> None:
        prefix = self._prefix()
        data = self._base_post_data()
        data.update(
            {
                "submit_action": "save",
                f"{prefix}-status": Publication.Status.FAILED,
                f"{prefix}-target": "https://t.me/newchannel",
                f"{prefix}-scheduled_for": "",
                f"{prefix}-published_at": "",
                f"{prefix}-result_text": "Обновлённый текст",
                f"{prefix}-error_message": "Требуется повторная отправка",
            }
        )

        response = self.client.post(reverse("stories:publications"), data=data)

        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, Publication.Status.FAILED)
        self.assertEqual(self.publication.target, "@newchannel")
        self.assertIsNone(self.publication.scheduled_for)
        self.assertEqual(self.publication.result_text, "Обновлённый текст")
        self.assertEqual(self.publication.error_message, "Требуется повторная отправка")

    def test_mark_published_without_timestamp_sets_now(self) -> None:
        prefix = self._prefix()
        data = self._base_post_data()
        data.update(
            {
                "submit_action": "save",
                f"{prefix}-status": Publication.Status.PUBLISHED,
                f"{prefix}-target": "@mainchannel",
                f"{prefix}-scheduled_for": "",
                f"{prefix}-published_at": "",
                f"{prefix}-result_text": "Готово",
                f"{prefix}-error_message": "",
            }
        )

        before = timezone.now()
        response = self.client.post(reverse("stories:publications"), data=data)
        after = timezone.now()

        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        self.publication.refresh_from_db()
        self.assertEqual(self.publication.status, Publication.Status.PUBLISHED)
        self.assertIsNotNone(self.publication.published_at)
        self.assertGreaterEqual(self.publication.published_at, before)
        self.assertLessEqual(self.publication.published_at, after + timedelta(seconds=5))

    def test_delete_publication_removes_record(self) -> None:
        prefix = self._prefix()
        data = self._base_post_data()
        data.update(
            {
                "submit_action": "delete",
                f"{prefix}-status": self.publication.status,
                f"{prefix}-target": self.publication.target,
                f"{prefix}-scheduled_for": "",
                f"{prefix}-published_at": "",
                f"{prefix}-result_text": self.publication.result_text,
                f"{prefix}-error_message": self.publication.error_message,
            }
        )

        response = self.client.post(reverse("stories:publications"), data=data)

        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        self.assertFalse(
            Publication.objects.filter(pk=self.publication.pk).exists()
        )

    def test_other_user_cannot_modify_publication(self) -> None:
        other = User.objects.create_user("hacker", password="pass")
        self.client.force_login(other)
        prefix = self._prefix()
        data = self._base_post_data()
        data.update(
            {
                "submit_action": "save",
                f"{prefix}-status": Publication.Status.PUBLISHED,
                f"{prefix}-target": "@mainchannel",
                f"{prefix}-scheduled_for": "",
                f"{prefix}-published_at": "",
                f"{prefix}-result_text": "Готово",
                f"{prefix}-error_message": "",
            }
        )

        response = self.client.post(reverse("stories:publications"), data=data)

        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_published_link_uses_project_target(self) -> None:
        self._create_publication(
            status=Publication.Status.PUBLISHED,
            message_ids=[101],
            published_at=timezone.now(),
        )
        response = self.client.get(reverse("stories:publications"))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "https://t.me/mainchannel/101")
