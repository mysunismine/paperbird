"""Общие помощники для тестов приложения projects."""

import importlib.util

from django.contrib.auth import get_user_model

User = get_user_model()


def make_preset_payload(name: str = "web_example") -> dict:
    return {
        "name": name,
        "version": "1.0.0",
        "match": {"domains": ["example.com"]},
        "fetch": {"timeout_sec": 5},
        "list_page": {
            "seeds": ["https://example.com/news"],
            "selectors": {
                "items": "article.item",
                "url": "a@href",
                "title": "a@text",
            },
            "pagination": {"type": "none"},
        },
        "article_page": {
            "selectors": {
                "title": "h1@text",
                "content": "div.body",
                "images": "div.body img@src*",
            },
            "cleanup": {"remove": ["div.ad"], "unwrap": []},
            "normalize": {"html_to_md": True},
        },
    }


HAS_BS4 = importlib.util.find_spec("bs4") is not None  # pragma: no cover
HAS_JSONSCHEMA = importlib.util.find_spec("jsonschema") is not None  # pragma: no cover
