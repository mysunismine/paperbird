from datetime import timedelta
from http import HTTPStatus
import re

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import User
from projects.models import Post, Project, Source
from core.models import WorkerTask


class ProjectPostListViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("viewer", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Новости")
        self.other_project = Project.objects.create(owner=self.user, name="Архив")
        self.source = Source.objects.create(project=self.project, telegram_id=1, title="Tech")
        self.web_source = Source.objects.create(project=self.project, type=Source.Type.WEB, title="Site")
        Source.objects.create(project=self.other_project, telegram_id=2, title="Other")
        now = timezone.now()
        Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=10,
            message="Apple представила новый продукт",
            posted_at=now,
            language=Post.Language.RU,
            status=Post.Status.NEW,
        )
        Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=11,
            message="Google updated the service",
            posted_at=now - timedelta(days=1),
            language=Post.Language.EN,
            status=Post.Status.USED,
        )

    def test_post_list_page_renders(self) -> None:
        response = self.client.get(reverse("feed-detail", args=[self.project.id]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Лента проекта")
        self.assertContains(response, "Apple представила")
        self.assertContains(response, "Сделать сюжет")

    def test_post_list_filters_by_search(self) -> None:
        response = self.client.get(
            reverse("feed-detail", args=[self.project.id]),
            data={"search": "Google"},
        )
        self.assertContains(response, "Google updated the service")
        self.assertNotContains(response, "Apple представила")

    def test_post_list_links_to_detail_page(self) -> None:
        post = Post.objects.filter(project=self.project).first()
        response = self.client.get(reverse("feed-detail", args=[self.project.id]))
        detail_url = reverse("feed-post-detail", args=[self.project.id, post.id])
        self.assertContains(response, detail_url)

    def test_posts_sorted_by_collection_then_publication(self) -> None:
        now = timezone.now()
        telegram_post = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=12,
            message="Телеграм-пост с более новой датой публикации",
            posted_at=now,
        )
        web_post = Post.objects.create(
            project=self.project,
            source=self.web_source,
            origin_type=Post.Origin.WEB,
            external_id="web-42",
            message="Веб-пост с более старой датой публикации",
            posted_at=now - timedelta(days=3),
        )
        Post.objects.filter(pk=telegram_post.pk).update(collected_at=now - timedelta(hours=1))

        response = self.client.get(reverse("feed-detail", args=[self.project.id]))

        posts = response.context["posts"]
        self.assertEqual(posts[0].id, web_post.id)
        self.assertEqual(posts[-1].id, telegram_post.id)

    def test_post_list_shows_telegram_media_preview(self) -> None:
        Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=99,
            message="Фото дня",
            posted_at=timezone.now(),
            has_media=True,
            media_path="uploads/media/photo.jpg",
            media_type="photo",
        )
        response = self.client.get(reverse("feed-detail", args=[self.project.id]))
        self.assertContains(response, "post-media-thumb")
        self.assertContains(response, "uploads/media/photo.jpg")

    def test_post_list_shows_web_images_manifest(self) -> None:
        Post.objects.create(
            project=self.project,
            source=self.web_source,
            origin_type=Post.Origin.WEB,
            external_id="web-1",
            message="Веб-пост",
            posted_at=timezone.now(),
            has_media=True,
            images_manifest=["https://example.com/image.jpg"],
        )
        response = self.client.get(reverse("feed-detail", args=[self.project.id]))
        self.assertContains(response, "https://example.com/image.jpg")


