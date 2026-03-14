import hashlib
import importlib.util
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import WorkerTask
from projects.forms import SourceCreateForm
from projects.models import Post, Project, Source, WebFetchCache, WebPreset
from projects.services.web_collector import WebCollector, parse_datetime
from projects.services.web_collector.fetcher import HttpFetcher
from projects.services.web_collector.watercrawl import WatercrawlCollector
from projects.services.web_preset_registry import PresetValidationError, WebPresetRegistry
from projects.workers import collect_project_web_sources_task

from . import HAS_BS4, HAS_JSONSCHEMA, User, make_preset_payload

HAS_HTTPX = importlib.util.find_spec("httpx") is not None  # pragma: no cover


@skipUnless(HAS_JSONSCHEMA, "jsonschema не установлена")
class WebPresetRegistryTests(TestCase):
    def test_import_and_reuse_preset(self) -> None:
        registry = WebPresetRegistry()
        payload = make_preset_payload()
        preset = registry.import_payload(json.dumps(payload))
        self.assertEqual(preset.name, "web_example")
        self.assertEqual(preset.status, WebPreset.Status.ACTIVE)
        again = registry.import_payload(json.dumps(payload))
        self.assertEqual(WebPreset.objects.count(), 1)
        self.assertEqual(preset.pk, again.pk)

    def test_invalid_payload_raises(self) -> None:
        registry = WebPresetRegistry()
        with self.assertRaises(PresetValidationError):
            registry.import_payload("{}")

    def test_sources_receive_snapshot_refresh(self) -> None:
        registry = WebPresetRegistry()
        payload = make_preset_payload("site_feed")
        preset = registry.import_payload(json.dumps(payload))
        project = Project.objects.create(
            owner=User.objects.create_user("snap", password="secret"),
            name="Snapshot",
        )
        source = Source.objects.create(
            project=project,
            type=Source.Type.WEB,
            title="Feed",
            web_preset=preset,
            web_preset_snapshot=payload,
        )
        updated_payload = payload | {"fetch": {**payload["fetch"], "timeout_sec": 25}}
        registry.import_payload(json.dumps(updated_payload))
        source.refresh_from_db()
        self.assertEqual(source.web_preset_snapshot["fetch"]["timeout_sec"], 25)


@skipUnless(HAS_JSONSCHEMA, "jsonschema не установлена")
class WebSourceFormTests(TestCase):
    @patch("projects.forms.source.enqueue_source_refresh")
    def test_web_source_created_from_json_payload(self, mock_refresh) -> None:
        user = User.objects.create_user("web", password="secret")
        project = Project.objects.create(owner=user, name="Web feed")
        payload = make_preset_payload("site_feed")
        form = SourceCreateForm(
            data={
                "type": Source.Type.WEB,
                "title": "",
                "telegram_id": "",
                "username": "",
                "invite_link": "",
                "web_preset": "",
                "preset_payload": json.dumps(payload),
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 5,
            },
            project=project,
        )
        self.assertTrue(form.is_valid(), form.errors)
        source = form.save()
        self.assertEqual(source.type, Source.Type.WEB)
        self.assertIsNotNone(source.web_preset)
        self.assertTrue(source.web_preset_snapshot)
        self.assertEqual(source.web_preset.name, "site_feed")
        mock_refresh.assert_not_called()

    @patch("projects.forms.source.enqueue_source_refresh")
    def test_watercrawl_source_created_without_preset(self, mock_refresh) -> None:
        user = User.objects.create_user("wc", password="secret")
        project = Project.objects.create(owner=user, name="Watercrawl feed")
        form = SourceCreateForm(
            data={
                "type": Source.Type.WEB,
                "web_engine": Source.WebEngine.WATERCRAWL,
                "source_url": "https://example.com/blog",
                "title": "",
                "web_preset": "",
                "preset_payload": "",
                "deduplicate_text": "on",
                "deduplicate_media": "on",
                "retention_days": 7,
            },
            project=project,
        )
        self.assertTrue(form.is_valid(), form.errors)
        source = form.save()
        self.assertEqual(source.type, Source.Type.WEB)
        self.assertEqual(source.web_engine, Source.WebEngine.WATERCRAWL)
        self.assertEqual(source.source_url, "https://example.com/blog")
        self.assertIsNone(source.web_preset)
        self.assertEqual(source.web_preset_snapshot, {})
        mock_refresh.assert_not_called()

    def test_watercrawl_requires_source_url(self) -> None:
        user = User.objects.create_user("wc2", password="secret")
        project = Project.objects.create(owner=user, name="Watercrawl feed 2")
        form = SourceCreateForm(
            data={
                "type": Source.Type.WEB,
                "web_engine": Source.WebEngine.WATERCRAWL,
                "source_url": "",
                "web_preset": "",
                "preset_payload": "",
                "retention_days": 7,
            },
            project=project,
        )
        self.assertFalse(form.is_valid())
        self.assertIn("source_url", form.errors)


