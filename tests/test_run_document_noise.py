from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import rae_egta.rag_post_event.run_document as run_document
from rae_egta.rag_post_event.data import REVISE, TARGET, KnowledgeEvent, Query
from rae_egta.rag_post_event.document_backend import (
    DOC_CURRENT,
    DOC_DISTRACTOR,
    DOC_OLD,
    INTENT_CURRENT_DEFAULT,
    INTENT_CURRENT_EXPLICIT,
    INTENT_HISTORICAL,
    POLICY_OLDREF_ADAPTED,
    POLICY_POSTEVENT_VALIDITY,
    STRESS_LEXICAL_SCHEMA,
    Document,
    DocumentIndex,
    build_document_corpus,
    build_indexes,
    expanded_prompt_for_event,
)
from rae_egta.rag_post_event.noise import (
    INTENT_ERROR,
    METADATA_MISSING,
    NoiseConfig,
    build_noise_view,
)
from rae_egta.rag_post_event.run_document import (
    CSV_FIELDS,
    answer_from_version_reranked_candidates_with_intent,
    generate_rows,
    row_for_query,
    write_csv,
)


ROOT = Path(__file__).resolve().parents[1]
LEGACY_ARTIFACTS = (
    "rag_postevent_metrics.csv",
    "rag_postevent_metrics_partial.csv",
    "events.jsonl",
    "queries.jsonl",
    "documents.jsonl",
    "chunks.jsonl",
    "run_manifest.json",
)


def runner_kwargs() -> dict[str, object]:
    return {
        "run_id": "noise_unit",
        "event_count": 8,
        "seeds": [13],
        "methods": ["postevent_validity_filter", "intent_aware_postevent_filter"],
        "stable_queries_per_event": 1,
        "add_fraction": 0.10,
        "revise_fraction": 0.50,
        "retire_fraction": 0.40,
        "distractors_per_event": 1,
        "chunk_noise_paragraphs": 0,
        "retriever": "bm25",
        "top_k": 5,
        "rerank_candidate_k": 20,
        "stress_axis": STRESS_LEXICAL_SCHEMA,
    }


