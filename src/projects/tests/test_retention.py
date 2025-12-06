import io
from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from core.models import WorkerTask
from projects.models import Post, Project, Source
from projects.services.retention import purge_expired_posts, schedule_retention_cleanup
from projects.workers import retention_cleanup_task
from stories.paperbird_stories.services import StoryFactory

from . import User


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
