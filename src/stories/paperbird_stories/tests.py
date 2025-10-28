"""Тесты сервиса сюжетов и рерайта."""

from __future__ import annotations

import base64
import io
import json
import os
import shutil
import tempfile
from datetime import datetime, timedelta
from http import HTTPStatus
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from projects.models import Post, Project, Source
from core.models import WorkerTask
from core.services.worker import TaskExecutionError
from stories.paperbird_stories.forms import (
    StoryImageAttachForm,
    StoryImageGenerateForm,
    StoryPublishForm,
    StoryRewriteForm,
)
from stories.paperbird_stories.models import (
    Publication,
    RewritePreset,
    RewriteTask,
    Story,
)
from stories.paperbird_stories.services import (
    GeneratedImage,
    ProviderResponse,
    PublicationFailed,
    PublishResult,
    RewriteFailed,
    StoryCreationError,
    StoryFactory,
    StoryPublisher,
    StoryRewriter,
    build_prompt,
    OpenAIImageProvider,
)
from stories.paperbird_stories.workers import publish_story_task

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
                        "content": {
                            "paragraphs": [
                                {"text": "Раз абзац"},
                                {"text": "Два абзац"},
                            ]
                        },
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
        self.assertEqual(story.summary, "")
        self.assertEqual(story.body, "Раз абзац\n\nДва абзац")
        self.assertEqual(story.hashtags, [])
        self.assertEqual(story.sources, [])
        self.assertEqual(story.last_rewrite_payload["structured"]["title"], "Новый заголовок")
        self.assertEqual(story.last_rewrite_payload["structured"]["text"], "Раз абзац\n\nДва абзац")
        self.assertIn("provider_result", story.last_rewrite_payload)
        self.assertEqual(
            story.last_rewrite_payload["provider_result"]["hashtags"],
            ["новости"],
        )
        self.assertTrue(provider.calls)
        self.assertIsNone(story.last_rewrite_preset)

    def test_rewrite_parses_text_array_payload(self) -> None:
        class TextArrayProvider:
            def run(self, *, messages):
                return ProviderResponse(
                    result={
                        "title": "Заголовок массива",
                        "text": [
                            {"text": "Первый абзац"},
                            {"text": "Второй абзац"},
                        ],
                    },
                    raw={"mock": True},
                )

        rewriter = StoryRewriter(provider=TextArrayProvider())
        rewriter.rewrite(self.story)

        story = Story.objects.get(pk=self.story.pk)
        self.assertEqual(story.body, "Первый абзац\n\nВторой абзац")
        self.assertEqual(story.summary, "")
        self.assertEqual(story.hashtags, [])
        self.assertEqual(story.sources, [])
        self.assertEqual(story.last_rewrite_payload["structured"]["text"], "Первый абзац\n\nВторой абзац")

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


class StoryPromptPreviewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("viewer", password="pass")
        self.project = Project.objects.create(owner=self.user, name="Preview")
        self.source = Source.objects.create(project=self.project, telegram_id=500)
        self.post = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=42,
            message="Контент поста",
            posted_at=timezone.now(),
        )
        self.story = StoryFactory(project=self.project).create(post_ids=[self.post.id], title="Предпросмотр")
        self.client.login(username="viewer", password="pass")

    def test_preview_displays_prompt_form(self) -> None:
        url = reverse("stories:detail", args=[self.story.pk])
        response = self.client.post(url, {"action": "rewrite", "preview": "1"})

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertTemplateUsed(response, "stories/story_prompt_preview.html")
        prompt_form = response.context["prompt_form"]
        self.assertIn("System prompt", prompt_form["prompt_system"].label)
        self.assertIn("Собери", prompt_form["prompt_user"].value())

    @patch("stories.paperbird_stories.views.default_rewriter")
    def test_confirm_rewrite_uses_custom_prompts(self, mocked_default_rewriter: MagicMock) -> None:
        rewriter = MagicMock()
        mocked_default_rewriter.return_value = rewriter

        url = reverse("stories:detail", args=[self.story.pk])
        response = self.client.post(
            url,
            {
                "action": "rewrite",
                "prompt_confirm": "1",
                "prompt_system": "Custom system",
                "prompt_user": "Custom user",
                "editor_comment": "Оставь цифры",
                "preset": "",
            },
        )

        self.assertEqual(response.status_code, HTTPStatus.FOUND)
        mocked_default_rewriter.assert_called_once_with(project=self.story.project)
        rewriter.rewrite.assert_called_once()
        _, kwargs = rewriter.rewrite.call_args
        self.assertEqual(kwargs["editor_comment"], "Оставь цифры")
        self.assertIsNone(kwargs["preset"])
        self.assertEqual(
            kwargs["messages_override"],
            [
                {"role": "system", "content": "Custom system"},
                {"role": "user", "content": "Custom user"},
            ],
        )

    @patch("stories.paperbird_stories.views.default_rewriter")
    def test_confirm_requires_both_prompts(self, mocked_default_rewriter: MagicMock) -> None:
        url = reverse("stories:detail", args=[self.story.pk])
        response = self.client.post(
            url,
            {
                "action": "rewrite",
                "prompt_confirm": "1",
                "prompt_system": "",
                "prompt_user": "User",  # только user заполнен
                "editor_comment": "Комментарий",
                "preset": "",
            },
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertTemplateUsed(response, "stories/story_prompt_preview.html")
        prompt_form = response.context["prompt_form"]
        self.assertIn("prompt_system", prompt_form.errors)
        mocked_default_rewriter.assert_not_called()

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

    @patch("stories.paperbird_stories.services.enqueue_task")
    def test_schedule_publication_enqueues_worker(self, mocked_enqueue) -> None:
        class DummyBackend:
            def send(self, *, story, text, target):  # pragma: no cover - not used
                raise AssertionError("Should not send immediately")

        publish_at = timezone.now() + timedelta(hours=1)
        publisher = StoryPublisher(backend=DummyBackend())

        publication = publisher.publish(
            self.story,
            target="@channel",
            scheduled_for=publish_at,
        )

        self.assertEqual(publication.status, Publication.Status.SCHEDULED)
        mocked_enqueue.assert_called_once()
        args, kwargs = mocked_enqueue.call_args
        self.assertEqual(args[0], WorkerTask.Queue.PUBLISH)
        self.assertEqual(kwargs["payload"], {"publication_id": publication.pk})
        self.assertEqual(kwargs["scheduled_for"], publish_at)

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


class StoryPublishFormTests(TestCase):
    def test_accepts_future_datetime(self) -> None:
        future = timezone.localtime(timezone.now() + timedelta(hours=2))
        form = StoryPublishForm(
            data={
                "target": "@channel",
                "publish_at": future.strftime("%Y-%m-%dT%H:%M"),
            }
        )

        self.assertTrue(form.is_valid())
        cleaned = form.cleaned_data["publish_at"]
        self.assertEqual(
            timezone.localtime(cleaned).replace(second=0, microsecond=0),
            future.replace(second=0, microsecond=0),
        )

    def test_rejects_past_datetime(self) -> None:
        past = timezone.localtime(timezone.now() - timedelta(minutes=5))
        form = StoryPublishForm(
            data={
                "target": "@channel",
                "publish_at": past.strftime("%Y-%m-%dT%H:%M"),
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("publish_at", form.errors)

    def test_normalizes_target_links(self) -> None:
        form = StoryPublishForm(data={"target": "https://t.me/example"})
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["target"], "@example")

    def test_requires_target(self) -> None:
        form = StoryPublishForm(data={"target": "   "})
        self.assertFalse(form.is_valid())
        self.assertIn("target", form.errors)


class StoryImageFormsTests(TestCase):
    def test_generate_form_requires_prompt(self) -> None:
        form = StoryImageGenerateForm(data={"prompt": "   "})
        self.assertFalse(form.is_valid())
        self.assertIn("prompt", form.errors)

    def test_attach_form_decodes_payload(self) -> None:
        encoded = base64.b64encode(b"binary").decode("ascii")
        form = StoryImageAttachForm(
            data={
                "prompt": "Sunset",
                "image_data": encoded,
                "mime_type": "image/png",
            }
        )
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["image_data"], b"binary")


class StoryImageViewTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("artist", password="pass")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Art")
        self.story = Story.objects.create(
            project=self.project,
            title="История",
            summary="Закат над морем",
        )

    def test_get_renders_form(self) -> None:
        response = self.client.get(reverse("stories:image", kwargs={"pk": self.story.pk}))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        generate_form = response.context["generate_form"]
        self.assertIsInstance(generate_form, StoryImageGenerateForm)
        self.assertEqual(generate_form.initial["model"], self.project.image_model)
        self.assertEqual(generate_form.initial["size"], self.project.image_size)
        self.assertEqual(generate_form.initial["quality"], self.project.image_quality)
        self.assertIn("Сгенерировать", response.content.decode("utf-8"))

    @patch("stories.paperbird_stories.views.default_image_generator")
    def test_generate_action_displays_preview(self, mock_generator) -> None:
        stub_generator = MagicMock()
        stub_generator.generate.return_value = GeneratedImage(data=b"image", mime_type="image/png")
        mock_generator.return_value = stub_generator

        response = self.client.post(
            reverse("stories:image", kwargs={"pk": self.story.pk}),
            data={
                "action": "generate",
                "prompt": "Яркий закат",
                "model": self.project.image_model,
                "size": self.project.image_size,
                "quality": self.project.image_quality,
            },
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertIn("data:image/png;base64", response.content.decode("utf-8"))
        stub_generator.generate.assert_called_once_with(
            prompt="Яркий закат",
            model=self.project.image_model,
            size=self.project.image_size,
            quality=self.project.image_quality,
        )

    def test_attach_action_saves_file(self) -> None:
        url = reverse("stories:image", kwargs={"pk": self.story.pk})
        encoded = base64.b64encode(b"fake-image").decode("ascii")
        media_root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(media_root, ignore_errors=True))

        with override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(
                url,
                data={
                    "action": "attach",
                    "prompt": "Летний пляж",
                    "image_data": encoded,
                    "mime_type": "image/png",
                },
            )

            self.assertEqual(response.status_code, HTTPStatus.FOUND)
            self.story.refresh_from_db()
            self.assertEqual(self.story.image_prompt, "Летний пляж")
            self.assertTrue(self.story.image_file.name)
            stored_path = os.path.join(settings.MEDIA_ROOT, self.story.image_file.name)
            self.assertTrue(os.path.exists(stored_path))

    def test_remove_action_deletes_file(self) -> None:
        media_root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(media_root, ignore_errors=True))

        with override_settings(MEDIA_ROOT=media_root):
            self.story.attach_image(prompt="Preview", data=b"img", mime_type="image/png")
            stored_path = os.path.join(settings.MEDIA_ROOT, self.story.image_file.name)
            self.assertTrue(os.path.exists(stored_path))

            response = self.client.post(
                reverse("stories:image", kwargs={"pk": self.story.pk}),
                data={"action": "remove", "confirm": "True"},
                follow=True,
            )

            self.assertEqual(response.status_code, HTTPStatus.OK)
            self.story.refresh_from_db()
            self.assertFalse(self.story.image_file)
            self.assertFalse(os.path.exists(stored_path))


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
        html = response.content.decode("utf-8")
        self.assertIn("Сюжеты", html)
        self.assertIn("Удалить", html)
        self.assertNotIn('<h2 class="h5">Проекты</h2>', html)
        self.assertNotIn("Создать сюжет", html)

    def test_create_story_via_selection(self) -> None:
        url = reverse("stories:create")
        response = self.client.post(
            url,
            data={"project": self.project.id, "posts": [self.post.id]},
            follow=True,
        )
        self.assertEqual(response.status_code, HTTPStatus.OK)
        story = Story.objects.order_by('-created_at').first()
        assert story is not None
        self.assertEqual(story.project, self.project)
        self.assertEqual(list(story.ordered_posts().values_list('id', flat=True)), [self.post.id])

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

    def test_prompt_snapshot_requires_existing(self) -> None:
        url = reverse("stories:prompt", kwargs={"pk": self.story.pk})
        response = self.client.get(url, follow=True)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "У сюжета ещё нет сохранённого промпта.")

    def test_prompt_snapshot_displays_last_prompt(self) -> None:
        self.story.prompt_snapshot = [
            {"role": "system", "content": "System snapshot"},
            {"role": "user", "content": "User snapshot"},
        ]
        self.story.editor_comment = "Комментарий"
        self.story.save(update_fields=["prompt_snapshot", "editor_comment", "updated_at"])
        url = reverse("stories:prompt", kwargs={"pk": self.story.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, HTTPStatus.OK)
        html = response.content.decode("utf-8")
        self.assertIn("System snapshot", html)
        self.assertIn("User snapshot", html)

    @patch("stories.paperbird_stories.views.default_rewriter")
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

    @patch("stories.paperbird_stories.views.default_rewriter")
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
        mock_rewriter.assert_called_once_with(project=self.story.project)
        mock_instance.rewrite.assert_called_once()
        _args, kwargs = mock_instance.rewrite.call_args
        self.assertEqual(kwargs.get("messages_override"), [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ])

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

    @patch("stories.paperbird_stories.views.default_publisher_for_story")
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
        publication = self._create_publication(
            status=Publication.Status.PUBLISHED,
            message_ids=[101],
            published_at=timezone.now(),
        )
        response = self.client.get(reverse("stories:publications"))
        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertContains(response, "https://t.me/mainchannel/101")

class PublishWorkerTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("worker", password="pass")
        self.project = Project.objects.create(owner=self.user, name="Queue")
        self.source = Source.objects.create(project=self.project, telegram_id=90)
        post = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=77,
            message="Текст",
            posted_at=timezone.now(),
        )
        self.story = StoryFactory(project=self.project).create(post_ids=[post.id], title="Story")
        self.story.apply_rewrite(
            title="Story",
            summary="",
            body="Body",
            hashtags=[],
            sources=[],
            payload={},
        )

    @patch("stories.paperbird_stories.workers.default_publisher_for_story")
    def test_worker_processes_publication(self, mocked_default) -> None:
        publication = Publication.objects.create(
            story=self.story,
            target="@channel",
            status=Publication.Status.SCHEDULED,
            result_text=self.story.compose_publication_text(),
        )

        class StubBackend:
            def send(self, *, story, text, target):
                return PublishResult(
                    message_ids=[55],
                    published_at=timezone.now(),
                    raw={"ok": True},
                )

        mocked_default.return_value = StoryPublisher(backend=StubBackend())

        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.PUBLISH,
            payload={"publication_id": publication.pk},
        )

        result = publish_story_task(task)

        mocked_default.assert_called_once_with(publication.story)
        publication.refresh_from_db()
        self.assertEqual(result["status"], Publication.Status.PUBLISHED)
        self.assertEqual(publication.status, Publication.Status.PUBLISHED)

    def test_worker_requires_publication_id(self) -> None:
        task = WorkerTask.objects.create(queue=WorkerTask.Queue.PUBLISH, payload={})

        with self.assertRaises(TaskExecutionError):
            publish_story_task(task)


