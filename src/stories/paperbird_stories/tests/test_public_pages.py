from __future__ import annotations

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from projects.models import Project
from projects.tests import User
from stories.paperbird_stories.models import Publication, Story


class PublicPagesTests(TestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user("reader", password="secret")
        self.project = Project.objects.create(
            owner=self.owner,
            name="Публичный проект",
            public_enabled=True,
            public_noindex=False,
        )
        self.story = Story.objects.create(
            project=self.project,
            title="Сюжет для публикации",
            body="Полный текст публикации.",
            status=Story.Status.PUBLISHED,
        )
        self.published = Publication.objects.create(
            story=self.story,
            target="@public",
            status=Publication.Status.PUBLISHED,
            result_text="Опубликованный текст",
            published_at=timezone.now(),
        )
        Publication.objects.create(
            story=self.story,
            target="@public",
            status=Publication.Status.SCHEDULED,
            result_text="Запланированный текст",
        )
        other_project = Project.objects.create(
            owner=self.owner,
            name="Другой проект",
            public_enabled=True,
        )
        other_story = Story.objects.create(project=other_project, title="Чужой сюжет")
        Publication.objects.create(
            story=other_story,
            target="@other",
            status=Publication.Status.PUBLISHED,
            result_text="Не должен появиться",
            published_at=timezone.now(),
        )

    def test_public_project_list_shows_only_published(self) -> None:
        url = reverse("public:project", args=[self.project.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Сюжет для публикации")
        self.assertNotContains(response, "Запланированный текст")
        self.assertNotContains(response, "Чужой сюжет")

    def test_publication_detail(self) -> None:
        url = reverse("public:publication", args=[self.project.id, self.published.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Опубликованный текст")

    def test_publication_detail_requires_published_status(self) -> None:
        scheduled = Publication.objects.filter(status=Publication.Status.SCHEDULED).first()
        url = reverse("public:publication", args=[self.project.id, scheduled.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_public_index_lists_public_projects(self) -> None:
        url = reverse("public:index")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Публичный проект")

    def test_private_project_is_hidden(self) -> None:
        private_project = Project.objects.create(
            owner=self.owner,
            name="Приватный проект",
            public_enabled=False,
        )
        url = reverse("public:project", args=[private_project.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)
