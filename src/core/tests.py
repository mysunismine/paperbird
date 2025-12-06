"""Тесты для очереди воркеров и обработки ошибок."""

from __future__ import annotations

import io
import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.constants import REWRITE_MAX_ATTEMPTS
from core.logging import event_logger, logging_context
from core.middleware import RequestContextMiddleware
from core.models import WorkerTask
from core.services.worker import TaskExecutionError, WorkerRunner, enqueue_task
from projects.models import Post, Project, Source

User = get_user_model()


class WorkerQueueTests(TestCase):
    """Тесты для механизма фоновых задач."""

    def test_enqueue_task_uses_queue_defaults(self) -> None:
        """Проверяет, что задача использует настройки очереди по умолчанию."""
        task = enqueue_task("rewrite", payload={"story_id": 42})
        self.assertEqual(task.queue, "rewrite")
        self.assertEqual(task.status, WorkerTask.Status.QUEUED)
        self.assertEqual(task.payload["story_id"], 42)
        self.assertEqual(task.max_attempts, REWRITE_MAX_ATTEMPTS)  # from queue defaults
        self.assertLessEqual(task.available_at - timezone.now(), timedelta(seconds=1))

    def test_worker_marks_task_succeeded(self) -> None:
        """Проверяет, что воркер помечает задачу как успешно выполненную."""
        task = enqueue_task("default", payload={"value": 10})

        def handler(current_task: WorkerTask) -> dict:
            return {"result": current_task.payload["value"] * 2}

        runner = WorkerRunner(queue="default", handler=handler, worker_id="test-worker")
        processed = runner.run_once()
        self.assertEqual(processed, 1)

        task.refresh_from_db()
        self.assertEqual(task.status, WorkerTask.Status.SUCCEEDED)
        self.assertEqual(task.result, {"result": 20})
        self.assertEqual(task.attempts, 1)
        attempts = list(task.attempts_log.all())
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].status, WorkerTask.Status.SUCCEEDED)
        self.assertFalse(attempts[0].will_retry)

    def test_enqueue_task_preserves_correlation_from_context(self) -> None:
        """Проверяет, что correlation_id из контекста сохраняется в задаче."""
        with logging_context(correlation_id="cid-test"):
            task = enqueue_task("default", payload={"value": 1})

        self.assertEqual(task.payload["correlation_id"], "cid-test")

    def test_worker_requeues_on_retryable_error(self) -> None:
        """Проверяет, что воркер ставит задачу на повторное выполнение при ошибке."""
        task = enqueue_task("default", payload={"value": 5})
        before = timezone.now()

        def handler(current_task: WorkerTask) -> dict:
            raise TaskExecutionError(
                "Temporary issue",
                code="TEMP_ERROR",
                retry=True,
                retry_in=30,
                payload={"value": current_task.payload["value"]},
            )

        runner = WorkerRunner(queue="default", handler=handler, worker_id="retry-worker")
        runner.run_once()

        task.refresh_from_db()
        self.assertEqual(task.status, WorkerTask.Status.QUEUED)
        self.assertEqual(task.attempts, 1)
        self.assertGreaterEqual(task.available_at, before + timedelta(seconds=29))
        self.assertLessEqual(task.available_at, before + timedelta(seconds=31))
        attempts = list(task.attempts_log.all())
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].status, WorkerTask.Status.FAILED)
        self.assertTrue(attempts[0].will_retry)
        self.assertEqual(attempts[0].error_code, "TEMP_ERROR")

    def test_worker_fails_when_attempts_exhausted(self) -> None:
        """Проверяет, что задача помечается как проваленная после исчерпания попыток."""
        task = enqueue_task("default", payload={}, max_attempts=1)

        def handler(current_task: WorkerTask) -> dict:
            raise TaskExecutionError("Still failing", code="STILL_FAIL")

        runner = WorkerRunner(queue="default", handler=handler, worker_id="fail-worker")
        runner.run_once()

        task.refresh_from_db()
        self.assertEqual(task.status, WorkerTask.Status.FAILED)
        self.assertEqual(task.attempts, 1)
        attempts = list(task.attempts_log.all())
        self.assertEqual(len(attempts), 1)
        self.assertFalse(task.can_retry())
        self.assertFalse(attempts[0].will_retry)

    def test_worker_respects_non_retryable_errors(self) -> None:
        """Проверяет, что воркер не повторяет задачу при фатальной ошибке."""
        task = enqueue_task("default", payload={}, max_attempts=3)

        def handler(current_task: WorkerTask) -> dict:
            raise TaskExecutionError("Fatal", code="FATAL", retry=False)

        runner = WorkerRunner(queue="default", handler=handler, worker_id="fatal-worker")
        runner.run_once()

        task.refresh_from_db()
        self.assertEqual(task.status, WorkerTask.Status.FAILED)
        self.assertEqual(task.attempts, 1)
        attempt = task.attempts_log.get()
        self.assertEqual(attempt.error_code, "FATAL")
        self.assertFalse(attempt.will_retry)

    def test_stale_tasks_revived_before_processing(self) -> None:
        """Проверяет, что зависшие задачи возвращаются в очередь."""
        task = enqueue_task("collector_web", payload={"value": 1})
        stale_time = timezone.now() - timedelta(minutes=15)
        WorkerTask.objects.filter(pk=task.pk).update(
            status=WorkerTask.Status.RUNNING,
            locked_at=stale_time,
            locked_by="stuck-worker",
        )
        revived = WorkerTask.revive_stale(queue="collector_web", max_age_seconds=60)
        self.assertEqual(revived, 1)
        task.refresh_from_db()
        self.assertEqual(task.status, WorkerTask.Status.QUEUED)
        self.assertEqual(task.locked_by, "")


