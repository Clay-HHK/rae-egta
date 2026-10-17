from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from rae_egta.rag_post_event.data import QUERY_TEMPLATE_VERSION
from rae_egta.rag_post_event.document_backend import (
    STRESS_LEXICAL_SCHEMA,
    RetrievedChunk,
    build_document_corpus,
)
from rae_egta.rag_post_event.llm_readers import PROMPT_POLICY_VERSION, ReaderResponse
from rae_egta.rag_post_event.llm_scoring import ABSTAIN
from rae_egta.rag_post_event.run_document_llm import (
    CSV_FIELDS,
    DETERMINISTIC_EXTRACTIVE_READER,
    EXPLORATORY_NOTE,
    FORMAL_RETRIEVER_MATRIX,
    GROK_READER,
    MINIMAX_READER,
    PILOT_METHODS,
    PILOT_GATE_PROTOCOL_VERSION,
    PILOT_READER_CELL_COUNT,
    QWEN_READER,
    REGISTERED_METHODS,
    RUNNER_PROTOCOL_VERSION,
    DocumentLLMRunner,
    RunnerConfig,
    build_pilot_gate_summary,
    enforce_t4_only,
    parse_args,
    resolve_formal_provenance,
    selection_for_config,
    validate_formal_matrix,
    validate_pilot_gate_manifest,
    write_pilot_gate_artifacts,
    write_run_outputs,
)
from rae_egta.rag_post_event.subsample import select_subsample


class ScriptedFakeReader:
    def __init__(self, reader_family: str) -> None:
        self.reader_family = reader_family
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def answer(
        self,
        *,
        query: str,
        retrieved: tuple[RetrievedChunk, ...],
    ) -> ReaderResponse:
        chunk_ids = tuple(item.chunk.chunk_id for item in retrieved)
        self.calls.append((query, chunk_ids))
        supported = next((item.chunk.answer for item in retrieved if item.chunk.answer), "UNKNOWN")
        scripted = (
            f"The documents support {supported}.",
            supported,
            "I cannot answer from the provided documents.",
            f"A cautious answer is {supported}.",
        )[(len(self.calls) - 1) % 4]
        prompt_hash = hashlib.sha256(
            json.dumps([self.reader_family, query, chunk_ids], sort_keys=True).encode("utf-8")
        ).hexdigest()
        return ReaderResponse(
            answer=scripted,
            reader_family=self.reader_family,
            model_requested=f"{self.reader_family}_model",
            model_returned=f"{self.reader_family}_model",
            prompt_sha256=prompt_hash,
            cache_hit=False,
            latency_seconds=0.0,
            base_url="fake://no-network",
            prompt_policy_version=PROMPT_POLICY_VERSION,
        )

    def manifest(self) -> dict[str, object]:
        return {
            "reader_family": self.reader_family,
            "model_id": f"{self.reader_family}_model",
            "temperature": 0.0,
            "transport": "fake",
        }


class CellDeterministicFakeReader:
    """No-network reader whose response is a pure function of one cell."""

    reader_family = "fake_cell_deterministic"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def answer(
        self,
        *,
        query: str,
        retrieved: tuple[RetrievedChunk, ...],
    ) -> ReaderResponse:
        chunk_ids = tuple(item.chunk.chunk_id for item in retrieved)
        self.calls.append((query, chunk_ids))
        supported = next((item.chunk.answer for item in retrieved if item.chunk.answer), "UNKNOWN")
        prompt_hash = hashlib.sha256(
            json.dumps([self.reader_family, query, chunk_ids], sort_keys=True).encode("utf-8")
        ).hexdigest()
        return ReaderResponse(
            answer=supported,
            reader_family=self.reader_family,
            model_requested="fake_cell_model",
            model_returned="fake_cell_model",
            prompt_sha256=prompt_hash,
            cache_hit=False,
            latency_seconds=0.0,
            base_url="fake://no-network",
            prompt_policy_version=PROMPT_POLICY_VERSION,
        )

    def manifest(self) -> dict[str, object]:
        return {
            "reader_family": self.reader_family,
            "model_id": "fake_cell_model",
            "temperature": 0.0,
            "transport": "fake",
        }


