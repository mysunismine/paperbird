"""Tests covering story publishing services and worker."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from core.models import WorkerTask
from core.services.worker import TaskExecutionError
from projects.models import Post, Project, Source
from stories.paperbird_stories.models import Publication, Story
from stories.paperbird_stories.services import (
    PublicationFailed,
    PublishResult,
    StoryFactory,
    StoryPublisher,
)
from stories.paperbird_stories.workers import publish_story_task

User = get_user_model()


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

    def test_publish_serializes_raw_response(self) -> None:
        sample_dt = timezone.now()

        class RawBackend:
            def send(self, *, story, text, target):
                return PublishResult(
                    message_ids=[1],
                    published_at=sample_dt,
                    raw={
                        "sent_at": sample_dt,
                        "history": (sample_dt - timedelta(minutes=5), sample_dt),
                    },
                )

        publisher = StoryPublisher(backend=RawBackend())
        publication = publisher.publish(self.story, target="@channel")
        self.assertEqual(publication.status, Publication.Status.PUBLISHED)
        self.assertEqual(publication.raw_response["sent_at"], sample_dt.isoformat())
        self.assertIsInstance(publication.raw_response["history"], list)


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