class FeedViewTests(TestCase):
    """Тесты для представлений ленты."""

    def setUp(self) -> None:
        self.user = User.objects.create_user("viewer", password="secret")
        self.client.force_login(self.user)
        self.project = Project.objects.create(owner=self.user, name="Новости")
        self.source = Source.objects.create(project=self.project, telegram_id=1, title="Tech")
        self.other_project = Project.objects.create(owner=self.user, name="Архив")
        self.other_source = Source.objects.create(
            project=self.other_project,
            telegram_id=2,
            title="Политика",
        )
        now = timezone.now()
        self.latest_post = Post.objects.create(
            project=self.project,
            source=self.source,
            telegram_id=10,
            message="Apple представила новый продукт",
            posted_at=now,
        )
        Post.objects.create(
            project=self.other_project,
            source=self.other_source,
            telegram_id=11,
            message="Парламент обсудил меры",
            posted_at=now - timedelta(hours=1),
        )

    def test_feed_requires_authentication(self) -> None:
        """Проверяет, что анонимный пользователь перенаправляется на страницу входа."""
        self.client.logout()
        response = self.client.get(reverse("feed"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("accounts/login", response.url)

    def test_feed_lists_latest_posts(self) -> None:
        """Проверяет, что лента по умолчанию показывает посты последнего проекта."""
        response = self.client.get(reverse("feed"))
        expected = reverse("feed-detail", args=[self.other_project.pk])
        self.assertRedirects(response, expected)

    def test_feed_filters_by_project(self) -> None:
        """Проверяет, что лента фильтрует посты по выбранному проекту."""
        response = self.client.get(reverse("feed"), data={"project": self.project.id})
        expected = reverse("feed-detail", args=[self.project.pk])
        self.assertRedirects(response, expected)


class StructuredLoggingTests(TestCase):
    """Тесты для структурированного логирования."""

    def test_event_logger_combines_context(self) -> None:
        """Проверяет, что логгер событий корректно объединяет контексты."""
        logger = event_logger("paperbird.tests")
        with logging_context(correlation_id="cid-1", user_id=42, project_id=7):
            with self.assertLogs("paperbird.tests", level="INFO") as captured:
                logger.info("story_processed", story_id=5)

        record = captured.records[0]
        payload = record.structured_payload
        self.assertEqual(payload["event"], "story_processed")
        self.assertEqual(payload["correlation_id"], "cid-1")
        self.assertEqual(payload["user_id"], 42)
        self.assertEqual(payload["project_id"], 7)
        self.assertEqual(payload["story_id"], 5)


class RequestContextMiddlewareTests(TestCase):
    """Тесты для middleware контекста запроса."""

    def setUp(self) -> None:
        self.factory = RequestFactory()

    def test_propagates_existing_correlation_id(self) -> None:
        """Проверяет, что middleware использует существующий correlation_id."""
        response = self.client.get("/", HTTP_X_CORRELATION_ID="abc123")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Correlation-ID"], "abc123")

    def test_logs_unhandled_exception(self) -> None:
        """Проверяет, что необработанные исключения логируются."""
        def raising_view(request):
            raise RuntimeError("Взрыв")

        middleware = RequestContextMiddleware(raising_view)
        request = self.factory.get("/boom")

        with self.assertLogs("paperbird.request", level="ERROR") as captured:
            with self.assertRaises(RuntimeError):
                middleware(request)

        record = captured.records[0]
        payload = record.structured_payload
        self.assertEqual(payload["event"], "unhandled_error")
        self.assertEqual(payload["method"], "GET")
        self.assertIn("correlation_id", payload)
        self.assertEqual(payload["error"], "Взрыв")


@override_settings(ROOT_URLCONF="core.tests_urls", DEBUG=False)
class ServerErrorViewTests(TestCase):
    """Тесты для страницы ошибки 500."""

    def setUp(self) -> None:
        super().setUp()
        self.client.raise_request_exception = False

    def test_custom_error_page_renders_with_correlation(self) -> None:
        """Проверяет, что кастомная страница 500 отображает correlation_id."""
        response = self.client.get("/boom/")
        self.assertEqual(response.status_code, 500)
        self.assertContains(response, "Упс! Что-то пошло не так", status_code=500)
        match = re.search(
            r"Идентификатор ошибки: <code>([a-f0-9]+)</code>",
            response.content.decode(),
        )
        self.assertIsNotNone(match)
        correlation_id = match.group(1)
        self.assertEqual(response["X-Correlation-ID"], correlation_id)


class RunCollectorsCommandTests(TestCase):
    """Тесты для команды `run_collectors`."""

    @patch("core.management.commands.run_collectors.make_runner")
    def test_run_collectors_once(self, mock_make_runner) -> None:
        """Тестирует разовый запуск команды."""
        class FakeRunner:
            def __init__(self, queue):
                self.queue = queue
                self.calls = 0

            def run_once(self):
                self.calls += 1
                return 1 if self.queue == "collector" else 0

        mock_make_runner.side_effect = lambda **kwargs: FakeRunner(kwargs["queue"])
        stdout = io.StringIO()
        call_command("run_collectors", "--once", stdout=stdout)
        self.assertIn("Processed 1 tasks", stdout.getvalue())
        self.assertEqual(mock_make_runner.call_count, 2)

    @patch("core.management.commands.run_collectors.time.sleep")
    @patch("core.management.commands.run_collectors.make_runner")
    def test_iterations_limit(self, mock_make_runner, mock_sleep) -> None:
        """Тестирует ограничение на количество итераций."""
        created_runners = []

        class IdleRunner:
            def __init__(self, queue):
                self.queue = queue
                self.calls = 0

            def run_once(self):
                self.calls += 1
                return 0

        def _side_effect(**kwargs):
            runner = IdleRunner(kwargs["queue"])
            created_runners.append(runner)
            return runner

        mock_make_runner.side_effect = _side_effect
        call_command("run_collectors", "--iterations", "2", "--sleep", "0.1")
        self.assertEqual(mock_make_runner.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 1)
        self.assertTrue(all(r.calls == 2 for r in created_runners))