class RunDocumentNoiseTest(unittest.TestCase):
    def test_zero_percent_condition_is_byte_equivalent_to_clean_runner(self) -> None:
        kwargs = runner_kwargs()
        clean = generate_rows(**kwargs)
        zero_noise = generate_rows(
            **kwargs,
            noise_factor=METADATA_MISSING,
            noise_rate=0.0,
        )
        self.assertEqual(clean, zero_noise)

        with tempfile.TemporaryDirectory() as tmp:
            clean_path = Path(tmp) / "clean.csv"
            zero_path = Path(tmp) / "zero.csv"
            write_csv(clean_path, clean[0], CSV_FIELDS)
            write_csv(zero_path, zero_noise[0], CSV_FIELDS)
            self.assertEqual(clean_path.read_bytes(), zero_path.read_bytes())

    def test_clean_cli_artifacts_match_pre_refactor_legacy_fields_byte_for_byte(self) -> None:
        """Gate N-002 against the independent module materialized from HEAD."""

        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            runner_path = temp_root / "run_document.py"
            output_dir = temp_root / "output"
            baseline_source = subprocess.check_output(
                ["git", "show", "HEAD:src/rae_egta/rag_post_event/run_document.py"],
                cwd=ROOT,
            )
            runner_path.write_bytes(baseline_source)
            command = [
                sys.executable,
                str(runner_path),
                "--output-dir",
                str(output_dir),
                "--run-id",
                "n002_golden",
                "--events",
                "8",
                "--seeds",
                "13",
                "--methods",
                "no_update",
                "store_only",
                "postevent_validity_filter",
                "intent_aware_postevent_filter",
                "--stable-queries-per-event",
                "1",
                "--distractors-per-event",
                "1",
                "--chunk-noise-paragraphs",
                "0",
                "--retriever",
                "bm25",
                "--top-k",
                "5",
                "--rerank-candidate-k",
                "20",
                "--stress-axis",
                "lexical_schema",
                "--partial-write-every-queries",
                "2",
                "--device",
                "cpu",
            ]
            environment = os.environ.copy()
            environment.update(
                {
                    "ALLOW_LOCAL_RAG_DOCUMENT_RUN": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": str(ROOT / "src"),
                }
            )
            subprocess.run(command, cwd=ROOT, env=environment, check=True, capture_output=True)
            baseline = {name: (output_dir / name).read_bytes() for name in LEGACY_ARTIFACTS}

            shutil.rmtree(output_dir)
            runner_path.write_bytes(Path(run_document.__file__).read_bytes())
            subprocess.run(command, cwd=ROOT, env=environment, check=True, capture_output=True)

            for name in ("rag_postevent_metrics.csv", "rag_postevent_metrics_partial.csv"):
                with io.StringIO(baseline[name].decode("utf-8"), newline="") as handle:
                    baseline_reader = csv.DictReader(handle)
                    legacy_fields = list(baseline_reader.fieldnames or [])
                    baseline_rows = list(baseline_reader)
                with (output_dir / name).open(newline="", encoding="utf-8") as handle:
                    new_rows = list(csv.DictReader(handle))
                self.assertEqual(len(baseline_rows), len(new_rows), name)
                projected = io.StringIO(newline="")
                writer = csv.DictWriter(projected, fieldnames=legacy_fields)
                writer.writeheader()
                writer.writerows({field: row[field] for field in legacy_fields} for row in new_rows)
                self.assertEqual(baseline[name], projected.getvalue().encode("utf-8"), name)

            for name in ("events.jsonl", "queries.jsonl", "documents.jsonl", "chunks.jsonl"):
                baseline_rows = [json.loads(line) for line in baseline[name].splitlines()]
                new_rows = [
                    json.loads(line)
                    for line in (output_dir / name).read_text(encoding="utf-8").splitlines()
                ]
                self.assertEqual(len(baseline_rows), len(new_rows), name)
                projected_bytes = b"".join(
                    (
                        json.dumps(
                            {field: new[field] for field in old},
                            sort_keys=True,
                        )
                        + "\n"
                    ).encode("utf-8")
                    for old, new in zip(baseline_rows, new_rows, strict=True)
                )
                self.assertEqual(baseline[name], projected_bytes, name)

            baseline_manifest = json.loads(baseline["run_manifest.json"])
            new_manifest = json.loads((output_dir / "run_manifest.json").read_text(encoding="utf-8"))

            def project_legacy(new: object, old: object) -> object:
                if isinstance(old, dict):
                    self.assertIsInstance(new, dict)
                    return {key: project_legacy(new[key], value) for key, value in old.items()}
                if isinstance(old, list):
                    self.assertIsInstance(new, list)
                    self.assertGreaterEqual(len(new), len(old))
                    return [project_legacy(value, template) for value, template in zip(new, old)]
                return new

            projected_manifest = project_legacy(new_manifest, baseline_manifest)
            projected_manifest_bytes = json.dumps(
                projected_manifest,
                indent=2,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
            self.assertEqual(baseline["run_manifest.json"], projected_manifest_bytes)
            self.assertTrue((output_dir / "noise_assignment.json").is_file())
            self.assertEqual("clean", new_manifest["noise_assignment"]["factor"])
            self.assertEqual(CSV_FIELDS, new_manifest["schema_fields"])

    def test_clean_intent_is_resolved_once_per_query(self) -> None:
        kwargs = runner_kwargs()
        original = run_document.infer_query_intent
        with patch.object(run_document, "infer_query_intent", wraps=original) as resolver:
            _, _, query_records, _, _ = generate_rows(
                **kwargs,
                noise_factor=INTENT_ERROR,
                noise_rate=0.20,
            )

        self.assertTrue(query_records)
        self.assertEqual(resolver.call_count, len(query_records))

    def test_corrupted_intent_reaches_prompt_and_rerank_bonus(self) -> None:
        corpus = build_document_corpus(
            event_count=12,
            seed=13,
            stable_queries_per_event=1,
            add_fraction=0.10,
            revise_fraction=0.50,
            retire_fraction=0.40,
            distractors_per_event=1,
            chunk_noise_paragraphs=0,
            stress_axis=STRESS_LEXICAL_SCHEMA,
        )
        event = next(item for item in corpus.events if item.event_type == REVISE)
        query = next(
            item
            for item in corpus.queries
            if item.event_id == event.event_id and item.query_type == TARGET
        )
        old_index, postevent_index = build_indexes(corpus, retriever="bm25")
        combined_index = DocumentIndex(corpus.documents, retriever="bm25")
        assignment = build_noise_view(
            {13: corpus},
            {},
            NoiseConfig(),
        ).assignment

        original_expander = run_document.expanded_prompt_for_event
        original_bonus = run_document._rerank_bonus
        with (
            patch.object(run_document, "expanded_prompt_for_event", wraps=original_expander) as expander,
            patch.object(run_document, "_rerank_bonus", wraps=original_bonus) as bonus,
        ):
            row = row_for_query(
                run_id="intent_propagation",
                seed=13,
                event=event,
                query=query,
                method="intent_aware_postevent_filter",
                old_index=old_index,
                postevent_index=postevent_index,
                combined_index=combined_index,
                top_k=5,
                rerank_candidate_k=20,
                stress_axis=STRESS_LEXICAL_SCHEMA,
                clean_intent=INTENT_CURRENT_EXPLICIT,
                effective_intent=INTENT_HISTORICAL,
                noise_assignment=assignment,
                evaluation_document_labels={
                    document.doc_id: (document.doc_role, document.version)
                    for document in corpus.documents
                },
            )

        self.assertEqual(row["clean_intent"], INTENT_CURRENT_EXPLICIT)
        self.assertEqual(row["effective_intent"], INTENT_HISTORICAL)
        self.assertEqual(row["intent_corrupted"], 1)
        self.assertEqual(
            row["retrieval_prompt"],
            expanded_prompt_for_event(
                query.prompt,
                event,
                stress_axis=STRESS_LEXICAL_SCHEMA,
                intent=INTENT_HISTORICAL,
            ),
        )
        self.assertTrue(expander.call_args_list)
        self.assertTrue(
            all(call.kwargs["intent"] == INTENT_HISTORICAL for call in expander.call_args_list)
        )
        self.assertTrue(bonus.call_args_list)
        self.assertTrue(
            all(call.kwargs["intent"] == INTENT_HISTORICAL for call in bonus.call_args_list)
        )

    @staticmethod
    def _noise_direction_fixture() -> tuple[KnowledgeEvent, Query]:
        event = KnowledgeEvent(
            event_id=7,
            event_type=REVISE,
            subject="subject",
            attribute="status",
            old_answer="OLD_ANSWER",
            new_answer="NEW_ANSWER",
            stable_facts=(),
        )
        query = Query(
            event_id=7,
            query_id="q7",
            query_type=TARGET,
            prompt="What is the current status of subject?",
            expected_old="OLD_ANSWER",
            expected_postevent="NEW_ANSWER",
            expected_current="NEW_ANSWER",
            obsolete_answer="OLD_ANSWER",
        )
        return event, query

    def test_old_to_current_metadata_changes_policy_but_uses_clean_obsolete_label(self) -> None:
        event, query = self._noise_direction_fixture()
        old = Document(
            "z_old",
            event.event_id,
            DOC_OLD,
            "old",
            "Status",
            "Canonical answer: OLD_ANSWER.",
            "OLD_ANSWER",
            "current",
        )
        distractor = Document(
            "a_distractor",
            event.event_id,
            DOC_DISTRACTOR,
            "distractor",
            "Status",
            "Canonical answer: STABLE_ANSWER.",
            "STABLE_ANSWER",
            "current",
        )
        labels = {
            old.doc_id: (old.doc_role, old.version),
            distractor.doc_id: (distractor.doc_role, distractor.version),
        }
        clean = answer_from_version_reranked_candidates_with_intent(
            DocumentIndex((old, distractor)),
            query,
            event,
            policy=POLICY_POSTEVENT_VALIDITY,
            stress_axis="none",
            effective_intent=INTENT_CURRENT_EXPLICIT,
            evaluation_document_labels=labels,
        )
        corrupted_old = replace(old, doc_role=DOC_CURRENT, version="postevent")
        corrupted = answer_from_version_reranked_candidates_with_intent(
            DocumentIndex((corrupted_old, distractor)),
            query,
            event,
            policy=POLICY_POSTEVENT_VALIDITY,
            stress_axis="none",
            effective_intent=INTENT_CURRENT_EXPLICIT,
            evaluation_document_labels=labels,
        )
        self.assertNotEqual(clean.answer, corrupted.answer)
        self.assertEqual("OLD_ANSWER", corrupted.answer)
        self.assertEqual(1, corrupted.obsolete_evidence_hit)

    def test_current_to_old_metadata_changes_policy_but_uses_clean_current_label(self) -> None:
        event, query = self._noise_direction_fixture()
        current = Document(
            "z_current",
            event.event_id,
            DOC_CURRENT,
            "postevent",
            "Status",
            "Canonical answer: NEW_ANSWER.",
            "NEW_ANSWER",
            "current",
        )
        distractor = Document(
            "a_distractor",
            event.event_id,
            DOC_DISTRACTOR,
            "distractor",
            "Status",
            "Canonical answer: STABLE_ANSWER.",
            "STABLE_ANSWER",
            "current",
        )
        labels = {
            current.doc_id: (current.doc_role, current.version),
            distractor.doc_id: (distractor.doc_role, distractor.version),
        }
        clean = answer_from_version_reranked_candidates_with_intent(
            DocumentIndex((current, distractor)),
            query,
            event,
            policy=POLICY_OLDREF_ADAPTED,
            stress_axis="none",
            effective_intent=INTENT_CURRENT_DEFAULT,
            evaluation_document_labels=labels,
        )
        corrupted_current = replace(current, doc_role=DOC_OLD, version="old")
        corrupted = answer_from_version_reranked_candidates_with_intent(
            DocumentIndex((corrupted_current, distractor)),
            query,
            event,
            policy=POLICY_OLDREF_ADAPTED,
            stress_axis="none",
            effective_intent=INTENT_CURRENT_DEFAULT,
            evaluation_document_labels=labels,
        )
        self.assertNotEqual(clean.answer, corrupted.answer)
        self.assertEqual("NEW_ANSWER", corrupted.answer)
        self.assertEqual(0, corrupted.obsolete_evidence_hit)


if __name__ == "__main__":
    unittest.main()
