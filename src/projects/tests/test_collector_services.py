import asyncio
import tempfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from core.models import WorkerTask
from projects.models import Post, Project, Source
from projects.services.collector import PostCollector, _normalize_raw
from projects.workers import collect_project_posts_task

from . import User


class CollectorSanitizationTests(TestCase):
    def test_normalize_raw_handles_datetime(self) -> None:
        payload = {
            "date": timezone.now(),
            "nested": [timezone.now(), {"another": timezone.now()}],
        }
        normalized = _normalize_raw(payload)
        import json

        json.dumps(normalized)
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
