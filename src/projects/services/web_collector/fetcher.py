"""HTTP fetching helpers with rate limiting."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

try:  # pragma: no cover - import guard for missing dependency during setup
    import httpx  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]


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
        headers = {
            "User-Agent": "PaperbirdWebCollector/1.0 (+https://paperbird.ai)",
            **(fetch_config.get("headers") or {}),
        }
        rate_limit_rps = float(fetch_config.get("rate_limit_rps") or 0)
        if rate_limit_rps > 0:
            self._respect_rate_limit(url, rate_limit_rps)
        try:
            response = httpx.get(
                url,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise RuntimeError(f"HTTP error for {url}: {exc}") from exc
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code} for {url}")
        return FetchResult(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            content=response.text,
        )

    def _respect_rate_limit(self, url: str, rate_limit_rps: float) -> None:
        domain = urlparse(url).netloc
        min_interval = 1.0 / rate_limit_rps if rate_limit_rps else 0
        last = self._last_request_at.get(domain)
        if last:
            elapsed = time.monotonic() - last
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
        self._last_request_at[domain] = time.monotonic()
