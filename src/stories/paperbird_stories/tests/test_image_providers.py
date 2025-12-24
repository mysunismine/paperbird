"""Tests for OpenAI image provider behaviour."""

from __future__ import annotations

import base64
import io
import json
import os
from unittest.mock import patch
from urllib.error import HTTPError

from django.test import SimpleTestCase

from stories.paperbird_stories.services import OpenAIImageProvider


class OpenAIImageProviderTests(SimpleTestCase):
    def setUp(self) -> None:
        self.prev_key = os.environ.get("OPENAI_API_KEY")
        os.environ["OPENAI_API_KEY"] = "test-key"

    def tearDown(self) -> None:
        if self.prev_key is None:
            os.environ.pop("OPENAI_API_KEY", None)
        else:
            os.environ["OPENAI_API_KEY"] = self.prev_key

    def test_fallback_without_response_format(self) -> None:
        provider = OpenAIImageProvider(response_format="b64_json")
        captured_payloads: list[dict] = []

        class DummyResponse:
            def __init__(self, payload: str) -> None:
                self._payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return self._payload.encode("utf-8")

        error_stream = io.BytesIO(
            b'{"error":{"message":"Unknown parameter: \\"response_format\\",'
            b'"code":"unknown_parameter"}}'
        )

        def fake_urlopen(request, timeout=30):
            payload = json.loads(request.data.decode("utf-8"))
            captured_payloads.append(payload)
            if len(captured_payloads) == 1:
                raise HTTPError(
                    provider.api_url,
                    400,
                    "Bad Request",
                    hdrs=None,
                    fp=error_stream,
                )
            data = {
                "data": [
                    {
                        "b64_json": base64.b64encode(b"mock-image").decode("ascii"),
                        "mime_type": "image/png",
                    }
                ]
            }
            return DummyResponse(json.dumps(data))

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            image = provider.generate(prompt="Demo image")

        self.assertEqual(len(captured_payloads), 2)
        self.assertIn("response_format", captured_payloads[0])
        self.assertNotIn("response_format", captured_payloads[1])
        self.assertEqual(image.mime_type, "image/png")
        self.assertEqual(image.data, b"mock-image")

    def test_normalizes_large_size(self) -> None:
        provider = OpenAIImageProvider()

        class DummyResponse:
            def __init__(self, payload: str) -> None:
                self._payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return self._payload.encode("utf-8")

        payloads: list[dict] = []

        def fake_urlopen(request, timeout=30):
            data = json.loads(request.data.decode("utf-8"))
            payloads.append(data)
            response_body = {
                "data": [
                    {
                        "b64_json": base64.b64encode(b"mini").decode("ascii"),
                        "mime_type": "image/png",
                    }
                ]
            }
            return DummyResponse(json.dumps(response_body))

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            image = provider.generate(prompt="Demo", size="2048x2048")

        self.assertEqual(image.data, b"mini")
        self.assertEqual(payloads[0]["size"], "1024x1024")

    def test_maps_quality_for_dalle_models(self) -> None:
        provider = OpenAIImageProvider(model="dall-e-3")

        class DummyResponse:
            def __init__(self, payload: str) -> None:
                self._payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self) -> bytes:
                return self._payload.encode("utf-8")

        payloads: list[dict] = []

        def fake_urlopen(request, timeout=30):
            data = json.loads(request.data.decode("utf-8"))
            payloads.append(data)
            response_body = {
                "data": [
                    {
                        "b64_json": base64.b64encode(b"mini").decode("ascii"),
                        "mime_type": "image/png",
                    }
                ]
            }
            return DummyResponse(json.dumps(response_body))

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            provider.generate(prompt="Demo", quality="low")
            provider.generate(prompt="Demo", quality="high")

        self.assertEqual(payloads[0]["quality"], "standard")
        self.assertEqual(payloads[1]["quality"], "hd")