class WebCollectorUtilsTests(TestCase):
    def test_parse_datetime_strips_location_suffix(self) -> None:
        parsed = parse_datetime("11.11.2025 09:51|Псков")
        self.assertIsNotNone(parsed)
        tz = timezone.get_current_timezone()
        localized = parsed.astimezone(tz)
        self.assertEqual(localized.year, 2025)
        self.assertEqual(localized.month, 11)
        self.assertEqual(localized.day, 11)
        self.assertEqual(localized.hour, 9)
        self.assertEqual(localized.minute, 51)


@skipUnless(HAS_BS4, "beautifulsoup4 не установлена")
class WebCollectorTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("crawler", password="secret")
        self.project = Project.objects.create(owner=self.user, name="Web Crawl")
        self.preset_data = make_preset_payload("crawler")
        checksum = hashlib.sha256(
            json.dumps(self.preset_data, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self.preset = WebPreset.objects.create(
            name=self.preset_data["name"],
            version=self.preset_data["version"],
            schema_version=1,
            status=WebPreset.Status.ACTIVE,
            checksum=checksum,
            config=self.preset_data,
        )
        self.source = Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            title="Crawler",
            web_preset=self.preset,
            web_preset_snapshot=self.preset_data,
            is_active=True,
        )
        self.fetcher = self._make_fetcher()

    def _make_fetcher(self):
        listing = """
        <html><body>
          <article class="item"><a href="https://example.com/article-1">Новость дня</a></article>
        </body></html>
        """
        article = """
        <html><body>
          <h1>Новость дня</h1>
          <div class="body">
            <p>Первый абзац текста</p>
            <img src="/images/photo.jpg" />
            <div class="ad">Реклама</div>
          </div>
        </body></html>
        """
        mapping = {
            "https://example.com/news": listing,
            "https://example.com/article-1": article,
        }

        class FakeFetcher:
            def __init__(self, responses):
                self.responses = responses

            def fetch(self, url, _config):
                return SimpleNamespace(
                    url=url,
                    final_url=url,
                    status_code=200,
                    content=self.responses[url],
                )

        return FakeFetcher(mapping)

    def test_collect_creates_and_skips_duplicates(self) -> None:
        collector = WebCollector(fetcher=self.fetcher)
        stats = collector.collect(self.source)
        self.assertEqual(stats["created"], 1)
        post = Post.objects.get(source=self.source)
        self.assertEqual(post.origin_type, Post.Origin.WEB)
        self.assertEqual(post.source, self.source)
        self.assertTrue(post.content_md)
        self.assertTrue(post.external_link)
        stats_repeat = collector.collect(self.source)
        self.assertEqual(stats_repeat["created"], 0)
        self.assertEqual(Post.objects.filter(source=self.source).count(), 1)

    def test_collect_respects_last_seen_url(self) -> None:
        self.source.web_last_seen_url = "https://example.com/article-1"
        self.source.save(update_fields=["web_last_seen_url", "updated_at"])
        collector = WebCollector(fetcher=self.fetcher)
        stats = collector.collect(self.source)
        self.assertEqual(stats["created"], 0)
        self.assertFalse(Post.objects.filter(source=self.source).exists())

    def test_collect_respects_last_seen_published_at(self) -> None:
        self.source.web_last_seen_published_at = timezone.now()
        snapshot = dict(self.source.web_preset_snapshot)
        snapshot["list_page"] = dict(snapshot["list_page"])
        snapshot["list_page"]["selectors"] = dict(snapshot["list_page"]["selectors"])
        snapshot["list_page"]["selectors"]["published_at"] = "time@datetime"
        self.source.web_preset_snapshot = snapshot
        self.source.save(
            update_fields=[
                "web_last_seen_published_at",
                "web_preset_snapshot",
                "updated_at",
            ]
        )
        self.fetcher.responses["https://example.com/news"] = """
        <html><body>
          <article class="item">
            <a href="https://example.com/article-1">Новость дня</a>
            <time datetime="2024-01-01T00:00:00+00:00"></time>
          </article>
        </body></html>
        """
        collector = WebCollector(fetcher=self.fetcher)
        stats = collector.collect(self.source)
        self.assertEqual(stats["created"], 0)
        self.assertFalse(Post.objects.filter(source=self.source).exists())

    def test_collect_combines_multiple_content_nodes(self) -> None:
        multi_preset = make_preset_payload("multi_content")
        multi_preset["article_page"]["selectors"]["content"] = "div.article__text*"
        checksum = hashlib.sha256(
            json.dumps(multi_preset, sort_keys=True).encode("utf-8")
        ).hexdigest()
        preset = WebPreset.objects.create(
            name=multi_preset["name"],
            version=multi_preset["version"],
            schema_version=1,
            status=WebPreset.Status.ACTIVE,
            checksum=checksum,
            config=multi_preset,
        )
        source = Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            title="Multi source",
            web_preset=preset,
            web_preset_snapshot=multi_preset,
            is_active=True,
        )
        self.fetcher.responses["https://example.com/article-1"] = """
        <html><body>
          <div class="article__text">Первый абзац текста</div>
          <div class="article__text"><strong>Второй абзац</strong> продолжает историю.</div>
        </body></html>
        """
        collector = WebCollector(fetcher=self.fetcher)
        stats = collector.collect(source)
        self.assertEqual(stats["created"], 1)
        post = Post.objects.get(source=source)
        self.assertIn("Первый абзац текста", post.message)
        self.assertIn("Второй абзац", post.message)

    def test_collect_merges_multiple_image_selectors(self) -> None:
        multi_preset = make_preset_payload("multi_images")
        multi_preset["article_page"]["selectors"]["images"] = [
            "div.body img@src*",
            "div.body .gallery@data-src*",
        ]
        checksum = hashlib.sha256(
            json.dumps(multi_preset, sort_keys=True).encode("utf-8")
        ).hexdigest()
        preset = WebPreset.objects.create(
            name=multi_preset["name"],
            version=multi_preset["version"],
            schema_version=1,
            status=WebPreset.Status.ACTIVE,
            checksum=checksum,
            config=multi_preset,
        )
        source = Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            title="Images source",
            web_preset=preset,
            web_preset_snapshot=multi_preset,
            is_active=True,
        )
        self.fetcher.responses["https://example.com/article-1"] = """
        <html><body>
          <div class="body">
            <img src="/images/photo.jpg" />
            <div class="gallery" data-src="https://cdn.example.com/extra.jpg"></div>
          </div>
        </body></html>
        """
        collector = WebCollector(fetcher=self.fetcher)
        collector.collect(source)
        post = Post.objects.get(source=source)
        self.assertIn("https://example.com/images/photo.jpg", post.images_manifest)
        self.assertIn("https://cdn.example.com/extra.jpg", post.images_manifest)


