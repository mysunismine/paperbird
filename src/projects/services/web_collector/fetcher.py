"""HTTP fetching helpers with rate limiting."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

try:  # pragma: no cover - import guard for missing dependency during setup
    import httpx  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from django.utils import timezone

from projects.models import WebFetchCache


@dataclass(slots=True)
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content: str


class HttpFetcher:
    """HTTP client with simple domain-based rate limiting."""

    def __init__(self) -> None:
        self._last_request_at: dict[str, float] = {}

    def fetch(self, url: str, fetch_config: dict[str, Any]) -> FetchResult:
        if httpx is None:  # pragma: no cover - defensive
            raise RuntimeError("httpx не установлен. Выполните `pip install -r requirements.txt`.")
        timeout = float(fetch_config.get("timeout_sec") or 15)
        cache_source_id = fetch_config.get("cache_source_id")
        cache_entry = None
        headers = {
            "User-Agent": "PaperbirdWebCollector/1.0 (+https://paperbird.ai)",
            **(fetch_config.get("headers") or {}),
        }
        if cache_source_id:
            cache_entry = WebFetchCache.objects.filter(
                source_id=cache_source_id, url=url
            ).first()
            if cache_entry:
                if cache_entry.etag:
                    headers["If-None-Match"] = cache_entry.etag
                if cache_entry.last_modified:
                    headers["If-Modified-Since"] = cache_entry.last_modified
        rate_limit_rps = float(fetch_config.get("rate_limit_rps") or 0)
        min_interval = float(fetch_config.get("min_interval_sec") or 0)
        jitter_sec = float(fetch_config.get("jitter_sec") or 0)
        if rate_limit_rps > 0 or min_interval > 0 or jitter_sec > 0:
            self._respect_rate_limit(url, rate_limit_rps, min_interval, jitter_sec)
        try:
            response = httpx.get(
                url,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"HTTP error for {url}: {exc}") from exc
        if cache_source_id:
            etag = response.headers.get("ETag") or response.headers.get("etag") or ""
            last_modified = (
                response.headers.get("Last-Modified") or response.headers.get("last-modified") or ""
            )
            if cache_entry and not etag:
                etag = cache_entry.etag
            if cache_entry and not last_modified:
                last_modified = cache_entry.last_modified
            if cache_entry:
                WebFetchCache.objects.filter(pk=cache_entry.pk).update(
                    etag=etag,
                    last_modified=last_modified,
                    last_status_code=response.status_code,
                    last_checked_at=timezone.now(),
                    updated_at=timezone.now(),
                )
            else:
                WebFetchCache.objects.create(
                    source_id=cache_source_id,
                    url=url,
                    etag=etag,
                    last_modified=last_modified,
                    last_status_code=response.status_code,
                    last_checked_at=timezone.now(),
                )
        if response.status_code == 304:
            return FetchResult(
                url=url,
                final_url=str(response.url),
                status_code=response.status_code,
                content="",
            )
        if response.status_code >= 400:
            if response.status_code in {403, 429, 503}:
                raise HttpBlockedError(url=url, status_code=response.status_code)
            raise HttpFetchError(url=url, status_code=response.status_code)
        return FetchResult(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            content=response.text,
        )

    def _respect_rate_limit(
        self,
        url: str,
        rate_limit_rps: float,
        min_interval: float,
        jitter_sec: float,
    ) -> None:
        domain = urlparse(url).netloc
        rps_interval = 1.0 / rate_limit_rps if rate_limit_rps else 0
        base_interval = max(min_interval, rps_interval)
        if jitter_sec:
            base_interval += random.uniform(0, jitter_sec)
        last = self._last_request_at.get(domain)
        if last:
            elapsed = time.monotonic() - last
            if elapsed < base_interval:
                time.sleep(base_interval - elapsed)
        self._last_request_at[domain] = time.monotonic()


class HttpFetchError(RuntimeError):
    def __init__(self, *, url: str, status_code: int) -> None:
        super().__init__(f"HTTP {status_code} for {url}")
        self.url = url
        self.status_code = status_code


class HttpBlockedError(HttpFetchError):
    """Возникает, когда источник вероятно блокирует доступ."""
