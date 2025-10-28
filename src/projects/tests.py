"""Тесты фильтрации постов и работы с ключевыми словами."""

from __future__ import annotations

import asyncio
import io
from datetime import timedelta
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from core.constants import (
    IMAGE_DEFAULT_MODEL,
    IMAGE_DEFAULT_QUALITY,
    IMAGE_DEFAULT_SIZE,
    IMAGE_MODEL_CHOICES,
    IMAGE_QUALITY_CHOICES,
    IMAGE_SIZE_CHOICES,
    REWRITE_DEFAULT_MODEL,
    REWRITE_MODEL_CHOICES,
)
from core.models import WorkerTask
from projects.models import Post, Project, Source
from projects.services.post_filters import (
    PostFilterOptions,
    apply_post_filters,
    collect_keyword_hits,
    summarize_keyword_hits,
)
from projects.services.collector import PostCollector, _normalize_raw
from projects.services.retention import purge_expired_posts, schedule_retention_cleanup
from projects.workers import (
    collect_project_posts_task,
    refresh_source_metadata_task,
    retention_cleanup_task,
)
from projects.services.telethon_client import (
    TelethonClientFactory,
    TelethonCredentialsMissingError,
)
from stories.paperbird_stories.services import StoryFactory

User = get_user_model()


class PostFilterServiceTests(TestCase):
    """Проверяет расширенные фильтры постов и ключевых слов."""

    def setUp(self) -> None:
        self.user = User.objects.create_user("analyst", password="secret")
        self.project = Project.objects.create(owner=self.user, name="Новости")
        self.source_primary = Source.objects.create(
            project=self.project,
            telegram_id=101,
            title="Технологические новости",
            username="technews",
        )
        self.source_secondary = Source.objects.create(
            project=self.project,
            telegram_id=202,
            title="Политика",
            username="politics",
        )
        now = timezone.now()
        self.post_new = Post.objects.create(
            project=self.project,
            source=self.source_primary,
            telegram_id=1,
            message="Apple представила новую серию устройств на презентации",
            posted_at=now - timedelta(hours=1),
            status=Post.Status.NEW,
            has_media=True,
            raw={"media": []},
        )
        self.post_used = Post.objects.create(
            project=self.project,
            source=self.source_primary,
            telegram_id=2,
            message="Google объявила о запуске сервиса на территории России",
            posted_at=now - timedelta(days=1),
            status=Post.Status.USED,
            has_media=False,
            raw={},
        )
        self.post_other_source = Post.objects.create(
            project=self.project,
            source=self.source_secondary,
            telegram_id=3,
            message="Парламент обсудил новые меры поддержки экономики",
            posted_at=now - timedelta(days=2),
            status=Post.Status.NEW,
            has_media=False,
            raw={},
        )

    def test_filter_by_status_and_media(self) -> None:
        options = PostFilterOptions(
            statuses={Post.Status.NEW},
            has_media=True,
        )
        queryset = apply_post_filters(
            Post.objects.filter(project=self.project).select_related("source"),
            options,
        )
        self.assertEqual(list(queryset), [self.post_new])

    def test_filter_by_search_terms(self) -> None:
        options = PostFilterOptions(search="Apple презентации")
        queryset = apply_post_filters(Post.objects.filter(project=self.project), options)
        self.assertEqual(list(queryset), [self.post_new])

    def test_filter_by_keywords_include_and_exclude(self) -> None:
        options = PostFilterOptions(
            include_keywords={"запуск"},
            exclude_keywords={"России"},
        )
        queryset = apply_post_filters(Post.objects.filter(project=self.project), options)
        self.assertEqual(list(queryset), [])

        options = PostFilterOptions(include_keywords={"Парламент", "запуск"})
        queryset = apply_post_filters(Post.objects.filter(project=self.project), options)
        self.assertCountEqual(list(queryset), [self.post_used, self.post_other_source])

    def test_filter_by_date_interval_and_source(self) -> None:
        options = PostFilterOptions(
            date_from=timezone.now() - timedelta(days=1, hours=12),
            date_to=timezone.now(),
            source_ids={self.source_primary.id},
        )
        queryset = apply_post_filters(Post.objects.filter(project=self.project), options)
        self.assertCountEqual(list(queryset), [self.post_new, self.post_used])

    def test_keyword_hits_summary(self) -> None:
        options = PostFilterOptions(include_keywords={"презентации", "поддержки"})
        queryset = apply_post_filters(
            Post.objects.filter(project=self.project).select_related("source"),
            options,
        )
        posts = list(queryset)
        hits = collect_keyword_hits(posts, options.highlight_keywords)
        summary = summarize_keyword_hits(posts, options.highlight_keywords)

        self.assertIn(self.post_new.id, hits)
        self.assertEqual(hits[self.post_new.id], ["презентации"])
        self.assertEqual(summary, {"презентации": 1, "поддержки": 1})

    def test_invalid_status_raises_error(self) -> None:
        options = PostFilterOptions(statuses={"unknown"})
        with self.assertRaisesMessage(ValueError, "Неизвестные статусы постов"):
            apply_post_filters(Post.objects.filter(project=self.project), options)


class ProjectPostListViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("viewer", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Новости")
        self.other_project = Project.objects.create(owner=self.user, name="Архив")
        self.source = Source.objects.create(project=self.project, telegram_id=1, title="Tech")
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
        response = self.client.get(reverse("projects:post-list", args=[self.project.id]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Лента проекта")
        self.assertContains(response, "Apple представила")
        self.assertContains(response, "Сделать сюжет")

    def test_post_list_filters_by_search(self) -> None:
        response = self.client.get(
            reverse("projects:post-list", args=[self.project.id]),
            data={"search": "Google"},
        )
        self.assertContains(response, "Google updated the service")
        self.assertNotContains(response, "Apple представила")

    def test_posts_sorted_by_newest_first(self) -> None:
        older_time = timezone.now() - timedelta(days=3)
        newest = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=12,
            message="Самый свежий пост",
            posted_at=timezone.now(),
        )
        older = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=13,
            message="Очень старый пост",
            posted_at=older_time,
        )

        response = self.client.get(reverse("projects:post-list", args=[self.project.id]))

        posts = response.context["posts"]
        self.assertGreaterEqual(posts[0].posted_at, posts[1].posted_at)
        self.assertEqual(posts[0].id, newest.id)
        self.assertEqual(posts[-1].id, older.id)


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

    def test_start_collector_enqueues_task(self) -> None:
        response = self.client.post(
            reverse("projects:post-list", args=[self.project.id]),
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
            reverse("projects:post-list", args=[self.project.id]),
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
            reverse("projects:post-list", args=[self.project.id]),
            data={"action": "collector_start"},
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.project.refresh_from_db()
        self.assertFalse(self.project.collector_enabled)
        self.assertFalse(
            WorkerTask.objects.filter(queue=WorkerTask.Queue.COLLECTOR).exists()
        )


class ProjectListViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("manager", password="secret")
        self.client.force_login(self.user)
        self.project_main = Project.objects.create(owner=self.user, name="Основной")
        self.project_extra = Project.objects.create(owner=self.user, name="Резерв")
        source = Source.objects.create(project=self.project_main, telegram_id=10)
        post = Post.objects.create(
            project=self.project_main,
            source=source,
            telegram_id=1,
            message="Новость",
            posted_at=timezone.now(),
        )
        # создаём сюжет, чтобы проверить счётчик
        story = StoryFactory(project=self.project_main).create(post_ids=[post.id])
        story.apply_rewrite(
            title="Заголовок",
            summary="",
            body="Текст",
            hashtags=[],
            sources=[],
            payload={},
        )

    def test_project_list_page(self) -> None:
        response = self.client.get(reverse("projects:list"))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Основной")
        self.assertContains(response, "Лента постов")
        self.assertContains(response, "Источники")
        self.assertContains(response, "Настройки")
        self.assertContains(response, "Создать проект")
        self.assertNotContains(response, "Создать сюжет")


class ProjectCreateViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("owner", password="secret")
        self.client.force_login(self.user)

    def test_get_create_page(self) -> None:
        response = self.client.get(reverse("projects:create"))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Новый проект")
        self.assertContains(response, "Сохранить проект")

    def test_post_creates_project_and_redirects(self) -> None:
        alt_model = IMAGE_MODEL_CHOICES[1][0]
        alt_size = IMAGE_SIZE_CHOICES[1][0]
        alt_quality = IMAGE_QUALITY_CHOICES[1][0]
        rewrite_choice = REWRITE_MODEL_CHOICES[1][0]
        response = self.client.post(
            reverse("projects:create"),
            data={
                "name": "Мониторинг",
                "description": "Telegram-лента",
                "publish_target": "@paperbird",
                "rewrite_model": rewrite_choice,
                "image_model": alt_model,
                "image_size": alt_size,
                "image_quality": alt_quality,
                "retention_days": 45,
            },
            follow=True,
        )
        self.assertContains(response, "Проект «Мониторинг» создан.")
        project = Project.objects.get(owner=self.user, name="Мониторинг")
        self.assertEqual(project.publish_target, "@paperbird")
        self.assertEqual(project.retention_days, 45)
        self.assertEqual(project.rewrite_model, rewrite_choice)
        self.assertEqual(project.image_model, alt_model)
        self.assertEqual(project.image_size, alt_size)
        self.assertEqual(project.image_quality, alt_quality)

    def test_duplicate_name_validation(self) -> None:
        Project.objects.create(owner=self.user, name="Мониторинг")
        response = self.client.post(
            reverse("projects:create"),
            data={
                "name": "Мониторинг",
                "description": "",
                "rewrite_model": REWRITE_DEFAULT_MODEL,
                "image_model": IMAGE_DEFAULT_MODEL,
                "image_size": IMAGE_DEFAULT_SIZE,
                "image_quality": IMAGE_DEFAULT_QUALITY,
                "retention_days": 90,
            },
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        form = response.context["form"]
        self.assertFormError(
            form,
            "name",
            "У вас уже есть проект с таким названием. Выберите другое.",
        )


class ProjectSettingsViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("editor", password="secret")
        self.other = User.objects.create_user("viewer", password="secret")
        self.project = Project.objects.create(
            owner=self.user,
            name="Новости",
            publish_target="@old",
            retention_days=30,
        )

    def test_get_settings_page(self) -> None:
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("projects:settings", args=[self.project.pk])
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Настройки проекта")
        self.assertContains(response, "@old")

    def test_post_updates_settings(self) -> None:
        self.client.force_login(self.user)
        new_model = IMAGE_MODEL_CHOICES[1][0]
        new_size = IMAGE_SIZE_CHOICES[-1][0]
        new_quality = IMAGE_QUALITY_CHOICES[1][0]
        new_rewrite = REWRITE_MODEL_CHOICES[-1][0]
        response = self.client.post(
            reverse("projects:settings", args=[self.project.pk]),
            data={
                "name": "Новости",
                "description": "Обновлённое описание",
                "publish_target": "@fresh",
                "rewrite_model": new_rewrite,
                "image_model": new_model,
                "image_size": new_size,
                "image_quality": new_quality,
                "retention_days": 60,
            },
            follow=True,
        )
        self.assertContains(response, "Настройки проекта «Новости» обновлены.")
        self.project.refresh_from_db()
        self.assertEqual(self.project.publish_target, "@fresh")
        self.assertEqual(self.project.retention_days, 60)
        self.assertEqual(self.project.description, "Обновлённое описание")
        self.assertEqual(self.project.rewrite_model, new_rewrite)
        self.assertEqual(self.project.image_model, new_model)
        self.assertEqual(self.project.image_size, new_size)
        self.assertEqual(self.project.image_quality, new_quality)

    def test_other_user_cannot_access(self) -> None:
        self.client.force_login(self.other)
        response = self.client.get(
            reverse("projects:settings", args=[self.project.pk])
        )
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)


class ProjectSourcesViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("curator", password="secret")
        self.other = User.objects.create_user("reader", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Мониторинг")

    def test_get_sources_page(self) -> None:
        response = self.client.get(reverse("projects:sources", args=[self.project.pk]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Источники проекта")

    @patch("projects.forms.enqueue_source_refresh")
    def test_post_creates_source(self, mock_refresh) -> None:
        response = self.client.post(
            reverse("projects:sources", args=[self.project.pk]),
            data={
                "title": "Tech",
                "telegram_id": "",
                "username": "https://t.me/technews",
                "invite_link": "",
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 15,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        source = Source.objects.get(project=self.project, username="technews")
        self.assertIsNone(source.telegram_id)
        mock_refresh.assert_called_once_with(source)

    @patch("projects.forms.enqueue_source_refresh")
    def test_username_from_s_path_normalized(self, mock_refresh) -> None:
        response = self.client.post(
            reverse("projects:sources", args=[self.project.pk]),
            data={
                "title": "News",
                "telegram_id": "",
                "username": "https://t.me/s/bazabazon",
                "invite_link": "",
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 10,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        source = Source.objects.get(project=self.project)
        self.assertEqual(source.username, "bazabazon")
        mock_refresh.assert_called_once()

    @patch("projects.forms.enqueue_source_refresh")
    def test_invite_link_detection_from_username_field(self, mock_refresh) -> None:
        self.client.post(
            reverse("projects:sources", args=[self.project.pk]),
            data={
                "title": "Private",
                "telegram_id": "",
                "username": "https://t.me/+abcdef",
                "invite_link": "",
                "retention_days": 7,
            },
            follow=True,
        )
        source = Source.objects.get(project=self.project, title="Private")
        self.assertEqual(source.invite_link, "https://t.me/+abcdef")
        mock_refresh.assert_called_once()

    @patch("projects.forms.enqueue_source_refresh")
    def test_update_source(self, mock_refresh) -> None:
        source = Source.objects.create(
            project=self.project,
            title="Old title",
            username="old",
        )
        response = self.client.post(
            reverse("projects:sources", args=[self.project.pk]),
            data={
                "action": "update",
                "source_id": source.pk,
                "title": "New title",
                "username": "@newusername",
                "invite_link": "",
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 5,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        source.refresh_from_db()
        self.assertEqual(source.title, "New title")
        self.assertEqual(source.username, "newusername")
        self.assertEqual(mock_refresh.call_count, 1)

    def test_delete_source(self) -> None:
        source = Source.objects.create(project=self.project, title="Temp", username="temp")
        response = self.client.post(
            reverse("projects:sources", args=[self.project.pk]),
            data={
                "action": "delete",
                "source_id": source.pk,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertFalse(Source.objects.filter(pk=source.pk).exists())

    def test_other_user_cannot_access(self) -> None:
        self.client.force_login(self.other)
        response = self.client.get(reverse("projects:sources", args=[self.project.pk]))
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)


class CollectorSanitizationTests(TestCase):
    def test_normalize_raw_handles_datetime(self) -> None:
        payload = {
            "date": timezone.now(),
            "nested": [timezone.now(), {"another": timezone.now()}],
        }
        normalized = _normalize_raw(payload)
        import json

        json.dumps(normalized)  # should not raise
        self.assertIsInstance(normalized["date"], str)


class CollectorRetentionWindowTests(TransactionTestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("window", password="secret")
        self.project = Project.objects.create(
            owner=self.user,
            name="Окно сбора",
            retention_days=180,
        )
        self.source = Source.objects.create(
            project=self.project,
            username="channel",
        )

    @patch("projects.services.collector.TelethonClientFactory.connect")
    def test_skips_messages_older_than_retention(self, mock_connect) -> None:
        class FakeMessage(SimpleNamespace):
            def to_dict(self):
                return {}

        now = timezone.now()
        historical = FakeMessage(
            id=101,
            message="Очень старый пост",
            date=now - timedelta(days=190),
            media=None,
        )
        recent = FakeMessage(
            id=202,
            message="Свежий пост",
            date=now - timedelta(days=5),
            media=None,
        )
        newer = FakeMessage(
            id=303,
            message="Новое сообщение",
            date=now - timedelta(days=2),
            media=None,
        )

        class FakeClient:
            def __init__(self, produced):
                self._produced = produced

            async def get_entity(self, target):
                return target

            async def iter_messages(self, *args, **kwargs):
                min_id = kwargs.get("min_id") or 0
                for item in self._produced:
                    if item.id <= min_id:
                        continue
                    yield item

        class FakeContext:
            def __init__(self, client):
                self.client = client

            async def __aenter__(self):
                return self.client

            async def __aexit__(self, exc_type, exc, tb):
                return False

        mock_connect.side_effect = [
            FakeContext(FakeClient([recent, historical])),
            FakeContext(FakeClient([newer, recent, historical])),
        ]

        with patch("projects.services.collector.Message", FakeMessage):
            collector = PostCollector(user=self.user)
            asyncio.run(collector.collect_for_project(self.project))
            asyncio.run(collector.collect_for_project(self.project))

        stored_posts = list(
            Post.objects.filter(source=self.source)
            .order_by("telegram_id")
            .values_list("telegram_id", flat=True)
        )
        self.assertEqual(stored_posts, [202, 303])

        self.source.refresh_from_db()
        self.assertEqual(self.source.last_synced_id, 303)

        logs = list(self.source.sync_logs.order_by("-started_at")[:2])
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0].fetched_messages, 1)
        self.assertEqual(logs[0].skipped_messages, 0)


class RetentionServiceTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("cleaner", password="secret")
        self.project = Project.objects.create(
            owner=self.user,
            name="Очистка",
            retention_days=30,
        )
        source = Source.objects.create(project=self.project, telegram_id=100)
        now = timezone.now()
        self.old_post = Post.objects.create(
            project=self.project,
            source=source,
            telegram_id=1,
            message="Старый пост",
            posted_at=now - timedelta(days=40),
        )
        self.referenced_post = Post.objects.create(
            project=self.project,
            source=source,
            telegram_id=2,
            message="Пост в сюжете",
            posted_at=now - timedelta(days=40),
        )
        self.fresh_post = Post.objects.create(
            project=self.project,
            source=source,
            telegram_id=3,
            message="Свежий пост",
            posted_at=now - timedelta(days=5),
        )
        Post.objects.filter(pk__in=[self.old_post.pk, self.referenced_post.pk]).update(
            collected_at=now - timedelta(days=35)
        )
        story = StoryFactory(project=self.project).create(post_ids=[self.referenced_post.pk])
        story.apply_rewrite(
            title="",
            summary="",
            body="",
            hashtags=[],
            sources=[],
            payload={},
        )

    def test_purge_removes_only_orphan_old_posts(self) -> None:
        removed = purge_expired_posts(project=self.project, now=timezone.now())
        self.assertEqual(removed, 1)
        self.assertFalse(Post.objects.filter(pk=self.old_post.pk).exists())
        self.assertTrue(Post.objects.filter(pk=self.referenced_post.pk).exists())
        self.assertTrue(Post.objects.filter(pk=self.fresh_post.pk).exists())

    def test_dry_run_counts_without_deletion(self) -> None:
        removed = purge_expired_posts(
            project=self.project,
            now=timezone.now(),
            dry_run=True,
        )
        self.assertEqual(removed, 1)
        self.assertTrue(Post.objects.filter(pk=self.old_post.pk).exists())

    def test_schedule_retention_cleanup_enqueues_task(self) -> None:
        tasks = schedule_retention_cleanup(project=self.project)
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task.queue, WorkerTask.Queue.MAINTENANCE)
        self.assertEqual(task.payload["project_id"], self.project.pk)

    def test_worker_handler_removes_posts(self) -> None:
        task = schedule_retention_cleanup(project=self.project)[0]
        payload = retention_cleanup_task(task)
        self.assertEqual(payload["removed"], 1)
        self.assertFalse(Post.objects.filter(pk=self.old_post.pk).exists())


class PurgeExpiredPostsCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("operator", password="secret")
        self.project = Project.objects.create(
            owner=self.user,
            name="Очистка",
            retention_days=10,
        )
        source = Source.objects.create(project=self.project, telegram_id=200)
        old_time = timezone.now() - timedelta(days=20)
        post = Post.objects.create(
            project=self.project,
            source=source,
            telegram_id=10,
            message="Для удаления",
            posted_at=old_time,
        )
        Post.objects.filter(pk=post.pk).update(collected_at=old_time)

    def test_command_supports_dry_run_and_cleanup(self) -> None:
        out = io.StringIO()
        call_command("purge_expired_posts", dry_run=True, stdout=out)
        self.assertIn("к удалению 1 постов", out.getvalue())
        self.assertEqual(Post.objects.filter(project=self.project).count(), 1)

        out = io.StringIO()
        call_command("purge_expired_posts", stdout=out)
        self.assertIn("удалено 1 постов", out.getvalue())
        self.assertEqual(Post.objects.filter(project=self.project).count(), 0)

    def test_schedule_command_creates_worker_tasks(self) -> None:
        WorkerTask.objects.all().delete()
        out = io.StringIO()
        call_command("schedule_retention_cleanup", stdout=out)
        self.assertIn("Очистка запланирована", out.getvalue())
        tasks = WorkerTask.objects.filter(queue=WorkerTask.Queue.MAINTENANCE)
        self.assertEqual(tasks.count(), 1)
        self.assertEqual(tasks.first().payload["project_id"], self.project.pk)


class SourceMetadataWorkerTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("owner", password="secret")
        self.user.telethon_api_id = 123
        self.user.telethon_api_hash = "hash"
        self.user.telethon_session = "session"
        self.user.save(update_fields=["telethon_api_id", "telethon_api_hash", "telethon_session"])
        self.project = Project.objects.create(owner=self.user, name="Лента")
        self.source = Source.objects.create(project=self.project, username="technews")

    @patch("projects.workers.TelethonClientFactory")
    def test_refresh_updates_source(self, mock_factory) -> None:
        async def get_entity(target):
            return SimpleNamespace(title="Tech News", username="TechNewsRu", id=999)

        class DummyContext:
            async def __aenter__(self):
                return SimpleNamespace(get_entity=get_entity)

            async def __aexit__(self, exc_type, exc, tb):
                return False

        mock_factory.return_value.connect.return_value = DummyContext()

        task = SimpleNamespace(payload={"source_id": self.source.pk})
        result = refresh_source_metadata_task(task)
        self.assertEqual(result["status"], "ok")
        mock_factory.assert_called_once_with(user=self.user)
        self.source.refresh_from_db()
        self.assertEqual(self.source.title, "Tech News")
        self.assertEqual(self.source.username, "technewsru")
        self.assertEqual(self.source.telegram_id, 999)

    def test_refresh_skips_without_credentials(self) -> None:
        self.user.telethon_api_id = None
        self.user.telethon_api_hash = ""
        self.user.telethon_session = ""
        self.user.save(update_fields=["telethon_api_id", "telethon_api_hash", "telethon_session"])
        task = SimpleNamespace(payload={"source_id": self.source.pk})
        result = refresh_source_metadata_task(task)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_credentials")

class TelethonClientFactoryTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("collector", password="secret")
        self.user.telethon_api_id = 123456
        self.user.telethon_api_hash = "hash123"
        self.user.save(update_fields=["telethon_api_id", "telethon_api_hash"])

    def test_build_requires_credentials(self) -> None:
        factory = TelethonClientFactory(user=self.user)
        with self.assertRaisesMessage(
            TelethonCredentialsMissingError,
            "У пользователя не заполнены ключи Telethon",
        ):
            factory.build()

    def test_build_rejects_invalid_session(self) -> None:
        self.user.telethon_session = "broken"
        self.user.save(update_fields=["telethon_session"])
        factory = TelethonClientFactory(user=self.user)
        with self.assertRaisesMessage(
            TelethonCredentialsMissingError,
            "Строка Telethon-сессии повреждена. Сгенерируйте новую и сохраните её в профиле.",
        ):
            factory.build()

    @patch("projects.services.telethon_client.TelegramClient")
    @patch("projects.services.telethon_client.StringSession")
    def test_build_strips_wrappers(self, mock_string_session, mock_client) -> None:
        mock_string_session.return_value = MagicMock()
        mock_client.return_value = MagicMock()
        self.user.telethon_session = 'StringSession("1Aabc==")'
        self.user.save(update_fields=["telethon_session"])
        factory = TelethonClientFactory(user=self.user)
        factory.build()
        mock_string_session.assert_called_once_with("1Aabc==")


class CollectPostsCommandTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("runner", password="secret")
        self.user.telethon_api_id = 123456
        self.user.telethon_api_hash = "hash123"
        self.user.telethon_session = "stub-session"
        self.user.save(
            update_fields=[
                "telethon_api_id",
                "telethon_api_hash",
                "telethon_session",
            ]
        )

    @patch("projects.management.commands.collect_posts.collect_for_user_sync")
    def test_command_wraps_telethon_errors(self, mock_collect) -> None:
        mock_collect.side_effect = TelethonCredentialsMissingError("Сессия недействительна")
        with self.assertRaisesMessage(CommandError, "Сессия недействительна"):
            call_command("collect_posts", self.user.username)
        mock_collect.assert_called_once()

    @patch("projects.management.commands.collect_posts.collect_for_user_sync")
    def test_command_passes_follow_arguments(self, mock_collect) -> None:
        call_command(
            "collect_posts",
            self.user.username,
            "--project",
            "7",
            "--limit",
            "25",
            "--interval",
            "30",
            "--follow",
        )
        mock_collect.assert_called_once_with(
            self.user,
            project_id=7,
            limit=25,
            continuous=True,
            interval=30,
        )


class CollectProjectPostsTaskTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("runner", password="secret")
        self.user.telethon_api_id = 123
        self.user.telethon_api_hash = "hash"
        self.user.telethon_session = "session"
        self.user.save(
            update_fields=[
                "telethon_api_id",
                "telethon_api_hash",
                "telethon_session",
            ]
        )
        self.project = Project.objects.create(
            owner=self.user,
            name="Live",
            collector_enabled=True,
            collector_interval=60,
        )

    @patch("projects.workers.collect_for_user", new_callable=AsyncMock)
    def test_task_collects_and_requeues(self, mock_collect) -> None:
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR,
            payload={"project_id": self.project.id, "interval": 45},
        )

        result = collect_project_posts_task(task)

        self.assertEqual(result["status"], "ok")
        self.project.refresh_from_db()
        self.assertIsNotNone(self.project.collector_last_run)
        self.assertTrue(
            WorkerTask.objects.filter(
                queue=WorkerTask.Queue.COLLECTOR,
                payload__project_id=self.project.id,
                status=WorkerTask.Status.QUEUED,
            )
            .exclude(pk=task.pk)
            .exists()
        )

    def test_task_skips_when_disabled(self) -> None:
        self.project.collector_enabled = False
        self.project.save(update_fields=["collector_enabled"])
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR,
            payload={"project_id": self.project.id},
        )
        result = collect_project_posts_task(task)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "disabled")
