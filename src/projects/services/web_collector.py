"""Universal web collector that ingests articles using JSON presets."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

try:  # pragma: no cover - optional dependency guard
    from bs4 import BeautifulSoup, Tag  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore
    Tag = Any  # type: ignore
try:  # pragma: no cover - optional dependency guard
    from dateutil import parser as date_parser  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    date_parser = None  # type: ignore[assignment]
from django.utils import timezone
try:  # pragma: no cover - optional dependency guard
    from markdownify import markdownify as html_to_md  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    def html_to_md(value: str) -> str:
        return value

try:  # pragma: no cover - import guard for missing dependency during setup
    import httpx  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from core.logging import event_logger
from projects.models import Post, Source
from projects.services.web_preset_registry import (
    PresetValidationError,
    WebPresetValidator,
)

logger = event_logger("projects.web_collector")


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


@dataclass(slots=True)
class ArticleItem:
    url: str
    title: str | None = None
    published_at: datetime | None = None


@dataclass(slots=True)
class ArticlePayload:
    source_url: str
    canonical_url: str | None
    title: str
    content_html: str
    content_md: str
    raw_html: str
    published_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)
    images: list[str] = field(default_factory=list)


class SelectorEngine:
    """Minimal CSS selector helper with DSL parsing."""

    def __init__(self, parser: str = "html.parser") -> None:
        self.parser = parser

    def parse(self, html: str) -> BeautifulSoup:
        if BeautifulSoup is None:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "beautifulsoup4 не установлен. Выполните `pip install -r requirements.txt`."
            )
        return BeautifulSoup(html, self.parser)

    def select_items(self, soup: BeautifulSoup, selector: str) -> list[Tag]:
        return list(soup.select(selector))

    def extract(self, node: Tag | BeautifulSoup, expression: str) -> Any:
        spec = self._parse_expression(expression)
        nodes: Iterable[Tag]
        if spec.selector:
            nodes = node.select(spec.selector)
        else:
            nodes = [node]  # act on the current node
        if spec.multiple:
            return [self._extract_value(n, spec.attribute) for n in nodes]
        try:
            target = next(iter(nodes))
        except StopIteration:
            if spec.optional:
                return None
            raise LookupError(f"Selector '{expression}' returned nothing")
        return self._extract_value(target, spec.attribute)

    def _extract_value(self, node: Tag, attribute: str | None) -> str:
        if attribute is None:
            return node.decode_contents().strip()
        if attribute == "text":
            return node.get_text(strip=True)
        return (node.get(attribute) or "").strip()

    @dataclass(slots=True)
    class Expression:
        selector: str | None
        attribute: str | None
        multiple: bool
        optional: bool

    def _parse_expression(self, expression: str) -> "SelectorEngine.Expression":
        optional = expression.endswith("?")
        multiple = expression.endswith("*")
        expr = expression
        if optional:
            expr = expr[:-1]
        if multiple:
            expr = expr[:-1]
        selector = expr
        attribute = None
        if "@" in expr:
            selector, attribute = expr.split("@", 1)
        selector = selector or None
        attribute = attribute or None
        return SelectorEngine.Expression(
            selector=selector.strip() if selector else None,
            attribute=attribute.strip() if attribute else None,
            multiple=multiple,
            optional=optional,
        )


def collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


DATETIME_DELIMITERS = ("|", "•", "·", " / ", " — ", " – ", "—", "−", "―")
DATETIME_PATTERN = re.compile(
    r"\d{1,2}[\./-]\d{1,2}[\./-]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?"
)


def _datetime_candidates(value: str | None) -> list[str]:
    if not value:
        return []
    raw = value.strip()
    normalized = collapse_whitespace(raw.replace("\xa0", " "))
    candidates: list[str] = []
    for candidate in (raw, normalized):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    for delimiter in DATETIME_DELIMITERS:
        if delimiter in normalized:
            trimmed = normalized.split(delimiter, 1)[0].strip()
            if trimmed and trimmed not in candidates:
                candidates.append(trimmed)
    match = DATETIME_PATTERN.search(normalized)
    if match:
        snippet = match.group(0).strip()
        if snippet and snippet not in candidates:
            candidates.append(snippet)
    return candidates


def parse_datetime(value: str | None) -> datetime | None:
    for candidate in _datetime_candidates(value):
        try:
            if date_parser is None:
                parsed = datetime.fromisoformat(candidate)
            else:
                parsed = date_parser.parse(candidate)
        except (ValueError, TypeError):  # pragma: no cover - defensive
            continue
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, timezone=timezone.get_current_timezone())
        return parsed
    return None


def normalize_url(base: str, url: str | None) -> str:
    if not url:
        return ""
    return urljoin(base, url)


def strip_tracking_params(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.query:
        return url
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
    new_query = urlencode(pairs)
    return urlunparse(parsed._replace(query=new_query))


class WebCollector:
    """Runs preset-defined pipeline to fetch, extract, and persist posts."""

    def __init__(
        self,
        *,
        fetcher: HttpFetcher | None = None,
        selector: SelectorEngine | None = None,
        validator: WebPresetValidator | None = None,
    ) -> None:
        self.fetcher = fetcher or HttpFetcher()
        self.selector = selector or SelectorEngine()
        self.validator = validator or WebPresetValidator()

    def collect(self, source: Source) -> dict[str, Any]:
        preset = source.active_web_preset()
        if not preset:
            raise PresetValidationError("Источник не содержит пресет")
        self.validator.validate(preset)
        stats = {"created": 0, "updated": 0, "skipped": 0, "items": 0}
        cutoff = source.retention_cutoff()
        cutoff_utc = cutoff.astimezone(dt_timezone.utc) if cutoff else None
        list_items = self._crawl_list_pages(preset, source)
        logger.info("web_collector_list_items", count=len(list_items), source_id=source.pk)
        for item in list_items:
            stats["items"] += 1
            try:
                article = self._fetch_article(item, preset, source)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.warning("web_collector_article_failed", url=item.url, error=str(exc))
                stats["skipped"] += 1
                continue
            content_hash = Post.make_hash(article.content_md or article.content_html)
            if source.has_web_duplicates(
                source_url=article.source_url,
                canonical_url=article.canonical_url,
                content_hash=content_hash,
            ):
                stats["skipped"] += 1
                continue
            posted_at = article.published_at or timezone.now()
            if cutoff_utc and posted_at:
                aware_posted = posted_at
                if timezone.is_naive(aware_posted):
                    aware_posted = timezone.make_aware(aware_posted, dt_timezone.utc)
                else:
                    aware_posted = aware_posted.astimezone(dt_timezone.utc)
                if aware_posted < cutoff_utc:
                    stats["skipped"] += 1
                    continue
            post, created = Post.create_or_update_web(
                project=source.project,
                source=source,
                source_url=article.source_url,
                canonical_url=article.canonical_url,
                title=article.title,
                content_html=article.content_html,
                content_md=article.content_md,
                raw_html=article.raw_html,
                raw_data=article.metadata,
                posted_at=posted_at,
                images=article.images,
            )
            if created:
                stats["created"] += 1
            else:
                stats["updated"] += 1
        source.web_last_synced_at = timezone.now()
        source.web_last_status = "ok"
        source.save(update_fields=["web_last_synced_at", "web_last_status", "updated_at"])
        return stats

    # --- pipeline helpers -------------------------------------------------

    def _crawl_list_pages(self, preset: dict[str, Any], source: Source) -> list[ArticleItem]:
        list_config = preset.get("list_page") or {}
        fetch_config = preset.get("fetch") or {}
        selectors = list_config.get("selectors") or {}
        item_selector = selectors.get("items")
        if not item_selector:
            return []
        seeds = list_config.get("seeds") or self._default_seeds(preset, source)
        max_pages = (list_config.get("pagination") or {}).get("max_pages", 1)
        pagination_type = (list_config.get("pagination") or {}).get("type", "none")
        pagination_selector = (list_config.get("pagination") or {}).get("selector")
        seen_urls: set[str] = set()
        items: list[ArticleItem] = []
        for seed in seeds:
            current_url = seed
            for _ in range(max_pages):
                page = self.fetcher.fetch(current_url, fetch_config)
                soup = self.selector.parse(page.content)
                for node in self.selector.select_items(soup, item_selector):
                    item_url = self._extract_with_fallback(
                        node,
                        selectors.get("url"),
                        page.final_url,
                    )
                    if not item_url:
                        continue
                    absolute_url = normalize_url(page.final_url, item_url)
                    if absolute_url in seen_urls:
                        continue
                    seen_urls.add(absolute_url)
                    title_expr = selectors.get("title")
                    title = self._safe_extract(node, title_expr)
                    published_expr = selectors.get("published_at")
                    published_at = parse_datetime(self._safe_extract(node, published_expr))
                    items.append(ArticleItem(url=absolute_url, title=title, published_at=published_at))
                next_url = None
                if pagination_type == "selector" and pagination_selector:
                    next_url = self._safe_extract(soup, f"{pagination_selector}@href?")
                if not next_url:
                    break
                current_url = normalize_url(page.final_url, next_url)
        return items

    def _fetch_article(self, item: ArticleItem, preset: dict[str, Any], source: Source) -> ArticlePayload:
        fetch_config = preset.get("fetch") or {}
        article_config = preset.get("article_page") or {}
        selectors = article_config.get("selectors") or {}
        response = self.fetcher.fetch(item.url, fetch_config)
        soup = self.selector.parse(response.content)
        self._apply_cleanup(soup, article_config.get("cleanup") or {})
        title = self._safe_extract(soup, selectors.get("title")) or item.title or item.url
        published_at = parse_datetime(self._safe_extract(soup, selectors.get("published_at"))) or item.published_at or timezone.now()
        content_html = self._extract_content_html(
            soup,
            selectors.get("content"),
            response.content,
        )
        content_html = self._normalize_html(content_html, response.final_url, article_config.get("normalize") or {})
        content_md = self._to_markdown(content_html, article_config.get("normalize") or {})
        canonical_expr = selectors.get("canonical_url")
        canonical_url = self._safe_extract(soup, canonical_expr)
        if canonical_url:
            canonical_url = normalize_url(response.final_url, canonical_url)
        images_expr = selectors.get("images")
        images = self._extract_images(soup, images_expr, response.final_url, article_config.get("media") or {})
        metadata = {
            "title": title,
            "published_at": published_at.isoformat(),
            "source": source.title or source.username or source.id,
        }
        additional_fields = ("category", "author", "source_name", "source_url", "summary")
        for field in additional_fields:
            expr = selectors.get(field)
            if not expr:
                continue
            value = self._safe_extract(soup, expr)
            if value:
                metadata[field] = value
        source_url = selectors.get("source_url")
        if source_url:
            resolved = self._safe_extract(soup, source_url)
            if resolved:
                metadata["source_url"] = normalize_url(response.final_url, resolved)
        return ArticlePayload(
            source_url=response.final_url,
            canonical_url=canonical_url,
            title=title,
            content_html=content_html,
            content_md=content_md,
            raw_html=response.content,
            published_at=published_at,
            metadata=metadata,
            images=images,
        )

    def _extract_content_html(
        self,
        soup: BeautifulSoup,
        expression: str | None,
        default: str,
    ) -> str:
        if not expression:
            return default
        try:
            value = self.selector.extract(soup, expression)
        except LookupError:
            return ""
        if isinstance(value, list):
            fragments = [fragment for fragment in value if fragment]
            return "\n\n".join(fragments)
        return value or ""

    def _normalize_html(self, html: str, base_url: str, normalize_cfg: dict[str, Any]) -> str:
        soup = self.selector.parse(html)
        if normalize_cfg.get("make_absolute_urls"):
            for tag in soup.select("[href]"):
                tag["href"] = normalize_url(base_url, tag.get("href"))
            for tag in soup.select("[src]"):
                tag["src"] = normalize_url(base_url, tag.get("src"))
        if normalize_cfg.get("strip_tracking_params"):
            for tag in soup.select("[href]"):
                tag["href"] = strip_tracking_params(tag.get("href"))
            for tag in soup.select("[src]"):
                tag["src"] = strip_tracking_params(tag.get("src"))
        cleaned = soup.decode_contents()
        if normalize_cfg.get("collapse_whitespace"):
            cleaned = collapse_whitespace(cleaned)
        return cleaned

    def _to_markdown(self, html: str, normalize_cfg: dict[str, Any]) -> str:
        if not html:
            return ""
        if normalize_cfg.get("html_to_md"):
            return html_to_md(html)
        return BeautifulSoup(html, "html.parser").get_text("\n", strip=True)

    def _extract_images(
        self,
        soup: BeautifulSoup,
        expression: str | None,
        base_url: str,
        media_cfg: dict[str, Any],
    ) -> list[str]:
        if not expression:
            return []
        result = self.selector.extract(soup, expression)
        if not isinstance(result, list):
            result = [result]
        prefix = ((media_cfg.get("images") or {}).get("prefix")) or base_url
        urls = []
        for value in result:
            if not value:
                continue
            candidate = normalize_url(prefix, value)
            urls.append(candidate)
        if (media_cfg.get("images") or {}).get("strip_tracking_params"):
            urls = [strip_tracking_params(u) for u in urls]
        return urls

    def _apply_cleanup(self, soup: BeautifulSoup, cleanup_cfg: dict[str, Any]) -> None:
        for selector in cleanup_cfg.get("remove", []):
            for node in soup.select(selector):
                node.decompose()
        for selector in cleanup_cfg.get("unwrap", []):
            for node in soup.select(selector):
                node.unwrap()

    def _safe_extract(self, node: Tag | BeautifulSoup, expression: str | None) -> str | None:
        if not expression:
            return None
        try:
            value = self.selector.extract(node, expression)
        except LookupError:
            return None
        if isinstance(value, list):
            return value[0] if value else None
        return value

    def _extract_with_fallback(self, node: Tag | BeautifulSoup, expression: str | None, base: str) -> str:
        value = self._safe_extract(node, expression or "@href")
        return normalize_url(base, value or "")

    def _default_seeds(self, preset: dict[str, Any], source: Source) -> list[str]:
        match = preset.get("match") or {}
        domains = match.get("domains") or []
        if not domains:
            return []
        return [f"https://{domains[0]}/"]


__all__ = ["WebCollector"]