class OpenAIImageProviderTests(SimpleTestCase):
    def setUp(self) -> None:
        self.prev_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "test-key"

    def tearDown(self) -> None:
        if self.prev_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self.prev_key

    def test_fallback_without_response_format(self) -> None:
        provider = OpenAIImageProvider(response_format="b64_json")
        captured_payloads: list[dict] = []

        class DummyResponse:
            def __init__(self, payload: str) -> None:
                self._payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return self._payload.encode("utf-8")

        error_stream = io.BytesIO(
            b'{"error":{"message":"Unknown parameter: \\"response_format\\"","code":"unknown_parameter"}}'
        )

        def fake_urlopen(request, timeout=30):
            payload = json.loads(request.data.decode("utf-8"))
            captured_payloads.append(payload)
            if len(captured_payloads) == 1:
                raise HTTPError(
                    provider.api_url,
                    400,
                    "Bad Request",
                    hdrs=None,
                    fp=error_stream,
                )
            data = {
                "data": [
                    {
                        "b64_json": base64.b64encode(b"mock-image").decode("ascii"),
                        "mime_type": "image/png",
                    }
                ]
            }
            return DummyResponse(json.dumps(data))

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            image = provider.generate(prompt="Demo image")

        self.assertEqual(len(captured_payloads), 2)
        self.assertIn("response_format", captured_payloads[0])
        self.assertNotIn("response_format", captured_payloads[1])
        self.assertEqual(image.mime_type, "image/png")
        self.assertEqual(image.data, b"mock-image")

    def test_normalizes_large_size(self) -> None:
        provider = OpenAIImageProvider()

        class DummyResponse:
            def __init__(self, payload: str) -> None:
                self._payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return self._payload.encode("utf-8")

        payloads: list[dict] = []

        def fake_urlopen(request, timeout=30):
            data = json.loads(request.data.decode("utf-8"))
            payloads.append(data)
            response_body = {
                "data": [
                    {
                        "b64_json": base64.b64encode(b"mini").decode("ascii"),
                        "mime_type": "image/png",
                    }
                ]
            }
            return DummyResponse(json.dumps(response_body))

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            image = provider.generate(prompt="Demo", size="2048x2048")

        self.assertEqual(image.data, b"mini")
        self.assertEqual(payloads[0]["size"], "512x512")
