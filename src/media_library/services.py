"""Helpers for media library operations."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from django.conf import settings

from projects.models import Post


def resolve_post_media(post: Post) -> dict[str, Any] | None:
    path_value = (post.media_path or "").strip()
    if not path_value:
        return None

    root = Path(settings.MEDIA_ROOT or ".").resolve()
    media_path = Path(path_value)
    if not media_path.is_absolute():
        media_path = root / media_path
    try:
        resolved = media_path.resolve()
    except (OSError, RuntimeError):
        return None

    if root and not str(resolved).startswith(str(root)):
        return None
    if not resolved.exists() or not resolved.is_file():
        return None

    mime, _ = mimetypes.guess_type(str(resolved))
    if mime and not mime.startswith("image/"):
        return None

    media_prefix = (settings.MEDIA_URL or "").rstrip("/")
    relative_path = None
    try:
        relative_path = resolved.relative_to(root).as_posix()
    except ValueError:
        pass
    url = None
    if media_prefix and relative_path:
        url = f"{media_prefix.rstrip('/')}/{relative_path.lstrip('/')}"
    if not url:
        url = resolved.as_posix()

    return {
        "post": post,
        "path": resolved,
        "mime": mime or "image/jpeg",
        "url": url,
        "file_name": resolved.name,
    }
