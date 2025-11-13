"""Тесты фильтрации постов и работы с ключевыми словами."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import tempfile
from datetime import datetime, timedelta, timezone as dt_timezone
from http import HTTPStatus
from unittest import skipUnless
from unittest.mock import ANY, AsyncMock, MagicMock, patch
from types import SimpleNamespace
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, TransactionTestCase, override_settings
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
from projects.forms import ProjectCreateForm, SourceCreateForm
from projects.models import Post, Project, ProjectPromptConfig, Source, WebPreset
from projects.services.post_filters import (
    PostFilterOptions,
    apply_post_filters,
    collect_keyword_hits,
    summarize_keyword_hits,
)
from projects.services.prompt_config import ensure_prompt_config, render_prompt
from projects.services.time_preferences import build_project_datetime_context
from projects.services.collector import PostCollector, _normalize_raw, collect_for_all_users
from projects.services.retention import purge_expired_posts, schedule_retention_cleanup
from projects.workers import (
    collect_project_posts_task,
    collect_project_web_sources_task,
    refresh_source_metadata_task,
    retention_cleanup_task,
)
from projects.services.telethon_client import (
    TelethonClientFactory,
    TelethonCredentialsMissingError,
)
from projects.services.web_collector import WebCollector, parse_datetime
from projects.services.web_preset_registry import PresetValidationError, WebPresetRegistry
from stories.paperbird_stories.services import StoryFactory

User = get_user_model()
try:  # pragma: no cover - optional dependency for tests
    import bs4  # type: ignore

    HAS_BS4 = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_BS4 = False

try:  # pragma: no cover - optional dependency for tests
    import jsonschema  # type: ignore

    HAS_JSONSCHEMA = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_JSONSCHEMA = False


def make_preset_payload(name: str = "web_example") -> dict:
    return {
        "name": name,
        "version": "1.0.0",
        "match": {"domains": ["example.com"]},
        "fetch": {"timeout_sec": 5},
        "list_page": {
            "seeds": ["https://example.com/news"],
            "selectors": {
                "items": "article.item",
                "url": "a@href",
                "title": "a@text",
            },
            "pagination": {"type": "none"},
        },
        "article_page": {
            "selectors": {
                "title": "h1@text",
                "content": "div.body",
                "images": "div.body img@src*",
            },
            "cleanup": {"remove": ["div.ad"], "unwrap": []},
            "normalize": {"html_to_md": True},
        },
    }


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
        # web_post остаётся с collected_at=now

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


class ProjectTimePreferenceFormTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("pref-owner", password="secret")

    def _make_data(self, **overrides):
        base = {
            "name": "Локальный проект",
            "description": "",
            "publish_target": "@channel",
            "locale": "ru_RU",
            "time_zone": "Europe/Moscow",
            "rewrite_model": REWRITE_DEFAULT_MODEL,
            "image_model": IMAGE_DEFAULT_MODEL,
            "image_size": IMAGE_DEFAULT_SIZE,
            "image_quality": IMAGE_DEFAULT_QUALITY,
            "retention_days": 30,
            "collector_telegram_interval": 300,
            "collector_web_interval": 300,
        }
        base.update(overrides)
        return base

    def test_invalid_timezone_rejected(self) -> None:
        form = ProjectCreateForm(data=self._make_data(time_zone="Mars/Phobos"), owner=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn("time_zone", form.errors)

    def test_fixed_offset_timezone_allowed(self) -> None:
        form = ProjectCreateForm(data=self._make_data(time_zone="UTC+05:30"), owner=self.user)
        self.assertTrue(form.is_valid(), form.errors)


class ProjectTimePreferenceUtilsTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("pref-utils", password="secret")

    def test_context_uses_locale_and_timezone(self) -> None:
        project = Project.objects.create(
            owner=self.user,
            name="Часовой пояс",
            locale="ru_RU",
            time_zone="UTC+02:30",
        )
        fixed_now = datetime(2024, 1, 1, 10, 0, tzinfo=dt_timezone.utc)
        with patch("projects.services.time_preferences.timezone.now", return_value=fixed_now):
            context = build_project_datetime_context(project)
        self.assertEqual(context["formatted"], "01.01.2024 12:30")
        self.assertEqual(context["offset"], "UTC+02:30")
        self.assertEqual(context["time_zone"], "UTC+02:30")
        self.assertTrue(context["iso"].startswith("2024-01-01T12:30:00"))


class PromptCurrentDatetimeInjectionTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("prompt-owner", password="secret")
        self.project = Project.objects.create(
            owner=self.user,
            name="Новости",
            locale="en_US",
            time_zone="Europe/Berlin",
        )

    def test_render_prompt_appends_datetime_section(self) -> None:
        context = {
            "formatted": "2024-05-10 12:00",
            "offset": "UTC+02:00",
            "time_zone": "Europe/Berlin",
            "iso": "2024-05-10T12:00:00+02:00",
        }
        with patch(
            "projects.services.prompt_config.build_project_datetime_context",
            return_value=context,
        ):
            rendered = render_prompt(project=self.project, posts=[], title="Новости дня")
        self.assertIn("UTC+02:00", rendered.full_text)
        self.assertIn("Europe/Berlin", rendered.full_text)
        self.assertEqual(rendered.sections[-1][0], "current_datetime")
        self.assertIn("2024-05-10 12:00", rendered.full_text)
        self.assertIn("ISO: 2024-05-10T12:00:00+02:00", rendered.sections[-1][1])


class PostDisplayMessageTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("reader", password="secret")
        self.project = Project.objects.create(owner=self.user, name="Лента")
        self.web_source = Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            title="Website",
        )
        self.telegram_source = Source.objects.create(
            project=self.project,
            type=Source.Type.TELEGRAM,
            telegram_id=555,
            title="Канал",
        )

    def test_web_post_combines_title_and_body(self) -> None:
        post = Post.objects.create(
            project=self.project,
            source=self.web_source,
            origin_type=Post.Origin.WEB,
            message="Первый абзац статьи",
            external_metadata={"title": "Заголовок материала"},
            posted_at=timezone.now(),
        )
        self.assertEqual(post.display_message, "Заголовок материала\n\nПервый абзац статьи")

    def test_web_post_does_not_duplicate_existing_title(self) -> None:
        original_text = "Заголовок материала\n\nОсновной текст"
        post = Post.objects.create(
            project=self.project,
            source=self.web_source,
            origin_type=Post.Origin.WEB,
            message=original_text,
            external_metadata={"title": "Заголовок материала"},
            posted_at=timezone.now(),
        )
        self.assertEqual(post.display_message, original_text)

    def test_telegram_post_returns_original_text(self) -> None:
        post = Post.objects.create(
            project=self.project,
            source=self.telegram_source,
            origin_type=Post.Origin.TELEGRAM,
            message="Новость из канала",
            posted_at=timezone.now(),
        )
        self.assertEqual(post.display_message, "Новость из канала")


class CollectForAllUsersTests(TransactionTestCase):
    def setUp(self) -> None:
        self.user_with_creds = User.objects.create_user("collector1", password="secret")
        self.user_with_creds.telethon_api_id = 111
        self.user_with_creds.telethon_api_hash = "hash"
        self.user_with_creds.telethon_session = "session"
        self.user_with_creds.save(
            update_fields=[
                "telethon_api_id",
                "telethon_api_hash",
                "telethon_session",
            ]
        )
        self.user_without_creds = User.objects.create_user("collector2", password="secret")

    @patch("projects.services.collector.collect_for_user", new_callable=AsyncMock)
    def test_collects_only_users_with_credentials(self, mock_collect) -> None:
        asyncio.run(collect_for_all_users(limit=77))
        mock_collect.assert_awaited_once()
        mock_collect.assert_awaited_with(
            self.user_with_creds,
            project_id=None,
            limit=77,
        )

    @patch("projects.services.collector.collect_for_user", new_callable=AsyncMock)
    def test_handles_collect_errors_per_user(self, mock_collect) -> None:
        mock_collect.side_effect = [RuntimeError("boom"), None]
        other = User.objects.create_user("collector3", password="secret")
        other.telethon_api_id = 222
        other.telethon_api_hash = "hash2"
        other.telethon_session = "session2"
        other.save(
            update_fields=[
                "telethon_api_id",
                "telethon_api_hash",
                "telethon_session",
            ]
        )
        # Should not raise even if one user fails
        asyncio.run(collect_for_all_users(limit=10))
        self.assertEqual(mock_collect.await_count, 2)


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
        alt_quality = IMAGE_QUALITY_CHOICES[2][0]
        rewrite_choice = REWRITE_MODEL_CHOICES[1][0]
        response = self.client.post(
            reverse("projects:create"),
            data={
                "name": "Мониторинг",
                "description": "Telegram-лента",
                "publish_target": "@paperbird",
                "locale": "ru_RU",
                "time_zone": "Europe/Moscow",
                "rewrite_model": rewrite_choice,
                "image_model": alt_model,
                "image_size": alt_size,
                "image_quality": alt_quality,
                "retention_days": 45,
                "collector_telegram_interval": 60,
                "collector_web_interval": 300,
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
                "publish_target": "",
                "locale": "ru_RU",
                "time_zone": "UTC",
                "rewrite_model": REWRITE_DEFAULT_MODEL,
                "image_model": IMAGE_DEFAULT_MODEL,
                "image_size": IMAGE_DEFAULT_SIZE,
                "image_quality": IMAGE_DEFAULT_QUALITY,
                "retention_days": 90,
                "collector_telegram_interval": 60,
                "collector_web_interval": 300,
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
        self.assertContains(response, "Перейти к промтам")

    def test_post_updates_settings(self) -> None:
        self.client.force_login(self.user)
        new_model = IMAGE_MODEL_CHOICES[1][0]
        new_size = IMAGE_SIZE_CHOICES[-1][0]
        new_quality = IMAGE_QUALITY_CHOICES[2][0]
        new_rewrite = REWRITE_MODEL_CHOICES[-1][0]
        response = self.client.post(
            reverse("projects:settings", args=[self.project.pk]),
            data={
                "name": "Новости",
                "description": "Обновлённое описание",
                "publish_target": "@fresh",
                "locale": "ru_RU",
                "time_zone": "Europe/Moscow",
                "rewrite_model": new_rewrite,
                "image_model": new_model,
                "image_size": new_size,
                "image_quality": new_quality,
                "retention_days": 60,
                "collector_telegram_interval": 90,
                "collector_web_interval": 240,
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


class ProjectPromptsViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("prompts", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(
            owner=self.user,
            name="Редакция",
            description="Новости технологий",
        )
        ensure_prompt_config(self.project)

    def _form_payload(self, overrides: dict[str, str] | None = None) -> dict[str, str]:
        config = self.project.prompt_config
        data = {
            "system_role": config.system_role,
            "task_instruction": config.task_instruction,
            "documents_intro": config.documents_intro,
            "style_requirements": config.style_requirements,
            "output_format": config.output_format,
            "output_example": config.output_example,
            "editor_comment_note": config.editor_comment_note,
        }
        if overrides:
            data.update(overrides)
        return data

    def test_prompts_page_lists_sections(self) -> None:
        response = self.client.get(reverse("projects:prompts", args=[self.project.id]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "1. [СИСТЕМНАЯ РОЛЬ]")
        self.assertContains(response, "{{PROJECT_NAME}}")
        self.assertContains(response, "Доступные плейсхолдеры")

    def test_prompt_update_persists(self) -> None:
        url = reverse("projects:prompts", args=[self.project.id])
        response = self.client.post(
            url,
            data=self._form_payload({"system_role": "Ты — редактор {{PROJECT_NAME}} и ведёшь канал."}),
            follow=True,
        )
        self.assertContains(response, "Промт проекта «Редакция» сохранён.")
        self.project.refresh_from_db()
        self.assertEqual(
            self.project.prompt_config.system_role,
            "Ты — редактор {{PROJECT_NAME}} и ведёшь канал.",
        )

    def test_default_config_created_when_missing(self) -> None:
        ProjectPromptConfig.objects.filter(project=self.project).delete()
        self.project = Project.objects.get(pk=self.project.pk)
        response = self.client.get(reverse("projects:prompts", args=[self.project.id]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.project.refresh_from_db()
        self.assertTrue(hasattr(self.project, "prompt_config"))
        self.assertIn(
            "{{PROJECT_NAME}}",
            self.project.prompt_config.system_role,
        )

    def test_export_contains_sections_in_order(self) -> None:
        url = reverse("projects:prompts-export", args=[self.project.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        body = response.content.decode("utf-8")
        self.assertTrue(body.startswith("1. [СИСТЕМНАЯ РОЛЬ]"))
        self.assertIn("5. [ФОРМАТ ОТВЕТА — JSON]", body)


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
        self.assertContains(response, "Добавить источник")

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


class ProjectSourceCreateViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("curator", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Мониторинг")

    def test_get_create_page(self) -> None:
        response = self.client.get(
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk})
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Добавить источник")

    @patch("projects.forms.enqueue_source_refresh")
    def test_post_creates_source(self, mock_refresh) -> None:
        response = self.client.post(
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk}),
            data={
                "type": Source.Type.TELEGRAM,
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
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk}),
            data={
                "type": Source.Type.TELEGRAM,
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
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk}),
            data={
                "type": Source.Type.TELEGRAM,
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
    def test_create_source_autofills_title(self, mock_refresh) -> None:
        response = self.client.post(
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk}),
            data={
                "type": Source.Type.TELEGRAM,
                "title": "",
                "telegram_id": "",
                "username": "https://t.me/techsource",
                "invite_link": "",
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 12,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        created = Source.objects.get(project=self.project, username="techsource")
        self.assertEqual(created.title, "@techsource")
        mock_refresh.assert_called_once_with(created)

    @patch("projects.views.enqueue_task")
    def test_web_source_schedules_collection(self, mock_enqueue) -> None:
        payload = json.dumps(make_preset_payload("site_feed"))
        response = self.client.post(
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk}),
            data={
                "type": Source.Type.WEB,
                "title": "Сайт",
                "preset_payload": payload,
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 30,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        created = Source.objects.get(project=self.project)
        self.assertEqual(created.type, Source.Type.WEB)
        mock_enqueue.assert_called_once()
        args, kwargs = mock_enqueue.call_args
        self.assertEqual(args[0], WorkerTask.Queue.COLLECTOR_WEB)
        payload_sent = kwargs["payload"]
        self.assertEqual(payload_sent["project_id"], self.project.pk)
        self.assertEqual(payload_sent["source_id"], created.pk)

    @patch("projects.views.enqueue_task", side_effect=RuntimeError("boom"))
    def test_web_source_enqueue_failure_shows_message(self, mock_enqueue) -> None:
        payload = json.dumps(make_preset_payload("site_feed"))
        response = self.client.post(
            reverse("projects:source-create", kwargs={"project_pk": self.project.pk}),
            data={
                "type": Source.Type.WEB,
                "title": "Сайт",
                "preset_payload": payload,
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 30,
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "не удалось запустить парсер")
        mock_enqueue.assert_called_once()


class ProjectSourceUpdateViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("editor", password="secret")
        self.other = User.objects.create_user("outsider", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Апдейты")
        self.source = Source.objects.create(
            project=self.project,
            title="Новости",
            username="news",
            retention_days=5,
        )

    def test_get_edit_page(self) -> None:
        url = reverse("projects:source-edit", args=[self.project.pk, self.source.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Редактирование источника")
        self.assertContains(response, "Новости")

    @patch("projects.forms.enqueue_source_refresh")
    def test_post_updates_source(self, mock_refresh) -> None:
        url = reverse("projects:source-edit", args=[self.project.pk, self.source.pk])
        response = self.client.post(
            url,
            data={
                "type": Source.Type.TELEGRAM,
                "title": "",
                "username": "@updated",
                "invite_link": "",
                "telegram_id": "",
                "deduplicate_text": "on",
                "deduplicate_media": "",
                "retention_days": 12,
            },
        )
        self.assertRedirects(response, reverse("projects:sources", args=[self.project.pk]))
        self.source.refresh_from_db()
        self.assertEqual(self.source.title, "@updated")
        self.assertEqual(self.source.username, "updated")
        self.assertEqual(self.source.retention_days, 12)
        mock_refresh.assert_called_once_with(self.source)

    def test_other_user_cannot_edit(self) -> None:
        self.client.force_login(self.other)
        url = reverse("projects:source-edit", args=[self.project.pk, self.source.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)


class ProjectCollectorQueueViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("ops", password="secret")
        self.other = User.objects.create_user("guest", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Мониторинг")
        self.payload = {"project_id": self.project.pk}

    def _make_task(self, **overrides):
        defaults = {
            "queue": WorkerTask.Queue.COLLECTOR,
            "payload": self.payload,
            "status": WorkerTask.Status.QUEUED,
        }
        defaults.update(overrides)
        return WorkerTask.objects.create(**defaults)

    def test_queue_view_lists_tasks(self) -> None:
        self._make_task()
        self._make_task(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            status=WorkerTask.Status.RUNNING,
        )
        response = self.client.get(reverse("projects:queue", args=[self.project.pk]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Очередь коллектора проекта")
        self.assertContains(response, "Telegram")
        self.assertContains(response, "Web")

    def test_other_user_cannot_view_queue(self) -> None:
        self.client.force_login(self.other)
        response = self.client.get(reverse("projects:queue", args=[self.project.pk]))
        self.assertEqual(response.status_code, HTTPStatus.NOT_FOUND)

    def test_cancel_task_via_ui(self) -> None:
        task = self._make_task()
        response = self.client.post(
            reverse("projects:queue", args=[self.project.pk]),
            data={"action": "cancel_task", "task_id": str(task.pk)},
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        task.refresh_from_db()
        self.assertEqual(task.status, WorkerTask.Status.CANCELLED)

    @patch("projects.views.enqueue_task")
    def test_retry_task_enqueues_new(self, mock_enqueue) -> None:
        task = self._make_task(status=WorkerTask.Status.SUCCEEDED)
        response = self.client.post(
            reverse("projects:queue", args=[self.project.pk]),
            data={"action": "retry_task", "task_id": str(task.pk)},
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        mock_enqueue.assert_called_once_with(
            task.queue,
            payload=task.payload,
            scheduled_for=ANY,
        )


@skipUnless(HAS_JSONSCHEMA, "jsonschema не установлена")
class WebPresetRegistryTests(TestCase):
    def test_import_and_reuse_preset(self) -> None:
        registry = WebPresetRegistry()
        payload = make_preset_payload()
        preset = registry.import_payload(json.dumps(payload))
        self.assertEqual(preset.name, "web_example")
        self.assertEqual(preset.status, WebPreset.Status.ACTIVE)
        again = registry.import_payload(json.dumps(payload))
        self.assertEqual(WebPreset.objects.count(), 1)
        self.assertEqual(preset.pk, again.pk)

    def test_invalid_payload_raises(self) -> None:
        registry = WebPresetRegistry()
        with self.assertRaises(PresetValidationError):
            registry.import_payload("{}")

    def test_sources_receive_snapshot_refresh(self) -> None:
        registry = WebPresetRegistry()
        payload = make_preset_payload("site_feed")
        preset = registry.import_payload(json.dumps(payload))
        project = Project.objects.create(owner=User.objects.create_user("snap", password="secret"), name="Snapshot")
        source = Source.objects.create(
            project=project,
            type=Source.Type.WEB,
            title="Feed",
            web_preset=preset,
            web_preset_snapshot=payload,
        )
        updated_payload = payload | {"fetch": {**payload["fetch"], "timeout_sec": 25}}
        registry.import_payload(json.dumps(updated_payload))
        source.refresh_from_db()
        self.assertEqual(source.web_preset_snapshot["fetch"]["timeout_sec"], 25)


@skipUnless(HAS_JSONSCHEMA, "jsonschema не установлена")
class WebSourceFormTests(TestCase):
    @patch("projects.forms.enqueue_source_refresh")
    def test_web_source_created_from_json_payload(self, mock_refresh) -> None:
        user = User.objects.create_user("web", password="secret")
        project = Project.objects.create(owner=user, name="Web feed")
        payload = make_preset_payload("site_feed")
        form = SourceCreateForm(
            data={
                "type": Source.Type.WEB,
                "title": "",
                "telegram_id": "",
                "username": "",
                "invite_link": "",
                "web_preset": "",
                "preset_payload": json.dumps(payload),
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 5,
            },
            project=project,
        )
        self.assertTrue(form.is_valid(), form.errors)
        source = form.save()
        self.assertEqual(source.type, Source.Type.WEB)
        self.assertIsNotNone(source.web_preset)
        self.assertTrue(source.web_preset_snapshot)
        self.assertEqual(source.web_preset.name, "site_feed")
        mock_refresh.assert_not_called()


class WebCollectorUtilsTests(TestCase):
    def test_parse_datetime_strips_location_suffix(self) -> None:
        parsed = parse_datetime("11.11.2025 09:51|Псков")
        self.assertIsNotNone(parsed)
        tz = timezone.get_current_timezone()
        localized = parsed.astimezone(tz)
        self.assertEqual(localized.year, 2025)
        self.assertEqual(localized.month, 11)
        self.assertEqual(localized.day, 11)
        self.assertEqual(localized.hour, 9)
        self.assertEqual(localized.minute, 51)


@skipUnless(HAS_BS4, "beautifulsoup4 не установлена")
class WebCollectorTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("crawler", password="secret")
        self.project = Project.objects.create(owner=self.user, name="Web Crawl")
        self.preset_data = make_preset_payload("crawler")
        checksum = hashlib.sha256(
            json.dumps(self.preset_data, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self.preset = WebPreset.objects.create(
            name=self.preset_data["name"],
            version=self.preset_data["version"],
            schema_version=1,
            status=WebPreset.Status.ACTIVE,
            checksum=checksum,
            config=self.preset_data,
        )
        self.source = Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            title="Crawler",
            web_preset=self.preset,
            web_preset_snapshot=self.preset_data,
            is_active=True,
        )
        self.fetcher = self._make_fetcher()

    def _make_fetcher(self):
        listing = """
        <html><body>
          <article class="item"><a href="https://example.com/article-1">Новость дня</a></article>
        </body></html>
        """
        article = """
        <html><body>
          <h1>Новость дня</h1>
          <div class="body">
            <p>Первый абзац текста</p>
            <img src="/images/photo.jpg" />
            <div class="ad">Реклама</div>
          </div>
        </body></html>
        """
        mapping = {
            "https://example.com/news": listing,
            "https://example.com/article-1": article,
        }

        class FakeFetcher:
            def __init__(self, responses):
                self.responses = responses

            def fetch(self, url, _config):
                return SimpleNamespace(
                    url=url,
                    final_url=url,
                    status_code=200,
                    content=self.responses[url],
                )

        return FakeFetcher(mapping)

    def test_collect_creates_and_skips_duplicates(self) -> None:
        collector = WebCollector(fetcher=self.fetcher)
        stats = collector.collect(self.source)
        self.assertEqual(stats["created"], 1)
        post = Post.objects.get(source=self.source)
        self.assertEqual(post.origin_type, Post.Origin.WEB)
        self.assertEqual(post.source, self.source)
        self.assertTrue(post.content_md)
        self.assertTrue(post.external_link)
        stats_repeat = collector.collect(self.source)
        self.assertGreaterEqual(stats_repeat["skipped"], 1)

    def test_collect_combines_multiple_content_nodes(self) -> None:
        multi_preset = make_preset_payload("multi_content")
        multi_preset["article_page"]["selectors"]["content"] = "div.article__text*"
        checksum = hashlib.sha256(
            json.dumps(multi_preset, sort_keys=True).encode("utf-8")
        ).hexdigest()
        preset = WebPreset.objects.create(
            name=multi_preset["name"],
            version=multi_preset["version"],
            schema_version=1,
            status=WebPreset.Status.ACTIVE,
            checksum=checksum,
            config=multi_preset,
        )
        source = Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            title="Multi source",
            web_preset=preset,
            web_preset_snapshot=multi_preset,
            is_active=True,
        )
        self.fetcher.responses["https://example.com/article-1"] = """
        <html><body>
          <div class="article__text">Первый абзац текста</div>
          <div class="article__text"><strong>Второй абзац</strong> продолжает историю.</div>
        </body></html>
        """
        collector = WebCollector(fetcher=self.fetcher)
        stats = collector.collect(source)
        self.assertEqual(stats["created"], 1)
        post = Post.objects.get(source=source)
        self.assertIn("Первый абзац текста", post.message)
        self.assertIn("Второй абзац", post.message)


class CollectProjectWebSourcesTaskTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("webber", password="secret")
        self.project = Project.objects.create(
            owner=self.user,
            name="Web project",
            collector_enabled=True,
            collector_telegram_interval=90,
            collector_web_interval=120,
        )

    def _add_web_source(self) -> Source:
        preset_data = make_preset_payload("worker_site")
        checksum = hashlib.sha256(
            json.dumps(preset_data, sort_keys=True).encode("utf-8")
        ).hexdigest()
        preset = WebPreset.objects.create(
            name=preset_data["name"],
            version=preset_data["version"],
            schema_version=1,
            status=WebPreset.Status.ACTIVE,
            checksum=checksum,
            config=preset_data,
        )
        return Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            title="Worker source",
            web_preset=preset,
            web_preset_snapshot=preset_data,
            is_active=True,
        )

    def test_task_skips_without_sources(self) -> None:
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            payload={"project_id": self.project.id},
        )
        result = collect_project_web_sources_task(task)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_sources")

    @patch("projects.workers.enqueue_task")
    def test_task_enqueues_sources_and_requeues(self, mock_enqueue) -> None:
        source = self._add_web_source()
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            payload={"project_id": self.project.id, "interval": 60},
        )
        result = collect_project_web_sources_task(task)
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["sources"], 1)
        self.assertTrue(result["rescheduled"])
        self.assertGreaterEqual(mock_enqueue.call_count, 2)
        source_call = mock_enqueue.call_args_list[0]
        self.assertEqual(
            source_call.kwargs["payload"],
            {"project_id": self.project.id, "source_id": source.id, "interval": 60},
        )
        scheduler_call = mock_enqueue.call_args_list[-1]
        self.assertEqual(
            scheduler_call.kwargs["payload"],
            {"project_id": self.project.id, "interval": 60},
        )

    @patch("projects.workers.WebCollector.collect")
    def test_task_handles_specific_source_without_reschedule(self, mock_collect) -> None:
        source = self._add_web_source()
        mock_collect.return_value = {"created": 1, "updated": 0, "skipped": 0}
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            payload={"project_id": self.project.id, "interval": 60, "source_id": source.id},
        )
        result = collect_project_web_sources_task(task)
        self.assertEqual(result["created"], 1)
        self.assertFalse(
            WorkerTask.objects.filter(
                queue=WorkerTask.Queue.COLLECTOR_WEB,
                payload__project_id=self.project.id,
                status=WorkerTask.Status.QUEUED,
            )
            .exclude(pk=task.pk)
            .exists()
        )

    @patch("projects.workers.enqueue_task")
    def test_source_retry_overrides_applied(self, mock_enqueue) -> None:
        source = self._add_web_source()
        Source.objects.filter(pk=source.pk).update(
            web_retry_max_attempts=7, web_retry_base_delay=45, web_retry_max_delay=300
        )
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            payload={"project_id": self.project.id, "interval": 120},
        )
        collect_project_web_sources_task(task)
        source_call = mock_enqueue.call_args_list[0]
        self.assertEqual(source_call.kwargs["max_attempts"], 7)
        self.assertEqual(source_call.kwargs["base_retry_delay"], 45)
        self.assertEqual(source_call.kwargs["max_retry_delay"], 300)


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


class CollectorMediaDownloadTests(TransactionTestCase):
    def setUp(self) -> None:
        self.media_root = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_root.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media_root.name)
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.user = User.objects.create_user("media-owner", password="secret")
        self.project = Project.objects.create(owner=self.user, name="Медиа")
        self.source = Source.objects.create(project=self.project, username="mediasource")

    def _process(self, message):
        collector = PostCollector(user=self.user)
        return asyncio.run(collector._process_message(message=message, source=self.source))

    def test_process_message_saves_media_file(self) -> None:
        class FakePhoto:
            pass

        class FakeMessage:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                self.download_request = None

            async def download_media(self, file=None):
                self.download_request = file
                return b"binary-image"

            def to_dict(self):
                return {"id": self.id}

        fake_message = FakeMessage(
            id=777,
            message="",
            date=timezone.now(),
            media=FakePhoto(),
            file=SimpleNamespace(ext=".png", mime_type="image/png", name="photo.png"),
        )

        with patch("projects.services.collector.MessageMediaPhoto", FakePhoto):
            processed = self._process(fake_message)

        self.assertTrue(processed)
        self.assertIs(fake_message.download_request, bytes)

        post = Post.objects.get(source=self.source, telegram_id=fake_message.id)
        self.assertTrue(post.has_media)
        self.assertTrue(post.media_path)

        stored_file = Path(self.media_root.name) / Path(post.media_path)
        self.assertTrue(stored_file.exists())
        self.assertEqual(stored_file.read_bytes(), b"binary-image")


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

    @patch("projects.management.commands.collect_posts.collect_for_all_users_sync")
    def test_all_users_flag_runs_collector(self, mock_all_users) -> None:
        call_command(
            "collect_posts",
            "--all-users",
            "--limit",
            "10",
            "--interval",
            "15",
            "--follow",
        )
        mock_all_users.assert_called_once_with(
            project_id=None,
            limit=10,
            continuous=True,
            interval=15,
        )

    def test_username_required_without_flag(self) -> None:
        with self.assertRaisesMessage(CommandError, "Укажите username или используйте флаг --all-users."):
            call_command("collect_posts")

    def test_all_users_conflicts_with_username(self) -> None:
        with self.assertRaisesMessage(CommandError, "Нельзя указывать username вместе с флагом --all-users."):
            call_command("collect_posts", self.user.username, "--all-users")

    def test_all_users_conflicts_with_project(self) -> None:
        with self.assertRaisesMessage(CommandError, "Флаг --project несовместим с режимом --all-users."):
            call_command("collect_posts", "--all-users", "--project", "1")


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
            collector_telegram_interval=60,
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
