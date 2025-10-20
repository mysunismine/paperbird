"""Tests for worker queue and error handling."""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.constants import REWRITE_MAX_ATTEMPTS
from core.models import WorkerTask
from core.services.worker import TaskExecutionError, WorkerRunner, enqueue_task


class WorkerQueueTests(TestCase):
    def test_enqueue_task_uses_queue_defaults(self) -> None:
        task = enqueue_task("rewrite", payload={"story_id": 42})
        self.assertEqual(task.queue, "rewrite")
        self.assertEqual(task.status, WorkerTask.Status.QUEUED)
        self.assertEqual(task.payload["story_id"], 42)
        self.assertEqual(task.max_attempts, REWRITE_MAX_ATTEMPTS)  # from queue defaults
        self.assertLessEqual(task.available_at - timezone.now(), timedelta(seconds=1))

    def test_worker_marks_task_succeeded(self) -> None:
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

    def test_worker_requeues_on_retryable_error(self) -> None:
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
