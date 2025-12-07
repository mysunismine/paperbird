"""Collector package for Telegram post ingestion."""

from telethon.tl.custom.message import Message
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto

from ..telethon_client import TelethonClientFactory
from .post_collector import CollectOptions, PostCollector, StoredMedia
from .runners import (
    collect_for_all_users,
    collect_for_all_users_sync,
    collect_for_user,
    collect_for_user_live,
    collect_for_user_sync,
)
from .utils import _normalize_raw

__all__ = [
    "CollectOptions",
    "MessageMediaDocument",
    "MessageMediaPhoto",
    "Message",
    "PostCollector",
    "StoredMedia",
    "TelethonClientFactory",
    "collect_for_all_users",
    "collect_for_all_users_sync",
    "collect_for_user",
    "collect_for_user_live",
    "collect_for_user_sync",
    "_normalize_raw",
]
