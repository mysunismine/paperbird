"""Tests for prompt building helpers."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from projects.models import Post, Project, Source
from stories.paperbird_stories.services import build_prompt

User = get_user_model()


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
            canonical_url="https://example.com/news/1",
        )
        messages = build_prompt(
            posts=[post],
            editor_comment="Сжать до 5 предложений",
            title="Заголовок",
        )
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("1. [СИСТЕМНАЯ РОЛЬ]", messages[0]["content"])
        self.assertIn("3. [ИСТОЧНИКИ / ДОКУМЕНТЫ]", messages[1]["content"])
        self.assertIn("НОВОСТЬ #1", messages[1]["content"])
        self.assertIn("Сжать до 5 предложений", messages[1]["content"])
        self.assertIn("Заголовок", messages[1]["content"])
        self.assertIn("https://example.com/news/1", messages[1]["content"])
