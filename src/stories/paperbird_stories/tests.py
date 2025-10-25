"""Тесты сервиса сюжетов и рерайта."""

from __future__ import annotations

from datetime import datetime
from http import HTTPStatus
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from projects.models import Post, Project, Source
from stories.paperbird_stories.forms import StoryRewriteForm
from stories.paperbird_stories.models import (
    Publication,
    RewritePreset,
    RewriteTask,
    Story,
)
from stories.paperbird_stories.services import (
    ProviderResponse,
    PublicationFailed,
    PublishResult,
    RewriteFailed,
    StoryCreationError,
    StoryFactory,
    StoryPublisher,
    StoryRewriter,
    build_prompt,
)

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


class PromptBuilderTests(TestCase):
    def test_prompt_contains_documents_and_comment(self) -> None:
        user = User.objects.create_user("u", password="x")
        project = Project.objects.create(owner=user, name="Test")
        source = Source.objects.create(project=project, telegram_id=999)
        post = Post.objects.create(
            project=project,
            source=source,
            telegram_id=1,
            message="Текст поста",
            posted_at=timezone.now(),
        )
        messages = build_prompt(
            posts=[post],
            editor_comment="Сжать до 5 предложений",
            title="Заголовок",
        )
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Документы", messages[1]["content"])
        self.assertIn("Сжать до 5 предложений", messages[1]["content"])
        self.assertIn("Заголовок", messages[1]["content"])


class StoryRewriterTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("author", password="pass")
        self.project = Project.objects.create(owner=self.user, name="Rewrite")
        self.source = Source.objects.create(project=self.project, telegram_id=77)
        self.post = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=10,
            message="Оригинальный текст",
            posted_at=timezone.now(),
        )
        self.story = StoryFactory(project=self.project).create(
            post_ids=[self.post.id],
            title="Исходный заголовок",
            editor_comment="Переделай в деловой стиль",
        )

    def test_successful_rewrite_updates_story(self) -> None:
        class StubProvider:
            def __init__(self) -> None:
                self.calls: list[list[dict[str, str]]] = []

            def run(self, *, messages):
                self.calls.append(list(messages))
                return ProviderResponse(
                    result={
                        "title": "Новый заголовок",
                        "summary": "Краткое описание",
                        "content": "Сгенерированный текст",
                        "hashtags": ["новости"],
                        "sources": ["канал Telegram"],
                    },
                    raw={"mock": True},
                    response_id="resp-1",
                )

        provider = StubProvider()
        rewriter = StoryRewriter(provider=provider)
        task = rewriter.rewrite(self.story)

        self.assertEqual(task.status, RewriteTask.Status.SUCCESS)
        story = Story.objects.get(pk=self.story.pk)
        self.assertEqual(story.status, Story.Status.READY)
        self.assertEqual(story.title, "Новый заголовок")
        self.assertEqual(story.summary, "Краткое описание")
        self.assertEqual(story.body, "Сгенерированный текст")
        self.assertEqual(story.hashtags, ["новости"])
        self.assertEqual(story.sources, ["канал Telegram"])
        self.assertEqual(story.last_rewrite_payload["structured"]["title"], "Новый заголовок")
        self.assertTrue(provider.calls)
        self.assertIsNone(story.last_rewrite_preset)

    def test_rewrite_with_preset_applies_configuration(self) -> None:
        preset = RewritePreset.objects.create(
            project=self.project,
            name="Деловой стиль",
            description="Сосредоточиться на цифрах",
            style="деловой, формальный",
            editor_comment="Сфокусируйся на ключевых метриках",
            output_format={"title": "string"},
        )

        class TrackingProvider:
            def __init__(self) -> None:
                self.calls: list[list[dict[str, str]]] = []

            def run(self, *, messages):
                self.calls.append(list(messages))
                return ProviderResponse(
                    result={
                        "title": "Пресетный заголовок",
                        "summary": "",
                        "content": "Текст",
                        "hashtags": [],
                        "sources": [],
                    },
                    raw={"preset": True},
                    response_id="resp-2",
                )

        provider = TrackingProvider()
        rewriter = StoryRewriter(provider=provider)
        task = rewriter.rewrite(self.story, editor_comment="", preset=preset)

        user_message = provider.calls[0][1]["content"]
        self.assertIn("Настройки пресета", user_message)
        self.assertIn("деловой", user_message)
        self.assertIn("Сфокусируйся на ключевых метриках", user_message)

        story = Story.objects.get(pk=self.story.pk)
        self.assertEqual(story.last_rewrite_preset, preset)
        self.assertEqual(task.preset, preset)
        self.assertEqual(story.editor_comment, "")

    def test_failed_rewrite_restores_draft_status(self) -> None:
        class FailingProvider:
            def run(self, *, messages):
                raise ValueError("API down")

        rewriter = StoryRewriter(provider=FailingProvider(), max_attempts=1)
        with self.assertRaises(RewriteFailed):
            rewriter.rewrite(self.story)

        story = Story.objects.get(pk=self.story.pk)
        task = RewriteTask.objects.filter(story=story).latest("created_at")
        self.assertEqual(story.status, Story.Status.DRAFT)
        self.assertEqual(task.status, RewriteTask.Status.FAILED)
        self.assertIn("API down", task.error_message)


class StoryPublisherTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("pub", password="pass")
        self.project = Project.objects.create(owner=self.user, name="Pub")
        self.source = Source.objects.create(project=self.project, telegram_id=55)
        self.post = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=11,
            message="Контент для публикации",
            posted_at=timezone.now(),
        )
        self.story = StoryFactory(project=self.project).create(
            post_ids=[self.post.id],
            title="Заголовок",
        )
        self.story.apply_rewrite(
            title="Заголовок",
            summary="",
            body="Готовый текст",
            hashtags=["новости"],
            sources=["source"],
            payload={},
        )

    def test_compose_publication_text(self) -> None:
        text = self.story.compose_publication_text()
        self.assertIn("Заголовок", text)
        self.assertIn("Готовый текст", text)
        self.assertIn("#новости", text)
        self.assertIn("Источники", text)

    def test_publish_success_updates_story_and_publication(self) -> None:
        class StubBackend:
            def __init__(self) -> None:
                self.calls: list[tuple[Story, str, str]] = []

            def send(self, *, story, text, target):
                self.calls.append((story, text, target))
                return PublishResult(
                    message_ids=[101],
                    published_at=timezone.now(),
                    raw={"ok": True},
                )

        backend = StubBackend()
        publisher = StoryPublisher(backend=backend)
        publication = publisher.publish(self.story, target="@channel")

        self.assertEqual(publication.status, Publication.Status.PUBLISHED)
        self.assertEqual(publication.message_ids, [101])
        self.assertEqual(publication.target, "@channel")
        self.story.refresh_from_db()
        self.assertEqual(self.story.status, Story.Status.PUBLISHED)

    def test_publish_failure_sets_error(self) -> None:
        class FailingBackend:
            def send(self, *, story, text, target):  # pragma: no cover - behaviour validated
                raise RuntimeError("Telegram unavailable")

        publisher = StoryPublisher(backend=FailingBackend())
        with self.assertRaises(PublicationFailed):
            publisher.publish(self.story, target="@channel")

        publication = Publication.objects.latest("created_at")
        self.assertEqual(publication.status, Publication.Status.FAILED)
        self.assertIn("Telegram unavailable", publication.error_message)
        self.story.refresh_from_db()
        self.assertEqual(self.story.status, Story.Status.READY)

    def test_publish_requires_ready_story(self) -> None:
        self.story.status = Story.Status.DRAFT
        self.story.save(update_fields=["status"])

        class DummyBackend:
            def send(self, *, story, text, target):  # pragma: no cover
                return PublishResult(message_ids=[], published_at=timezone.now())

        publisher = StoryPublisher(backend=DummyBackend())
        with self.assertRaises(PublicationFailed):
            publisher.publish(self.story, target="@channel")

        self.story.refresh_from_db()
        self.assertEqual(self.story.status, Story.Status.DRAFT)


class StoryRewriteFormTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("editor", password="pass")
        self.project = Project.objects.create(owner=self.user, name="Формы")
        self.story = Story.objects.create(project=self.project, title="Тестовая история")
        self.preset_a = RewritePreset.objects.create(
            project=self.project,
            name="Пресет А",
            style="деловой",
            editor_comment="Придерживайся фактов",
        )
        self.preset_b = RewritePreset.objects.create(
            project=self.project,
            name="Пресет Б",
            style="разговорный",
            editor_comment="Используй лёгкий тон",
        )
        self.story.last_rewrite_preset = self.preset_b
        self.story.save(update_fields=["last_rewrite_preset"])

    def test_form_limits_presets_to_story_project(self) -> None:
        form = StoryRewriteForm(story=self.story)
        preset_names = list(form.fields["preset"].queryset.values_list("name", flat=True))
        self.assertCountEqual(preset_names, ["Пресет А", "Пресет Б"])
        self.assertEqual(form.fields["preset"].initial, self.preset_b)


class StoryViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("viewer", password="pass")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Newsroom")
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
        self.assertIn("Сюжеты", response.content.decode("utf-8"))

    def test_create_story_view(self) -> None:
        response = self.client.post(
            reverse("stories:create"),
            data={
                "project": self.project.id,
                "posts": [self.post.id],
                "title": "Новый сюжет",
                "editor_comment": "",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        story = Story.objects.order_by("-created_at").first()
        assert story is not None
        self.assertEqual(story.title, "Новый сюжет")

    @patch("stories.paperbird_stories.views.default_rewriter")
    def test_rewrite_action_shows_prompt(self, mock_rewriter) -> None:
        mock_instance = MagicMock()
        mock_rewriter.return_value = mock_instance
        url = reverse("stories:detail", kwargs={"pk": self.story.pk})
        response = self.client.post(
            url,
            data={"action": "rewrite", "editor_comment": ""},
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        content = response.content.decode("utf-8")
        self.assertIn("Проверьте промпт перед отправкой", content)
        mock_instance.rewrite.assert_not_called()

    @patch("stories.paperbird_stories.views.default_rewriter")
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
        mock_instance.rewrite.assert_called_once()

    @patch("stories.paperbird_stories.views.default_publisher_for_story")
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
        self.assertIn("@channel", content)
