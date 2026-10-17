from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from rae_egta.rag_post_event.llm_readers import (
    GROK_DEFAULT_API_KEY_ENV,
    GROK_DEFAULT_MODEL,
    GROK_READER,
    Grok45Reader,
    build_reader,
)


class ScriptedFakeTransport(httpx.BaseTransport):
    """Deterministic in-process transport; no socket can be opened."""

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("unexpected transport call")
        return httpx.Response(200, json=self.responses.pop(0), request=request)


def completion(*, content: str, reasoning_content: str) -> dict[str, object]:
    return {
        "model": GROK_DEFAULT_MODEL,
        "choices": [
            {
                "message": {
                    "content": content,
                    "reasoning_content": reasoning_content,
                }
            }
        ],
    }


class GrokReaderTests(unittest.TestCase):
    def test_content_is_parsed_and_reasoning_content_is_ignored(self) -> None:
        transport = ScriptedFakeTransport(
            [completion(content="Plus", reasoning_content="A hidden chain ending in Basic")]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            reader = Grok45Reader(
                base_urls=["https://api.example.test/v1"],
                output_dir=Path(temp_dir),
                retries=1,
                transport=transport,
            )
            with patch.dict(os.environ, {GROK_DEFAULT_API_KEY_ENV: "fake-token"}, clear=True):
                response = reader.complete(messages=[{"role": "user", "content": "tier?"}])

        self.assertEqual("Plus", response.answer)
        self.assertNotIn("hidden", response.answer)
        self.assertEqual(GROK_READER, response.reader_family)
        self.assertEqual(GROK_DEFAULT_MODEL, response.model_requested)
        self.assertEqual(1, len(transport.requests))
        request = transport.requests[0]
        self.assertEqual("Bearer fake-token", request.headers["Authorization"])
        payload = json.loads(request.content)
        self.assertEqual(GROK_DEFAULT_MODEL, payload["model"])
        self.assertEqual(0.0, payload["temperature"])

    def test_empty_content_does_not_fall_back_to_reasoning_content(self) -> None:
        transport = ScriptedFakeTransport(
            [completion(content="", reasoning_content="The unsupported fallback is Plus")]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            reader = Grok45Reader(
                base_urls=["https://api.example.test/v1"],
                output_dir=Path(temp_dir),
                retries=1,
                transport=transport,
            )
            with patch.dict(os.environ, {GROK_DEFAULT_API_KEY_ENV: "fake-token"}, clear=True):
                response = reader.complete(messages=[{"role": "user", "content": "tier?"}])

        self.assertEqual("", response.answer)

    def test_api_key_env_is_resolved_without_fallback(self) -> None:
        transport = ScriptedFakeTransport(
            [completion(content="Plus", reasoning_content="ignored")]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            reader = Grok45Reader(
                base_urls=["https://api.example.test/v1"],
                output_dir=Path(temp_dir),
                api_key_env="UNIT_TEST_GROK_KEY",
                retries=1,
                transport=transport,
            )
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "Required API key environment variable is unset: UNIT_TEST_GROK_KEY",
                ):
                    reader.complete(messages=[{"role": "user", "content": "tier?"}])

        self.assertEqual([], transport.requests)

    def test_cache_only_replay_uses_grok_cache_without_transport(self) -> None:
        fill_transport = ScriptedFakeTransport(
            [completion(content="Plus", reasoning_content="ignored")]
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            messages = [{"role": "user", "content": "tier?"}]
            writer = Grok45Reader(
                base_urls=["https://api.example.test/v1"],
                output_dir=output_dir,
                retries=1,
                transport=fill_transport,
            )
            with patch.dict(os.environ, {GROK_DEFAULT_API_KEY_ENV: "fake-token"}, clear=True):
                first = writer.complete(messages=messages)

            replay_transport = ScriptedFakeTransport([])
            replay = Grok45Reader(
                base_urls=["https://api.example.test/v1"],
                output_dir=output_dir,
                require_cache=True,
                transport=replay_transport,
            )
            with patch.dict(os.environ, {}, clear=True):
                second = replay.complete(messages=messages)

        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual("Plus", second.answer)
        self.assertEqual(first.prompt_sha256, second.prompt_sha256)
        self.assertEqual([], replay_transport.requests)

    def test_factory_uses_frozen_grok_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reader = build_reader(
                GROK_READER,
                base_urls=["https://api.example.test/v1"],
                output_dir=Path(temp_dir),
            )

        self.assertIsInstance(reader, Grok45Reader)
        self.assertEqual(GROK_DEFAULT_MODEL, reader.model_id)
        self.assertEqual(GROK_DEFAULT_API_KEY_ENV, reader.api_key_env)
        self.assertEqual(0.0, reader.temperature)
        self.assertEqual({}, reader.extra_body)

    def test_factory_applies_grok_cli_overrides_and_cache_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reader = build_reader(
                GROK_READER,
                base_urls=["https://override.example.test/v1"],
                output_dir=Path(temp_dir),
                model_id="grok-test-model",
                api_key_env="UNIT_TEST_GROK_OVERRIDE_KEY",
                require_cache=True,
            )

        self.assertEqual(["https://override.example.test/v1"], reader.base_urls)
        self.assertEqual("grok-test-model", reader.model_id)
        self.assertEqual("UNIT_TEST_GROK_OVERRIDE_KEY", reader.api_key_env)
        self.assertTrue(reader.require_cache)


if __name__ == "__main__":
    unittest.main()
