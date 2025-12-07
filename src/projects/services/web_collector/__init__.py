"""Web collector package for preset-driven scraping."""

from .fetcher import FetchResult, HttpFetcher
from .parser import ArticleItem, ArticlePayload, WebCollector
from .selector import SelectorEngine
from .utils import collapse_whitespace, parse_datetime

__all__ = [
    "ArticleItem",
    "ArticlePayload",
    "FetchResult",
    "HttpFetcher",
    "SelectorEngine",
    "WebCollector",
    "collapse_whitespace",
    "parse_datetime",
]