@skipUnless(HAS_HTTPX, "httpx не установлена")
class WebFetcherCacheTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("cache", password="secret")
        self.project = Project.objects.create(owner=self.user, name="Cache Project")
        self.source = Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            title="Cache Source",
            web_preset_snapshot=make_preset_payload("cache_preset"),
            is_active=True,
        )

    def _make_response(self, status_code: int, headers: dict[str, str], text: str):
        class FakeResponse:
            def __init__(self, status_code, headers, text):
                self.status_code = status_code
                self.headers = headers
                self.text = text
                self.url = "https://example.com/news"

        return FakeResponse(status_code, headers, text)

    @patch("projects.services.web_collector.fetcher.httpx.get")
    def test_fetcher_stores_and_reuses_cache_headers(self, mock_get) -> None:
        mock_get.side_effect = [
            self._make_response(
                200,
                {"ETag": "etag-1", "Last-Modified": "Mon, 10 Mar 2025 12:00:00 GMT"},
                "<html>Ok</html>",
            ),
            self._make_response(304, {}, ""),
        ]
        fetcher = HttpFetcher()
        result = fetcher.fetch(
            "https://example.com/news",
            {"cache_source_id": self.source.pk},
        )
        self.assertEqual(result.status_code, 200)
        cache = WebFetchCache.objects.get(source=self.source, url="https://example.com/news")
        self.assertEqual(cache.etag, "etag-1")
        self.assertEqual(cache.last_status_code, 200)

        result = fetcher.fetch(
            "https://example.com/news",
            {"cache_source_id": self.source.pk},
        )
        self.assertEqual(result.status_code, 304)
        cache.refresh_from_db()
        self.assertEqual(cache.last_status_code, 304)

        _, kwargs = mock_get.call_args
        headers = kwargs["headers"]
        self.assertEqual(headers["If-None-Match"], "etag-1")
        self.assertEqual(headers["If-Modified-Since"], "Mon, 10 Mar 2025 12:00:00 GMT")


