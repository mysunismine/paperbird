"""Watercrawl-based web collector for preset-less sources."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

try:  # pragma: no cover - optional dependency guard
    import httpx  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from django.conf import settings
from django.utils import timezone

from projects.models import Post, Source

from .fetcher import HttpBlockedError
from .utils import normalize_url, parse_datetime


class WatercrawlCollector:
    """Collects web posts using a remote Watercrawl-compatible API."""

    def collect(self, source: Source) -> dict[str, int]:
        if source.type != Source.Type.WEB:
            raise RuntimeError("Watercrawl collector supports web sources only.")
        source_url = (source.source_url or "").strip()
        if not source_url:
            raise RuntimeError("У веб-источника не указан source_url для Watercrawl.")
        docs = self._fetch_documents(source_url, source.web_policy())
        stats = {"created": 0, "updated": 0, "skipped": 0, "items": 0}
        cutoff = source.retention_cutoff()
        cutoff_utc = cutoff.astimezone(UTC) if cutoff else None
        last_seen_url = source.web_last_seen_url
        last_seen_published_at = source.web_last_seen_published_at
        newest_url = source.web_last_seen_url
        newest_published_at = source.web_last_seen_published_at

        for doc in docs:
            stats["items"] += 1
            article_url = self._doc_url(doc, source_url)
            if not article_url:
                stats["skipped"] += 1
                continue
            published_at = parse_datetime(self._first_non_empty(doc, ("published_at", "date")))
            if last_seen_url and article_url == last_seen_url:
                break
            if (
                last_seen_published_at
                and published_at
                and published_at <= last_seen_published_at
            ):
                break
            title = self._first_non_empty(doc, ("title", "headline")) or article_url
            canonical_url = self._doc_canonical_url(doc, article_url)
            content_html = self._first_non_empty(doc, ("html", "content_html"))
            content_md = self._first_non_empty(doc, ("markdown", "content_markdown", "text"))
            if not content_md and content_html:
                content_md = content_html
            if not content_html and content_md:
                content_html = f"<p>{content_md}</p>"
            if not content_md and not content_html:
                stats["skipped"] += 1
                continue
            content_hash = Post.make_hash(content_md or content_html)
            if source.has_web_duplicates(
                source_url=article_url,
                canonical_url=canonical_url,
                content_hash=content_hash,
            ):
                stats["skipped"] += 1
                continue
            posted_at = published_at or timezone.now()
            if cutoff_utc and self._is_older_than_cutoff(posted_at, cutoff_utc):
                stats["skipped"] += 1
                continue
            metadata = self._doc_metadata(doc)
            images = self._doc_images(doc, article_url)
            _post, created = Post.create_or_update_web(
                project=source.project,
                source=source,
                source_url=article_url,
                canonical_url=canonical_url,
                title=title,
                content_html=content_html or "",
                content_md=content_md or "",
                raw_html=content_html or "",
                raw_data=metadata,
                posted_at=posted_at,
                images=images,
            )
            if newest_url == source.web_last_seen_url:
                newest_url = article_url
            if published_at and (
                newest_published_at is None or published_at > newest_published_at
            ):
                newest_published_at = published_at
            if created:
                stats["created"] += 1
            else:
                stats["updated"] += 1

        source.web_last_synced_at = timezone.now()
        source.web_last_status = "ok"
        source.web_blocked_until = None
        source.web_block_reason = ""
        source.web_last_seen_url = newest_url or source.web_last_seen_url
        source.web_last_seen_published_at = newest_published_at
        source.save(
            update_fields=[
                "web_last_synced_at",
                "web_last_status",
                "web_blocked_until",
                "web_block_reason",
                "web_last_seen_url",
                "web_last_seen_published_at",
                "updated_at",
            ]
        )
        return stats

    def _fetch_documents(self, source_url: str, policy: dict[str, int]) -> list[dict[str, Any]]:
        if httpx is None:  # pragma: no cover - defensive
            raise RuntimeError("httpx не установлен. Выполните `pip install -r requirements.txt`.")
        api_url = (getattr(settings, "WATERCRAWL_API_URL", "") or "").strip()
        api_key = (getattr(settings, "WATERCRAWL_API_KEY", "") or "").strip()
        timeout_sec = float(getattr(settings, "WATERCRAWL_TIMEOUT_SEC", 30))
        if not api_url:
            raise RuntimeError("Не настроен WATERCRAWL_API_URL.")
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers["X-API-Key"] = api_key
        payload = {
            "url": source_url,
            "limit": policy.get("max_items_per_run", 10),
            "formats": ["markdown", "html"],
        }
        try:
            response = httpx.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=timeout_sec,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Watercrawl request failed: {exc}") from exc
        if response.status_code >= 400:
            if response.status_code in {403, 429, 503}:
                raise HttpBlockedError(url=source_url, status_code=response.status_code)
            raise RuntimeError(f"Watercrawl HTTP {response.status_code}: {response.text[:200]}")
        try:
            payload_data = response.json()
        except ValueError as exc:
            raise RuntimeError("Watercrawl вернул не-JSON ответ.") from exc
        return self._extract_documents(payload_data)

    def _extract_documents(self, payload_data: Any) -> list[dict[str, Any]]:
        if isinstance(payload_data, list):
            return [item for item in payload_data if isinstance(item, dict)]
        if not isinstance(payload_data, dict):
            return []
        if any(key in payload_data for key in ("url", "markdown", "html", "content")):
            return [payload_data]
        candidates = (
            payload_data.get("data"),
            payload_data.get("results"),
            payload_data.get("items"),
            payload_data.get("pages"),
            payload_data.get("documents"),
        )
        for candidate in candidates:
            if isinstance(candidate, list):
                return [item for item in candidate if isinstance(item, dict)]
            if isinstance(candidate, dict):
                nested = (
                    candidate.get("results"),
                    candidate.get("items"),
                    candidate.get("pages"),
                    candidate.get("documents"),
                )
                for nested_candidate in nested:
                    if isinstance(nested_candidate, list):
                        return [item for item in nested_candidate if isinstance(item, dict)]
        return []

    def _doc_url(self, doc: dict[str, Any], base_url: str) -> str:
        url = self._first_non_empty(doc, ("url", "source_url", "link", "id"))
        if not url:
            return ""
        return normalize_url(base_url, url)

    def _doc_canonical_url(self, doc: dict[str, Any], fallback_url: str) -> str | None:
        canonical = self._first_non_empty(doc, ("canonical_url", "canonical", "final_url"))
        if not canonical:
            metadata = doc.get("metadata")
            if isinstance(metadata, dict):
                canonical = self._first_non_empty(metadata, ("canonical_url", "canonical"))
        if not canonical:
            return None
        return normalize_url(fallback_url, canonical)

    def _doc_metadata(self, doc: dict[str, Any]) -> dict[str, Any]:
        metadata = doc.get("metadata")
        normalized: dict[str, Any] = {}
        if isinstance(metadata, dict):
            normalized.update(metadata)
        for key in ("title", "author", "summary", "category", "source_name", "source_url"):
            value = doc.get(key)
            if isinstance(value, str) and value.strip():
                normalized[key] = value.strip()
        return normalized

    def _doc_images(self, doc: dict[str, Any], base_url: str) -> list[str]:
        value = doc.get("images")
        if not isinstance(value, Iterable) or isinstance(value, str | bytes):
            return []
        images: list[str] = []
        seen: set[str] = set()
        for item in value:
            image_url = ""
            if isinstance(item, str):
                image_url = item
            elif isinstance(item, dict):
                image_url = self._first_non_empty(item, ("url", "src", "href"))
            if not image_url:
                continue
            normalized = normalize_url(base_url, image_url)
            if normalized in seen:
                continue
            seen.add(normalized)
            images.append(normalized)
        return images

    def _first_non_empty(self, payload: dict[str, Any], keys: tuple[str, ...]) -> str:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _is_older_than_cutoff(self, posted_at: datetime, cutoff_utc: datetime) -> bool:
        aware_posted = posted_at
        if timezone.is_naive(aware_posted):
            aware_posted = timezone.make_aware(aware_posted, UTC)
        else:
            aware_posted = aware_posted.astimezone(UTC)
        return aware_posted < cutoff_utc
