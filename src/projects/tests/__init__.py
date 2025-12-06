"""Общие помощники для тестов приложения projects."""

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


try:  # pragma: no cover - опциональная зависимость
    import bs4  # type: ignore

    HAS_BS4 = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_BS4 = False

try:  # pragma: no cover - опциональная зависимость
    import jsonschema  # type: ignore

    HAS_JSONSCHEMA = True
except ModuleNotFoundError:  # pragma: no cover
    HAS_JSONSCHEMA = False
