from __future__ import annotations

import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from rae_egta.rag_post_event.document_backend import Chunk, RetrievedChunk
from rae_egta.rag_post_event.llm_readers import (
    MINIMAX_READER,
    PROMPT_POLICY_VERSION,
    QWEN_READER,
    READER_SYSTEM_PROMPT,
    MiniMaxM3Reader,
    OpenAICompatibleReader,
    Qwen35Reader,
    build_reader,
    clean_llm_content,
    reader_messages,
    reader_user_prompt,
    stable_request_payload,
)


def retrieved_fixture() -> tuple[RetrievedChunk, ...]:
    chunk = Chunk(
        chunk_id="doc-1-p0",
        doc_id="doc-1",
        event_id=1,
        doc_role="old_internal_role",
        version="old_internal_version",
        paragraph_id=0,
        text="The currently supported product tier is Plus.",
        answer="Plus",
        evidence_span="currently supported product tier is Plus",
    )
    return (RetrievedChunk(chunk=chunk, score=3.5, rank=1),)


class LLMReaderAdapterTests(unittest.TestCase):
    def test_extract_when_present_prompt_policy_is_frozen(self) -> None:
        expected_prompt = (
            "Answer only from the provided documents. For a query about an attribute, "
            "use the highest-ranked provided document that states a value for that "
            "attribute and return that value verbatim. If a value is stated, do not "
            "return UNKNOWN. Return UNKNOWN only if no provided document states a value "
            "for the queried attribute. Return only that shortest supported value, "
            "without explanation. Do not use outside knowledge."
        )
        self.assertEqual("naturalrev_reader_v2_extract_when_present", PROMPT_POLICY_VERSION)
        self.assertEqual(expected_prompt, READER_SYSTEM_PROMPT)
        messages = reader_messages(
            query="As of now, what is the status of entity_13_0083?",
            retrieved=retrieved_fixture(),
        )
        self.assertEqual(
            {"role": "system", "content": expected_prompt},
            messages[0],
        )

    def test_qwen_live_result_is_cached_and_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            reader = Qwen35Reader(
                base_urls=["http://127.0.0.1:8080/v1/"],
                output_dir=output_dir,
                retries=1,
            )
            with patch.object(
                reader,
                "_post_chat_completion",
                return_value=("Plus", "qwen-returned"),
            ) as mocked_post:
                first = reader.answer(query="Which tier is supported?", retrieved=retrieved_fixture())

            self.assertFalse(first.cache_hit)
            self.assertEqual(first.answer, "Plus")
            self.assertEqual(first.reader_family, QWEN_READER)
            self.assertEqual(reader.real_call_count, 1)
            mocked_post.assert_called_once()

            replay = Qwen35Reader(
                base_urls=["http://127.0.0.1:8080/v1"],
                output_dir=output_dir,
                require_cache=True,
            )
            second = replay.answer(query="Which tier is supported?", retrieved=retrieved_fixture())
            self.assertTrue(second.cache_hit)
            self.assertEqual(second.answer, "Plus")
            self.assertEqual(first.prompt_sha256, second.prompt_sha256)
            self.assertEqual(replay.real_call_count, 0)
            self.assertEqual(replay.cache_hit_count, 1)

            log_rows = [
                json.loads(line)
                for line in (output_dir / "qwen_reader_requests.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual([row["status"] for row in log_rows], ["success", "cache_hit"])

    def test_concurrent_identical_prompts_are_cached_with_one_reader_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reader = Qwen35Reader(
                base_urls=["http://127.0.0.1:8080/v1"],
                output_dir=Path(temp_dir),
                retries=1,
            )
            with patch.object(
                reader,
                "_post_chat_completion",
                return_value=("Plus", "qwen-returned"),
            ) as mocked_post:
                with ThreadPoolExecutor(max_workers=4) as executor:
                    responses = list(
                        executor.map(
                            lambda _: reader.answer(
                                query="Which tier is supported?",
                                retrieved=retrieved_fixture(),
                            ),
                            range(4),
                        )
                    )

            self.assertEqual(1, mocked_post.call_count)
            self.assertEqual(1, reader.real_call_count)
            self.assertEqual(3, reader.cache_hit_count)
            self.assertEqual(1, sum(not row.cache_hit for row in responses))
            self.assertEqual(3, sum(row.cache_hit for row in responses))
            cache_rows = (Path(temp_dir) / "qwen_reader_cache.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()
            self.assertEqual(1, len(cache_rows))

    def test_minimax_adapter_freezes_reasoning_disabled_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reader = MiniMaxM3Reader(
                base_urls=["https://api.example.test/v1"],
                output_dir=Path(temp_dir),
            )
            manifest = reader.manifest()
            self.assertEqual(manifest["reader_family"], MINIMAX_READER)
            self.assertEqual(manifest["model_id"], "MiniMax-M3")
            self.assertEqual(manifest["temperature"], 0.0)
            self.assertEqual(manifest["max_tokens"], 64)
            self.assertEqual(manifest["extra_body"], {"thinking": {"type": "disabled"}})
            self.assertEqual(manifest["api_key_env"], "MINIMAX_API_KEY")

    def test_reader_prompt_does_not_expose_internal_role_or_version(self) -> None:
        prompt = reader_user_prompt(
            query="Which tier is supported?",
            retrieved=retrieved_fixture(),
        )
        self.assertIn("doc_id=doc-1", prompt)
        self.assertIn("The currently supported product tier is Plus.", prompt)
        self.assertNotIn("old_internal_role", prompt)
        self.assertNotIn("old_internal_version", prompt)

    def test_cache_key_separates_reader_families(self) -> None:
        common = {
            "model_id": "same-model-label",
            "messages": [{"role": "user", "content": "question"}],
            "temperature": 0.0,
            "max_tokens": 64,
            "extra_body": {},
        }
        qwen_payload = stable_request_payload(reader_family=QWEN_READER, **common)
        minimax_payload = stable_request_payload(reader_family=MINIMAX_READER, **common)
        self.assertNotEqual(qwen_payload, minimax_payload)

    def test_cache_only_miss_fails_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reader = Qwen35Reader(
                base_urls=["http://127.0.0.1:8080/v1"],
                output_dir=Path(temp_dir),
                require_cache=True,
            )
            with self.assertRaisesRegex(RuntimeError, "cache-only replay missing prompt"):
                reader.answer(query="missing", retrieved=())

    def test_minimax_requires_configured_api_key_before_network(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            reader = MiniMaxM3Reader(
                base_urls=["https://api.example.test/v1"],
                output_dir=Path(temp_dir),
                api_key_env="TEST_MISSING_MINIMAX_KEY",
                retries=1,
            )
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "environment variable is unset"):
                    reader._post_chat_completion(
                        base_url="https://api.example.test/v1",
                        messages=[{"role": "user", "content": "test"}],
                    )

    def test_factory_builds_existing_frozen_readers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            qwen = build_reader(
                QWEN_READER,
                base_urls=["http://127.0.0.1:8080/v1"],
                output_dir=output_dir / "qwen",
            )
            minimax = build_reader(
                MINIMAX_READER,
                base_urls=["https://api.example.test/v1"],
                output_dir=output_dir / "minimax",
            )
            self.assertIsInstance(qwen, Qwen35Reader)
            self.assertIsInstance(minimax, MiniMaxM3Reader)
            with self.assertRaisesRegex(ValueError, "Unsupported reader family"):
                build_reader(
                    "third_reader",
                    base_urls=["http://127.0.0.1:8080/v1"],
                    output_dir=output_dir / "third",
                )

    def test_generic_reader_rejects_reserved_extra_body_and_non_http_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with self.assertRaisesRegex(ValueError, "reserved keys"):
                OpenAICompatibleReader(
                    reader_family=QWEN_READER,
                    base_urls=["http://127.0.0.1:8080/v1"],
                    model_id="model",
                    cache_path=output_dir / "cache.jsonl",
                    request_log_path=output_dir / "requests.jsonl",
                    extra_body={"model": "override"},
                )
            with self.assertRaisesRegex(ValueError, "must be HTTP"):
                Qwen35Reader(
                    base_urls=["file:///tmp/socket"],
                    output_dir=output_dir,
                )

    def test_reasoning_tags_are_removed(self) -> None:
        self.assertEqual(clean_llm_content("<think>hidden</think>\nPlus"), "Plus")


if __name__ == "__main__":
    unittest.main()
