from http import HTTPStatus

from django.test import TestCase
from django.urls import reverse

from projects.models import Post, Project, Source

from . import User


class ManualPostCreateViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("manual-user", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Ручной ввод")

    def test_manual_post_form_renders(self) -> None:
        response = self.client.get(
            reverse("projects:manual-post", args=[self.project.id])
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Добавить редакторский текст")

    def test_manual_post_creates_feed_item(self) -> None:
        response = self.client.post(
            reverse("projects:manual-post", args=[self.project.id]),
            data={"title": "Заметка", "body": "Текст для проверки"},
        )
        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        self.assertEqual(Post.objects.filter(project=self.project).count(), 1)

        post = Post.objects.get(project=self.project)
        self.assertEqual(post.origin_type, Post.Origin.MANUAL)
        self.assertEqual(post.source.type, Source.Type.MANUAL)
        self.assertIn("Текст для проверки", post.message)
        self.assertEqual(post.versions.count(), 1)


class ManualPostUpdateViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("manual-editor", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Редакторский проект")
        self.source = Source.objects.create(
            project=self.project,
            type=Source.Type.MANUAL,
            title="Редакторский текст",
        )
        self.post = Post.create_manual(
            project=self.project,
            source=self.source,
            title="Заметка",
            body="Первый текст",
            created_by=self.user,
        )

    def test_manual_post_update_creates_version(self) -> None:
        response = self.client.post(
            reverse("projects:manual-post-edit", args=[self.project.id, self.post.id]),
            data={"title": "Обновлённый заголовок", "body": "Обновлённый текст"},
        )
        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        post = Post.objects.get(pk=self.post.pk)
        self.assertIn("Обновлённый текст", post.message)
        self.assertEqual(post.versions.count(), 2)

    def test_manual_post_version_detail(self) -> None:
        version = self.post.versions.first()
        response = self.client.get(
            reverse(
                "projects:manual-post-version",
                args=[self.project.id, self.post.id, version.id],
            )
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Версия редакторского текста")
