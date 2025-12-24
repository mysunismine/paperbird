"""Helpers to persist post media locally for later attachment."""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path
from urllib.parse import urlparse

import httpx
from django.conf import settings

from projects.models import Post


def ensure_post_media_local(post: Post, *, timeout: float | None = None) -> list[str]:
    """Скачивает изображения из манифеста поста в MEDIA_ROOT, если они ещё не локальные.

    Возвращает список относительных путей сохранённых файлов.
    """

    media_prefix = (settings.MEDIA_URL or "").rstrip("/")
    manifest = post.images_manifest or []
    seen_urls: set[str] = set()
    stored: list[str] = []
    updated_manifest: list = []

    def is_local(url: str) -> tuple[bool, str | None]:
        parsed = urlparse(url)
        path = parsed.path if parsed.scheme else url
        if media_prefix and path.startswith(media_prefix):
            relative = path[len(media_prefix) :].lstrip("/")
            return True, relative or None
        if not parsed.scheme:
            return True, path
        return False, None

    def save_bytes(data: bytes, *, mime: str | None, url_hint: str) -> str | None:
        extension = (
            mimetypes.guess_extension(mime or "") or Path(urlparse(url_hint).path).suffix or ".jpg"
        )
        filename = f"{post.id}_{uuid.uuid4().hex}{extension}"
        relative_path = (
            Path("uploads")
            / "media"
            / str(post.project_id or "0")
            / str(post.source_id or "0")
            / filename
        )
        root = Path(settings.MEDIA_ROOT or ".")
        absolute = root / relative_path
        absolute.parent.mkdir(parents=True, exist_ok=True)
        absolute.write_bytes(data)
        return relative_path.as_posix()

    for entry in manifest:
        url = ""
        entry_type = None
        if isinstance(entry, str):
            url = entry
        elif isinstance(entry, dict):
            url = entry.get("url") or entry.get("src") or ""
            entry_type = entry.get("type") or entry.get("media_type")

        if not url:
            continue

        if url in seen_urls:
            continue
        seen_urls.add(url)

        local, relative = is_local(url)
        if local and relative:
            stored.append(relative)
            updated_manifest.append(_manifest_entry(entry, relative, media_prefix, entry_type))
            continue

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            updated_manifest.append(entry)
            continue

        try:
            response = httpx.get(url, timeout=timeout or 30.0)
        except Exception:
            updated_manifest.append(entry)
            continue
        if response.status_code != 200 or not response.content:
            updated_manifest.append(entry)
            continue

        mime = (response.headers.get("content-type") or "").split(";")[0].strip()
        if mime and not mime.startswith("image/"):
            updated_manifest.append(entry)
            continue

        relative_path = save_bytes(response.content, mime=mime, url_hint=url)
        if not relative_path:
            updated_manifest.append(entry)
            continue

        stored.append(relative_path)
        updated_manifest.append(_manifest_entry(entry, relative_path, media_prefix, entry_type))

    if updated_manifest != manifest or (stored and not post.media_path):
        update_fields = []
        if updated_manifest != manifest:
            post.images_manifest = updated_manifest
            update_fields.append("images_manifest")
        if stored and not post.media_path:
            post.media_path = stored[0]
            post.media_type = post.media_type or "image"
            update_fields.extend(["media_path", "media_type"])
        if update_fields:
            post.save(update_fields=[*update_fields, "updated_at"])
    return stored


def _manifest_entry(
    entry,
    relative_path: str,
    media_prefix: str | None,
    entry_type: str | None,
) -> dict:
    url = relative_path
    if media_prefix:
        url = f"{media_prefix.rstrip('/')}/{relative_path.lstrip('/')}"
    base = {"url": url}
    if entry_type:
        base["type"] = entry_type
    if isinstance(entry, dict):
        base.update(
            {k: v for k, v in entry.items() if k not in {"url", "src", "type", "media_type"}}
        )
    base["local_path"] = relative_path
    return base
