"""Tests for rewrite services and flows."""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from projects.models import Post, Project, Source
from stories.paperbird_stories.models import RewritePreset, RewriteTask, Story
from stories.paperbird_stories.services import (
    ProviderResponse,
    RewriteFailed,
    StoryFactory,
    StoryRewriter,
)

User = get_user_model()


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
        system_prompt = provider.calls[0][0]["content"]
        self.assertIn("1. [СИСТЕМНАЯ РОЛЬ]", system_prompt)
        self.assertIn(self.project.name, system_prompt)

    def test_rewrite_uses_preset(self) -> None:
        preset = RewritePreset.objects.create(
            project=self.project,
            name="Аналитика",
            style="деловой, формальный",
            editor_comment="Сфокусируйся на ключевых метриках",
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
        self.assertIn("Описание: Сосредоточиться на цифрах", user_message)
        self.assertIn("деловой, формальный", user_message)
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
        self.story = StoryFactory(project=self.project).create(
            post_ids=[self.post.id],
            title="Предпросмотр",
        )
        self.client.login(username="viewer", password="pass")

    def test_preview_displays_prompt_form(self) -> None:
        url = reverse("stories:detail", args=[self.story.pk])
        response = self.client.post(url, {"action": "rewrite", "preview": "1"})

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertTemplateUsed(response, "stories/story_prompt_preview.html")
        prompt_form = response.context["prompt_form"]
        self.assertIn("System prompt", prompt_form["prompt_system"].label)
        self.assertIn("ФОРМАТ ОТВЕТА", prompt_form["prompt_user"].value())

    @patch("stories.paperbird_stories.views.story_detail.default_rewriter")
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

    @patch("stories.paperbird_stories.views.story_detail.default_rewriter")
    def test_confirm_requires_both_prompts(self, mocked_default_rewriter: MagicMock) -> None:
        url = reverse("stories:detail", args=[self.story.pk])
        response = self.client.post(
            url,
            {
                "action": "rewrite",
                "prompt_confirm": "1",
                "prompt_system": "",
                "prompt_user": "User",
                "editor_comment": "Комментарий",
                "preset": "",
            },
        )

        self.assertEqual(response.status_code, HTTPStatus.OK)
        self.assertTemplateUsed(response, "stories/story_prompt_preview.html")
        prompt_form = response.context["prompt_form"]
        self.assertIn("prompt_system", prompt_form.errors)
        mocked_default_rewriter.assert_not_called()
