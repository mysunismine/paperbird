"""Unit tests for rewrite helper utilities and providers."""

from __future__ import annotations

import json
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from core.constants import OPENAI_DEFAULT_TEMPERATURE
from stories.paperbird_stories.services import (
    OpenAIChatProvider,
    _openai_temperature_for_model,
    _strip_code_fence,
)


class RewriteHelperTests(SimpleTestCase):
    def test_strip_code_fence_handles_json(self) -> None:
        raw = "```json\n{\"title\": \"Example\"}\n```"
        self.assertEqual(_strip_code_fence(raw), '{"title": "Example"}')

    def test_openai_temperature_for_gpt5(self) -> None:
        self.assertEqual(_openai_temperature_for_model("gpt-5"), 1.0)
        self.assertEqual(_openai_temperature_for_model("gpt-5o"), 1.0)
        self.assertEqual(_openai_temperature_for_model("gpt-4o-mini"), OPENAI_DEFAULT_TEMPERATURE)


class OpenAIChatProviderParsingTests(SimpleTestCase):
    class _FakeResponse:
        def __init__(self, payload: dict) -> None:
            self.body = json.dumps(payload).encode("utf-8")

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    @override_settings(OPENAI_API_KEY="sk-test")
    def test_parses_list_based_content(self) -> None:
        payload = {
            "id": "chatcmpl-list",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": '{"title": "Hi", "content": "Body"}'},
                        ],
                    }
                }
            ],
        }
        provider = OpenAIChatProvider(model="gpt-5")
        with patch("urllib.request.urlopen", return_value=self._FakeResponse(payload)):
            response = provider.run(messages=[{"role": "user", "content": "Hello"}])
        self.assertEqual(response.result["title"], "Hi")
        self.assertEqual(response.response_id, "chatcmpl-list")

    @override_settings(OPENAI_API_KEY="sk-test")
    def test_uses_parsed_payload_when_available(self) -> None:
        payload = {
            "id": "chatcmpl-parsed",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": [],
                        "parsed": {"title": "From parsed", "content": "Structured"},
                    }
                }
            ],
        }
        provider = OpenAIChatProvider(model="gpt-5")
        with patch("urllib.request.urlopen", return_value=self._FakeResponse(payload)):
            response = provider.run(messages=[{"role": "user", "content": "Hello"}])
        self.assertEqual(response.result["title"], "From parsed")
        self.assertEqual(response.response_id, "chatcmpl-parsed")