def tiny_fixture() -> tuple[dict[int, object], object, RunnerConfig]:
    corpus = build_document_corpus(
        event_count=12,
        seed=13,
        stable_queries_per_event=2,
        add_fraction=0.10,
        revise_fraction=0.50,
        retire_fraction=0.40,
        distractors_per_event=1,
        chunk_noise_paragraphs=0,
        stress_axis=STRESS_LEXICAL_SCHEMA,
    )
    selection = select_subsample(
        {13: corpus.events},
        {13: corpus.queries},
        event_counts={"ADD": 1, "REVISE": 2, "RETIRE": 1},
        mode="test",
    )
    config = RunnerConfig(
        run_id="unit_controlled_llm",
        mode="test",
        seeds=(13,),
        methods=("no_update", "store_only"),
        retrievers=("bm25",),
        source_events_per_seed=12,
        source_stable_queries_per_event=2,
        distractors_per_event=1,
        top_k=3,
        rerank_candidate_k=5,
        stress_axis=STRESS_LEXICAL_SCHEMA,
        device="cpu",
    )
    return {13: corpus}, selection, config


class RunDocumentLLMTests(unittest.TestCase):
    def test_holdout_offset_cli_is_explicit(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "run_document_llm.py",
                "--output-dir",
                "unused",
                "--reader",
                QWEN_READER,
                "--mode",
                "pilot",
                "--holdout-offset",
                "2",
            ],
        ):
            args = parse_args()

        self.assertEqual(2, args.holdout_offset)
        self.assertEqual(1, args.reader_concurrency)

    def test_reader_concurrency_cli_is_explicit(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "run_document_llm.py",
                "--output-dir",
                "unused",
                "--reader",
                QWEN_READER,
                "--reader-concurrency",
                "4",
            ],
        ):
            args = parse_args()

        self.assertEqual(4, args.reader_concurrency)

    def test_reader_concurrency_cli_rejects_non_positive_values(self) -> None:
        with (
            patch.object(
                sys,
                "argv",
                [
                    "run_document_llm.py",
                    "--output-dir",
                    "unused",
                    "--reader",
                    QWEN_READER,
                    "--reader-concurrency",
                    "0",
                ],
            ),
            self.assertRaises(SystemExit),
        ):
            parse_args()

    def test_exploratory_cli_flag_is_explicit(self) -> None:
        with patch.object(
            sys,
            "argv",
            [
                "run_document_llm.py",
                "--output-dir",
                "unused",
                "--reader",
                QWEN_READER,
                "--mode",
                "formal",
                "--exploratory",
            ],
        ):
            args = parse_args()

        self.assertTrue(args.exploratory)

    def test_pilot_exact_method_set_and_cardinality_are_frozen(self) -> None:
        self.assertEqual(
            (
                "no_update",
                "store_only",
                "query_rewrite_update",
                "postevent_validity_filter",
            ),
            PILOT_METHODS,
        )
        config = RunnerConfig(
            run_id="pilot_contract",
            mode="pilot",
            seeds=(13,),
            methods=PILOT_METHODS,
            retrievers=("bm25",),
            source_events_per_seed=80,
            source_stable_queries_per_event=2,
            distractors_per_event=1,
            holdout_offset=1,
            device="cpu",
        )
        corpora = {
            13: build_document_corpus(
                event_count=80,
                seed=13,
                stable_queries_per_event=2,
                add_fraction=0.10,
                revise_fraction=0.50,
                retire_fraction=0.40,
                distractors_per_event=1,
                chunk_noise_paragraphs=0,
                stress_axis=STRESS_LEXICAL_SCHEMA,
            )
        }
        selection = selection_for_config(corpora, config)
        self.assertEqual(1, selection.holdout_offset)
        self.assertEqual(1, selection.to_manifest()["holdout_offset"])
        self.assertEqual(12, len(selection.to_manifest()["selected_events"]))
        self.assertEqual(58, len(selection.query_keys))
        self.assertEqual(232, len(selection.query_keys) * len(PILOT_METHODS))
        self.assertEqual(PILOT_READER_CELL_COUNT, 232)

    @staticmethod
    def _passing_pilot_rows() -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for method in PILOT_METHODS:
            for query_index in range(58):
                query_type = "target" if query_index < 12 else (
                    "obsolete_unqualified" if query_index < 23 else "current_valid_stable"
                )
                event_type = "REVISE" if query_index % 2 == 0 else "RETIRE"
                correct = method == "query_rewrite_update" or (
                    method == "store_only" and query_type != "target"
                )
                rows.append(
                    {
                        "method": method,
                        "query_type": query_type,
                        "event_type": event_type,
                        "query_template_version": QUERY_TEMPLATE_VERSION,
                        "holdout_offset": 1,
                        "subsample_hash": "unit_cl2_subsample",
                        "scoring_class": "CORRECT_SIDE_CONTAINMENT",
                        "correct_canonical_contained": int(correct),
                        "conflicting_canonical_contained": int(
                            method == "no_update" and query_type == "obsolete_unqualified"
                        ),
                    }
                )
        return rows

    def test_pilot_gate_artifacts_bind_all_four_values_and_detect_tampering(self) -> None:
        rows = self._passing_pilot_rows()
        summary = build_pilot_gate_summary(rows)
        self.assertEqual("PASS", summary["status"])
        self.assertEqual(
            "controlled_document_llm_pilot_gate_v3",
            PILOT_GATE_PROTOCOL_VERSION,
        )
        self.assertEqual(QUERY_TEMPLATE_VERSION, summary["query_template_version"])
        self.assertEqual(1, summary["holdout_offset"])
        self.assertEqual("unit_cl2_subsample", summary["subsample_hash"])
        self.assertEqual(
            {
                "parse_success_rate": {"operator": ">=", "threshold": 0.95},
                "query_rewrite_target_success": {"operator": ">=", "threshold": 0.70},
                "store_only_shifted_target_success": {"operator": "<=", "threshold": 0.30},
                "no_update_obsolete_rate": {"operator": ">=", "threshold": 0.70},
            },
            summary["gate_thresholds"],
        )
        self.assertEqual(
            {
                "parse_success_rate",
                "query_rewrite_target_success",
                "store_only_shifted_target_success",
                "no_update_obsolete_rate",
            },
            set(summary["gate_values"]),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            provenance = write_pilot_gate_artifacts(output_dir, rows)
            validated = validate_pilot_gate_manifest(output_dir / "pilot_gate_manifest.json")
            self.assertEqual("PASS", validated["status"])
            self.assertEqual(provenance["manifest_sha256"], validated["manifest_sha256"])
            self.assertEqual(QUERY_TEMPLATE_VERSION, validated["query_template_version"])
            self.assertEqual(1, validated["holdout_offset"])
            self.assertEqual("unit_cl2_subsample", validated["subsample_hash"])
            with (output_dir / "pilot_gate_summary.json").open("a", encoding="utf-8") as handle:
                handle.write(" ")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                validate_pilot_gate_manifest(output_dir / "pilot_gate_manifest.json")

    def test_formal_matrix_requires_all_six_methods_and_frozen_retrievers(self) -> None:
        for reader, retrievers in FORMAL_RETRIEVER_MATRIX.items():
            validate_formal_matrix(reader, REGISTERED_METHODS, retrievers)
        with self.assertRaisesRegex(ValueError, "exact frozen matrix"):
            validate_formal_matrix(QWEN_READER, ("no_update",), ("bm25",))
        with self.assertRaisesRegex(ValueError, "exact frozen matrix"):
            validate_formal_matrix(GROK_READER, REGISTERED_METHODS, ("bm25", "dense"))
        self.assertEqual(("bm25",), FORMAL_RETRIEVER_MATRIX[GROK_READER])
        self.assertEqual(("bm25",), FORMAL_RETRIEVER_MATRIX[MINIMAX_READER])
        self.assertEqual(("bm25", "dense"), FORMAL_RETRIEVER_MATRIX[DETERMINISTIC_EXTRACTIVE_READER])

    def test_exploratory_formal_still_enforces_frozen_reader_matrix(self) -> None:
        self.assertEqual(
            {},
            resolve_formal_provenance(
                reader=QWEN_READER,
                methods=REGISTERED_METHODS,
                retrievers=("bm25", "dense"),
                pilot_gate_manifest=None,
                exploratory=True,
            ),
        )
        with self.assertRaisesRegex(ValueError, "exact frozen matrix"):
            resolve_formal_provenance(
                reader=QWEN_READER,
                methods=REGISTERED_METHODS,
                retrievers=("bm25",),
                pilot_gate_manifest=None,
                exploratory=True,
            )

    def test_exploratory_accepts_failed_or_missing_pilot_but_default_refuses(self) -> None:
        failed_rows = self._passing_pilot_rows()
        for row in failed_rows:
            if row["method"] == "query_rewrite_update" and row["query_type"] == "target":
                row["correct_canonical_contained"] = 0

        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            provenance = write_pilot_gate_artifacts(output_dir, failed_rows)
            gate_manifest = output_dir / "pilot_gate_manifest.json"
            self.assertEqual("FAIL", provenance["status"])
            exploratory_provenance = resolve_formal_provenance(
                reader=QWEN_READER,
                methods=REGISTERED_METHODS,
                retrievers=("bm25", "dense"),
                pilot_gate_manifest=gate_manifest,
                exploratory=True,
            )
            self.assertEqual("FAIL", exploratory_provenance["status"])
            with self.assertRaisesRegex(ValueError, "requires pilot gate status PASS"):
                resolve_formal_provenance(
                    reader=QWEN_READER,
                    methods=REGISTERED_METHODS,
                    retrievers=("bm25", "dense"),
                    pilot_gate_manifest=gate_manifest,
                    exploratory=False,
                )

        self.assertEqual(
            {},
            resolve_formal_provenance(
                reader=QWEN_READER,
                methods=REGISTERED_METHODS,
                retrievers=("bm25", "dense"),
                pilot_gate_manifest=None,
                exploratory=True,
            ),
        )
        with self.assertRaisesRegex(ValueError, "requires --pilot-gate-manifest"):
            resolve_formal_provenance(
                reader=QWEN_READER,
                methods=REGISTERED_METHODS,
                retrievers=("bm25", "dense"),
                pilot_gate_manifest=None,
                exploratory=False,
            )

    def test_fake_reader_end_to_end_schema_pairing_and_no_network(self) -> None:
        corpora, selection, config = tiny_fixture()
        reader = ScriptedFakeReader("fake_a")
        runner = DocumentLLMRunner(config=config, reader=reader)
        with patch("socket.socket", side_effect=AssertionError("network access forbidden")):
            rows = runner.run_rows(corpora=corpora, selection=selection)

        self.assertEqual(36, len(rows))
        self.assertEqual(36, len(reader.calls))
        self.assertTrue(all(list(row) == CSV_FIELDS for row in rows))
        self.assertTrue(all(row["subsample_hash"] == selection.subsample_hash for row in rows))
        self.assertTrue(
            all(row["query_template_version"] == QUERY_TEMPLATE_VERSION for row in rows)
        )
        self.assertTrue(all(row["holdout_offset"] == 0 for row in rows))
        self.assertTrue(all(row["reader_id"] == "fake_a" for row in rows))
        self.assertTrue(all(row["reader_base_url"] == "fake://no-network" for row in rows))
        self.assertTrue(
            all(row["reader_prompt_policy_version"] == PROMPT_POLICY_VERSION for row in rows)
        )
        self.assertTrue(
            all(row["effective_reader_query"] == row["clean_query_prompt"] for row in rows)
        )
        reader_visible_queries = {
            query.reader_prompt
            for item in selection.selected_events
            for query in item.queries
        }
        self.assertTrue(all(query in reader_visible_queries for query, _ in reader.calls))
        self.assertTrue(all("current current" not in query.casefold() for query, _ in reader.calls))

        query_keys_by_method: dict[str, set[tuple[object, ...]]] = {}
        for method in config.methods:
            query_keys_by_method[method] = {
                (row["seed"], row["event_id"], row["query_id"], row["retriever"])
                for row in rows
                if row["method"] == method
            }
        self.assertEqual(query_keys_by_method["no_update"], query_keys_by_method["store_only"])

    def test_reader_concurrency_one_preserves_sequential_rows_exactly(self) -> None:
        corpora, selection, config = tiny_fixture()
        with patch("socket.socket", side_effect=AssertionError("network access forbidden")):
            default_rows = DocumentLLMRunner(
                config=config,
                reader=ScriptedFakeReader("fake_sequential"),
            ).run_rows(corpora=corpora, selection=selection)
            explicit_rows = DocumentLLMRunner(
                config=config,
                reader=ScriptedFakeReader("fake_sequential"),
                reader_concurrency=1,
            ).run_rows(corpora=corpora, selection=selection)

        self.assertEqual(default_rows, explicit_rows)

    def test_reader_concurrency_four_matches_one_in_canonical_order(self) -> None:
        corpora, selection, config = tiny_fixture()
        sequential = DocumentLLMRunner(
            config=config,
            reader=CellDeterministicFakeReader(),
            reader_concurrency=1,
        )
        concurrent = DocumentLLMRunner(
            config=config,
            reader=CellDeterministicFakeReader(),
            reader_concurrency=4,
        )
        with patch("socket.socket", side_effect=AssertionError("network access forbidden")):
            sequential_rows = sequential.run_rows(corpora=corpora, selection=selection)
            concurrent_rows = concurrent.run_rows(corpora=corpora, selection=selection)

        self.assertEqual(sequential_rows, concurrent_rows)
        self.assertEqual(sequential.config_hash(selection), concurrent.config_hash(selection))
        self.assertEqual(
            [
                (row["seed"], row["event_id"], row["query_id"], row["retriever"], row["method"])
                for row in sequential_rows
            ],
            [
                (row["seed"], row["event_id"], row["query_id"], row["retriever"], row["method"])
                for row in concurrent_rows
            ],
        )

    def test_same_selection_is_shared_across_reader_arms(self) -> None:
        corpora, selection, config = tiny_fixture()
        rows_a = DocumentLLMRunner(
            config=config,
            reader=ScriptedFakeReader("fake_a"),
        ).run_rows(corpora=corpora, selection=selection)
        rows_b = DocumentLLMRunner(
            config=config,
            reader=ScriptedFakeReader("fake_b"),
        ).run_rows(corpora=corpora, selection=selection)
        paired_keys = lambda rows: {
            (row["seed"], row["event_id"], row["query_id"], row["method"], row["retriever"])
            for row in rows
        }
        self.assertEqual(paired_keys(rows_a), paired_keys(rows_b))
        self.assertEqual(
            {selection.subsample_hash},
            {str(row["subsample_hash"]) for row in [*rows_a, *rows_b]},
        )
        self.assertNotEqual(rows_a[0]["config_hash"], rows_b[0]["config_hash"])

    def test_parsed_abstention_passes_format_gate_but_fails_task_gates(self) -> None:
        rows = self._passing_pilot_rows()
        for row in rows:
            row["scoring_class"] = ABSTAIN
            row["correct_canonical_contained"] = 0
            row["conflicting_canonical_contained"] = 0

        summary = build_pilot_gate_summary(rows)

        self.assertEqual(1.0, summary["gate_values"]["parse_success_rate"])
        self.assertTrue(summary["gate_pass"]["parse_success_rate"])
        self.assertEqual(1.0, summary["diagnostic_values"]["abstention_rate"])
        self.assertEqual(0.0, summary["gate_values"]["query_rewrite_target_success"])
        self.assertEqual(0.0, summary["gate_values"]["no_update_obsolete_rate"])
        self.assertFalse(summary["gate_pass"]["query_rewrite_target_success"])
        self.assertFalse(summary["gate_pass"]["no_update_obsolete_rate"])
        self.assertEqual("FAIL", summary["status"])

    def test_streamed_csv_and_manifest_have_required_provenance(self) -> None:
        corpora, selection, config = tiny_fixture()
        reader = CellDeterministicFakeReader()
        runner = DocumentLLMRunner(config=config, reader=reader, reader_concurrency=4)
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "run"
            with (
                patch(
                    "rae_egta.rag_post_event.run_document_llm.git_metadata",
                    return_value={"git_sha": "unit", "git_dirty": False},
                ),
                patch(
                    "rae_egta.rag_post_event.run_document_llm.environment_metadata",
                    return_value={"device": "cpu", "network": "unused"},
                ),
                patch("socket.socket", side_effect=AssertionError("network access forbidden")),
            ):
                rows, manifest = write_run_outputs(
                    output_dir=output_dir,
                    runner=runner,
                    corpora=corpora,
                    selection=selection,
                )

            with (output_dir / "rag_document_llm_metrics.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                csv_reader = csv.DictReader(handle)
                written_rows = list(csv_reader)
                self.assertEqual(CSV_FIELDS, csv_reader.fieldnames)
            disk_manifest = json.loads((output_dir / "run_manifest.json").read_text(encoding="utf-8"))
            disk_subsample = json.loads((output_dir / "subsample.json").read_text(encoding="utf-8"))

            self.assertEqual(len(rows), len(written_rows))
            self.assertEqual(manifest, disk_manifest)
            self.assertEqual(selection.subsample_hash, disk_manifest["subsample_hash"])
            self.assertEqual(selection.subsample_hash, disk_subsample["subsample_hash"])
            self.assertEqual(RUNNER_PROTOCOL_VERSION, disk_manifest["protocol_version"])
            self.assertEqual(QUERY_TEMPLATE_VERSION, disk_manifest["query_template_version"])
            self.assertEqual(QUERY_TEMPLATE_VERSION, disk_subsample["query_template_version"])
            self.assertEqual(0, disk_manifest["holdout_offset"])
            self.assertEqual(0, disk_subsample["holdout_offset"])
            self.assertEqual(rows[0]["config_hash"], disk_manifest["config_hash"])
            self.assertEqual(4, disk_manifest["reader_concurrency"])
            self.assertEqual(CSV_FIELDS, disk_manifest["schema_fields"])
            self.assertFalse(disk_manifest["git"]["git_dirty"])
            self.assertEqual(
                (output_dir / "rag_document_llm_metrics_partial.csv").read_bytes(),
                (output_dir / "rag_document_llm_metrics.csv").read_bytes(),
            )

    def test_exploratory_outputs_stamp_manifest_rows_and_subsample(self) -> None:
        corpora, selection, base_config = tiny_fixture()
        config = replace(base_config, mode="formal", exploratory=True)
        runner = DocumentLLMRunner(config=config, reader=ScriptedFakeReader("fake_exploratory"))
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "run"
            with (
                patch(
                    "rae_egta.rag_post_event.run_document_llm.git_metadata",
                    return_value={"git_sha": "unit", "git_dirty": False},
                ),
                patch(
                    "rae_egta.rag_post_event.run_document_llm.environment_metadata",
                    return_value={"device": "cpu", "network": "unused"},
                ),
                patch("socket.socket", side_effect=AssertionError("network access forbidden")),
            ):
                rows, manifest = write_run_outputs(
                    output_dir=output_dir,
                    runner=runner,
                    corpora=corpora,
                    selection=selection,
                )

            with (output_dir / "rag_document_llm_metrics.csv").open(
                newline="", encoding="utf-8"
            ) as handle:
                written_rows = list(csv.DictReader(handle))
            subsample = json.loads((output_dir / "subsample.json").read_text(encoding="utf-8"))

            self.assertTrue(manifest["exploratory"])
            self.assertFalse(manifest["preregistered_closure"])
            self.assertEqual(EXPLORATORY_NOTE, manifest["note"])
            self.assertTrue(all(row["exploratory"] is True for row in rows))
            self.assertTrue(all(row["exploratory"] == "True" for row in written_rows))
            self.assertTrue(subsample["exploratory"])
            self.assertFalse(subsample["preregistered_closure"])
            self.assertEqual(EXPLORATORY_NOTE, subsample["note"])

    def test_macos_guard_requires_explicit_override(self) -> None:
        with patch("platform.system", return_value="Darwin"):
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(SystemExit, "Refusing to run"):
                    enforce_t4_only()
            with patch.dict(os.environ, {"ALLOW_LOCAL_RAG_DOCUMENT_LLM_RUN": "1"}, clear=True):
                enforce_t4_only()


if __name__ == "__main__":
    unittest.main()
