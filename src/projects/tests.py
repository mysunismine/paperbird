"""Тесты фильтрации постов и работы с ключевыми словами."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from projects.models import Post, Project, Source
from projects.services.post_filters import (
    PostFilterOptions,
    apply_post_filters,
    collect_keyword_hits,
    summarize_keyword_hits,
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
        source = Source.objects.create(project=self.project, telegram_id=1, title="Tech")
        Source.objects.create(project=self.other_project, telegram_id=2, title="Other")
        now = timezone.now()
        Post.objects.create(
            project=self.project,
            source=source,
            telegram_id=10,
            message="Apple представила новый продукт",
            posted_at=now,
            status=Post.Status.NEW,
        )
        Post.objects.create(
            project=self.project,
            source=source,
            telegram_id=11,
            message="Google обновила сервис",
            posted_at=now - timedelta(days=1),
            status=Post.Status.USED,
        )

    def test_post_list_page_renders(self) -> None:
        response = self.client.get(reverse("projects:post-list", args=[self.project.id]))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "Лента постов")
        self.assertContains(response, "Apple представила")

    def test_post_list_filters_by_search(self) -> None:
        response = self.client.get(
            reverse("projects:post-list", args=[self.project.id]),
            data={"search": "Google"},
        )
        self.assertContains(response, "Google обновила сервис")
        self.assertNotContains(response, "Apple представила")


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
        response = self.client.post(
            reverse("projects:create"),
            data={"name": "Мониторинг", "description": "Telegram-лента"},
            follow=True,
        )
        self.assertContains(response, "Проект «Мониторинг» создан.")
        self.assertTrue(
            Project.objects.filter(owner=self.user, name="Мониторинг").exists()
        )

    def test_duplicate_name_validation(self) -> None:
        Project.objects.create(owner=self.user, name="Мониторинг")
        response = self.client.post(
            reverse("projects:create"),
            data={"name": "Мониторинг", "description": ""},
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        form = response.context["form"]
        self.assertFormError(
            form,
            "name",
            "У вас уже есть проект с таким названием. Выберите другое.",
        )


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