@skipUnless(HAS_HTTPX, "httpx не установлена")
class WatercrawlCollectorTests(TestCase):
    @override_settings(WATERCRAWL_API_URL="https://api.example.test/crawl")
    @patch("projects.services.web_collector.watercrawl.httpx.post")
    def test_collect_creates_posts_from_watercrawl_documents(self, mock_post) -> None:
        user = User.objects.create_user("wc-collector", password="secret")
        project = Project.objects.create(owner=user, name="WC project")
        source = Source.objects.create(
            project=project,
            type=Source.Type.WEB,
            title="WC source",
            web_engine=Source.WebEngine.WATERCRAWL,
            source_url="https://example.com/blog",
            is_active=True,
        )
        mock_post.return_value = SimpleNamespace(
            status_code=200,
            json=lambda: {
                "data": {
                    "results": [
                        {
                            "url": "https://example.com/blog/post-1",
                            "title": "Post 1",
                            "markdown": "Body 1",
                            "published_at": "2026-03-10T10:00:00+00:00",
                        }
                    ]
                }
            },
        )
        collector = WatercrawlCollector()
        stats = collector.collect(source)
        self.assertEqual(stats["created"], 1)
        post = Post.objects.get(source=source)
        self.assertIn("Body 1", post.message)


class CollectProjectWebSourcesTaskTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("webber", password="secret")
        self.project = Project.objects.create(
            owner=self.user,
            name="Web project",
            collector_enabled=True,
            collector_telegram_interval=90,
            collector_web_interval=120,
        )

    def _add_web_source(self) -> Source:
        preset_data = make_preset_payload("worker_site")
        checksum = hashlib.sha256(
            json.dumps(preset_data, sort_keys=True).encode("utf-8")
        ).hexdigest()
        preset = WebPreset.objects.create(
            name=preset_data["name"],
            version=preset_data["version"],
            schema_version=1,
            status=WebPreset.Status.ACTIVE,
            checksum=checksum,
            config=preset_data,
        )
        return Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            title="Worker source",
            web_preset=preset,
            web_preset_snapshot=preset_data,
            is_active=True,
        )

    def _add_watercrawl_source(self) -> Source:
        return Source.objects.create(
            project=self.project,
            type=Source.Type.WEB,
            title="Watercrawl source",
            web_engine=Source.WebEngine.WATERCRAWL,
            source_url="https://example.com/news",
            is_active=True,
        )

    def test_task_skips_without_sources(self) -> None:
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            payload={"project_id": self.project.id},
        )
        result = collect_project_web_sources_task(task)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_sources")

    @patch("projects.workers.enqueue_task")
    def test_task_enqueues_sources_and_requeues(self, mock_enqueue) -> None:
        source = self._add_web_source()
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            payload={"project_id": self.project.id, "interval": 60},
        )
        result = collect_project_web_sources_task(task)
        self.assertEqual(result["status"], "scheduled")
        self.assertEqual(result["sources"], 1)
        self.assertTrue(result["rescheduled"])
        self.assertGreaterEqual(mock_enqueue.call_count, 2)
        source_call = mock_enqueue.call_args_list[0]
        self.assertEqual(
            source_call.kwargs["payload"],
            {"project_id": self.project.id, "source_id": source.id, "interval": 60},
        )
        scheduler_call = mock_enqueue.call_args_list[-1]
        self.assertEqual(
            scheduler_call.kwargs["payload"],
            {"project_id": self.project.id, "interval": 60},
        )

    @patch("projects.workers.WebCollector.collect")
    def test_task_handles_specific_source_without_reschedule(self, mock_collect) -> None:
        source = self._add_web_source()
        mock_collect.return_value = {"created": 1, "updated": 0, "skipped": 0}
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            payload={"project_id": self.project.id, "interval": 60, "source_id": source.id},
        )
        result = collect_project_web_sources_task(task)
        self.assertEqual(result["created"], 1)
        self.assertFalse(
            WorkerTask.objects.filter(
                queue=WorkerTask.Queue.COLLECTOR_WEB,
                payload__project_id=self.project.id,
                status=WorkerTask.Status.QUEUED,
            )
            .exclude(pk=task.pk)
            .exists()
        )

    @patch("projects.workers.WatercrawlCollector.collect")
    def test_task_uses_watercrawl_engine(self, mock_collect) -> None:
        source = self._add_watercrawl_source()
        mock_collect.return_value = {"created": 2, "updated": 1, "skipped": 0}
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            payload={"project_id": self.project.id, "source_id": source.id, "interval": 60},
        )
        result = collect_project_web_sources_task(task)
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["updated"], 1)
        mock_collect.assert_called_once()

    @patch("projects.workers.enqueue_task")
    def test_source_retry_overrides_applied(self, mock_enqueue) -> None:
        source = self._add_web_source()
        Source.objects.filter(pk=source.pk).update(
            web_retry_max_attempts=7, web_retry_base_delay=45, web_retry_max_delay=300
        )
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            payload={"project_id": self.project.id, "interval": 120},
        )
        collect_project_web_sources_task(task)
        source_call = mock_enqueue.call_args_list[0]
        self.assertEqual(source_call.kwargs["max_attempts"], 7)
        self.assertEqual(source_call.kwargs["base_retry_delay"], 45)
        self.assertEqual(source_call.kwargs["max_retry_delay"], 300)

    @patch("projects.workers.enqueue_task")
    def test_task_skips_blocked_sources(self, mock_enqueue) -> None:
        source = self._add_web_source()
        Source.objects.filter(pk=source.pk).update(
            web_last_status="blocked",
            web_blocked_until=timezone.now() + timedelta(hours=1),
        )
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            payload={"project_id": self.project.id, "interval": 60},
        )
        collect_project_web_sources_task(task)
        for call in mock_enqueue.call_args_list:
            payload = call.kwargs.get("payload") or {}
            self.assertNotIn("source_id", payload)

    @patch("projects.workers.WebCollector.collect")
    def test_task_skips_blocked_source_run(self, mock_collect) -> None:
        source = self._add_web_source()
        Source.objects.filter(pk=source.pk).update(
            web_last_status="blocked",
            web_blocked_until=timezone.now() + timedelta(hours=1),
        )
        task = WorkerTask.objects.create(
            queue=WorkerTask.Queue.COLLECTOR_WEB,
            payload={"project_id": self.project.id, "source_id": source.id, "interval": 60},
        )
        result = collect_project_web_sources_task(task)
        self.assertEqual(result["status"], "ok")
        mock_collect.assert_not_called()
        source.refresh_from_db()
        self.assertEqual(source.web_last_status, "cooldown")