class ProjectPostDetailViewTests(TestCase):
    def setUp(self) -> None:
        self.owner = User.objects.create_user("post-owner", password="secret")
        self.other = User.objects.create_user("stranger", password="secret")
        self.client.force_login(self.owner)
        self.project = Project.objects.create(owner=self.owner, name="Детали поста")
        self.source = Source.objects.create(project=self.project, telegram_id=1, title="Tech")
        self.web_source = Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            title="Site",
        )
        self.post = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=42,
            message="Полный текст новости с важной концовкой для проверки.",
            posted_at=timezone.now(),
            has_media=True,
            images_manifest=["https://cdn.example.com/photo.png"],
        )

    def test_owner_can_read_full_post_with_media(self) -> None:
        url = reverse("feed-post-detail", args=[self.project.id, self.post.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Полный текст новости с важной концовкой")
        self.assertContains(response, "cdn.example.com/photo.png")
        self.assertContains(response, "К ленте")

    def test_other_user_cannot_access_foreign_post(self) -> None:
        self.client.force_login(self.other)
        url = reverse("feed-post-detail", args=[self.project.id, self.post.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_web_post_shows_original_link(self) -> None:
        web_post = Post.objects.create(
            project=self.project,
            source=self.web_source,
            origin_type=Post.Origin.WEB,
            external_id="web-1",
            canonical_url="https://example.com/full-story",
            message="Веб-пост с оригинальной ссылкой",
            posted_at=timezone.now(),
        )
        url = reverse("feed-post-detail", args=[self.project.id, web_post.id])
        response = self.client.get(url)
        self.assertContains(response, "Открыть оригинал")
        self.assertContains(response, "https://example.com/full-story")


class NavigationMenuTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("navigator", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Навигация")

    def _active_nav_links(self, html: str) -> list[tuple[str, str]]:
        pattern = re.compile(
            r'<a\s+class="nav-link active"\s+href="([^"]+)"\s*>\s*([^<]+)',
            re.IGNORECASE,
        )
        return [(href, label.strip()) for href, label in pattern.findall(html)]

    def test_projects_nav_active_on_project_list(self) -> None:
        response = self.client.get(reverse("projects:list"))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        projects_href = reverse("projects:list")
        feed_href = reverse("feed")
        html = response.content.decode("utf-8")
        active_links = self._active_nav_links(html)
        self.assertIn((projects_href, "Проекты"), active_links)
        self.assertNotIn((feed_href, "Лента"), active_links)

    def test_feed_nav_active_on_project_feed(self) -> None:
        response = self.client.get(reverse("feed-detail", args=[self.project.id]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        projects_href = reverse("projects:list")
        feed_href = reverse("feed")
        html = response.content.decode("utf-8")
        active_links = self._active_nav_links(html)
        self.assertIn((feed_href, "Лента"), active_links)
        self.assertNotIn((projects_href, "Проекты"), active_links)


class CollectorControlViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("owner", password="secret")
        self.user.telethon_api_id = 111
        self.user.telethon_api_hash = "hash"
        self.user.telethon_session = "session"
        self.user.save(
            update_fields=[
                "telethon_api_id",
                "telethon_api_hash",
                "telethon_session",
            ]
        )
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Collector")
        self.source = Source.objects.create(
            project=self.project,
            type=Source.Type.TELEGRAM,
            telegram_id=777,
            title="Collector Source",
            is_active=True,
        )

    def test_start_collector_enqueues_task(self) -> None:
        response = self.client.post(
            reverse("feed-detail", args=[self.project.id]),
            data={"action": "collector_start"},
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.project.refresh_from_db()
        self.assertTrue(self.project.collector_enabled)
        self.assertTrue(
            WorkerTask.objects.filter(
                queue=WorkerTask.Queue.COLLECTOR,
                payload__project_id=self.project.id,
                status=WorkerTask.Status.QUEUED,
            ).exists()
        )

    def test_stop_collector_disables_and_cancels_tasks(self) -> None:
        self.project.collector_enabled = True
        self.project.save(update_fields=["collector_enabled"])
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR,
            payload={"project_id": self.project.id},
        )
        response = self.client.post(
            reverse("feed-detail", args=[self.project.id]),
            data={"action": "collector_stop"},
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.project.refresh_from_db()
        self.assertFalse(self.project.collector_enabled)
        task.refresh_from_db()
        self.assertEqual(task.status, WorkerTask.Status.CANCELLED)

    def test_start_requires_credentials(self) -> None:
        self.user.telethon_session = ""
        self.user.save(update_fields=["telethon_session"])
        response = self.client.post(
            reverse("feed-detail", args=[self.project.id]),
            data={"action": "collector_start"},
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.project.refresh_from_db()
        self.assertFalse(self.project.collector_enabled)
        self.assertFalse(
            WorkerTask.objects.filter(queue=WorkerTask.Queue.COLLECTOR).exists()
        )
