from __future__ import annotations

import argparse
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from rae_egta.rag_post_event.llm_cluster_inference import (
    ABSTAIN,
    CORRECT,
    MIXED,
    REFUSAL,
    WRONG,
    LLMInferenceError,
    build_bootstrap_weights,
    cell_metric_rows,
    contrast_rows,
    load_input,
    run_analysis,
)


METHODS = (
    "no_update",
    "store_only",
    "query_rewrite_update",
    "postevent_validity_filter",
)
FIELDS = (
    "run_id",
    "seed",
    "event_id",
    "event_type",
    "query_id",
    "query_type",
    "reader_id",
    "retriever",
    "method",
    "scoring_class",
    "correct_canonical_contained",
    "conflicting_canonical_contained",
    "exploratory",
)


def add_query(
    rows: list[dict[str, object]],
    *,
    event_id: str,
    query_id: str,
    outcomes: dict[str, str],
    seed: int = 13,
    event_type: str = "REVISE",
    query_type: str = "target",
    reader_id: str = "qwen3p5_9b",
    retriever: str = "bm25",
    exploratory: bool = False,
    conflicts: dict[str, bool] | None = None,
) -> None:
    conflicts = conflicts or {}
    for method in METHODS:
        scoring_class = outcomes[method]
        rows.append(
            {
                "run_id": "fixture",
                "seed": seed,
                "event_id": event_id,
                "event_type": event_type,
                "query_id": query_id,
                "query_type": query_type,
                "reader_id": reader_id,
                "retriever": retriever,
                "method": method,
                "scoring_class": scoring_class,
                "correct_canonical_contained": scoring_class in {CORRECT, MIXED},
                "conflicting_canonical_contained": conflicts.get(
                    method, scoring_class == MIXED
                ),
                "exploratory": exploratory,
            }
        )


def write_fixture(
    root: Path,
    rows: list[dict[str, object]],
    *,
    exploratory: bool = False,
    git_dirty: bool = False,
    preregistered_closure: bool | None = None,
) -> Path:
    metrics_path = root / "rag_document_llm_metrics.csv"
    root.mkdir(parents=True, exist_ok=True)
    with metrics_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    if preregistered_closure is None:
        preregistered_closure = not exploratory
    (root / "run_manifest.json").write_text(
        json.dumps(
            {
                "row_count": len(rows),
                "exploratory": exploratory,
                "preregistered_closure": preregistered_closure,
                "git": {"git_sha": "fixture-sha", "git_dirty": git_dirty},
            }
        ),
        encoding="utf-8",
    )
    return metrics_path


def find_row(rows: list[dict[str, object]], **expected: object) -> dict[str, object]:
    matches = [
        row
        for row in rows
        if all(row.get(field) == value for field, value in expected.items())
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one row for {expected}, found {len(matches)}")
    return matches[0]


class LLMClusterInferenceTest(unittest.TestCase):
    def test_coverage_selective_accuracy_and_obsolete_rate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rows: list[dict[str, object]] = []
            outcomes = (CORRECT, WRONG, ABSTAIN, REFUSAL)
            for index, outcome in enumerate(outcomes):
                per_method = {method: outcome for method in METHODS}
                conflicts = {
                    method: outcome == WRONG for method in METHODS
                }
                add_query(
                    rows,
                    event_id="e1",
                    query_id=f"q{index}",
                    outcomes=per_method,
                    conflicts=conflicts,
                )
            metrics_path = write_fixture(Path(tmp), rows)
            data = load_input(metrics_path).cells[("qwen3p5_9b", "bm25")]
            weights = np.ones((4, 1), dtype=np.int32)
            output = cell_metric_rows(data, weights)

            coverage = find_row(
                output,
                method="no_update",
                query_type="target",
                metric="coverage",
            )
            selective = find_row(
                output,
                method="no_update",
                query_type="target",
                metric="selective_accuracy",
            )
            unconditional = find_row(
                output,
                method="no_update",
                query_type="target",
                metric="unconditional_target_success",
            )
            obsolete = find_row(
                output,
                method="no_update",
                query_type="target",
                metric="obsolete_rate",
            )
            abstention = find_row(
                output,
                method="no_update",
                query_type="target",
                metric="abstention_rate",
            )
            self.assertEqual(coverage["estimate"], 0.5)
            self.assertEqual(selective["estimate"], 0.5)
            self.assertEqual(selective["denominator"], 2)
            self.assertEqual(unconditional["estimate"], 0.25)
            self.assertEqual(obsolete["estimate"], 0.25)
            self.assertEqual(abstention["estimate"], 0.5)

    def test_clean_selective_separation_is_coverage_limited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rows: list[dict[str, object]] = []
            add_query(
                rows,
                event_id="e1",
                query_id="q1",
                outcomes={method: CORRECT for method in METHODS},
            )
            add_query(
                rows,
                event_id="e1",
                query_id="q2",
                outcomes={
                    "no_update": ABSTAIN,
                    "store_only": ABSTAIN,
                    "query_rewrite_update": CORRECT,
                    "postevent_validity_filter": CORRECT,
                },
            )
            data = load_input(write_fixture(Path(tmp), rows)).cells[
                ("qwen3p5_9b", "bm25")
            ]
            weights = np.ones((8, 1), dtype=np.int32)
            cells = cell_metric_rows(data, weights)
            contrasts = contrast_rows(data, weights)

            old_coverage = find_row(
                cells,
                method="no_update",
                query_type="target",
                metric="coverage",
            )
            current_coverage = find_row(
                cells,
                method="query_rewrite_update",
                query_type="target",
                metric="coverage",
            )
            unconditional = find_row(
                contrasts,
                current_method="query_rewrite_update",
                old_store_method="no_update",
                query_type="target",
                metric="unconditional_target_success",
            )
            selective = find_row(
                contrasts,
                current_method="query_rewrite_update",
                old_store_method="no_update",
                query_type="target",
                metric="selective_accuracy",
            )
            self.assertEqual(old_coverage["estimate"], 0.5)
            self.assertEqual(current_coverage["estimate"], 1.0)
            self.assertEqual(unconditional["difference"], 0.5)
            self.assertEqual(selective["difference"], 0.0)

    def test_pairing_is_enforced_and_shared_cluster_shocks_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rows: list[dict[str, object]] = []
            for index, outcome in enumerate((WRONG, CORRECT, WRONG, CORRECT)):
                add_query(
                    rows,
                    event_id=f"e{index}",
                    query_id=f"q{index}",
                    outcomes={method: outcome for method in METHODS},
                )
            metrics_path = write_fixture(Path(tmp), rows)
            data = load_input(metrics_path).cells[("qwen3p5_9b", "bm25")]
            weights = build_bootstrap_weights(data, replicates=64, rng_seed=7)
            contrast = find_row(
                contrast_rows(data, weights),
                current_method="query_rewrite_update",
                old_store_method="no_update",
                query_type="target",
                metric="unconditional_target_success",
            )
            self.assertEqual(contrast["difference"], 0.0)
            self.assertEqual(contrast["ci_low"], 0.0)
            self.assertEqual(contrast["ci_high"], 0.0)

            unpaired = rows[:-1]
            unpaired_root = Path(tmp) / "unpaired"
            unpaired_path = write_fixture(unpaired_root, unpaired)
            with self.assertRaisesRegex(LLMInferenceError, "unpaired method coverage"):
                load_input(unpaired_path)

    def test_readers_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rows: list[dict[str, object]] = []
            add_query(
                rows,
                event_id="e1",
                query_id="q1",
                outcomes={method: CORRECT for method in METHODS},
                reader_id="qwen3p5_9b",
            )
            add_query(
                rows,
                event_id="e1",
                query_id="q1",
                outcomes={method: WRONG for method in METHODS},
                reader_id="qwen3p5_9b",
                retriever="dense",
            )
            add_query(
                rows,
                event_id="e1",
                query_id="q1",
                outcomes={method: WRONG for method in METHODS},
                reader_id="grok_4_5",
            )
            loaded = load_input(write_fixture(Path(tmp), rows))
            self.assertEqual(
                set(loaded.cells),
                {
                    ("qwen3p5_9b", "bm25"),
                    ("qwen3p5_9b", "dense"),
                    ("qwen3p5_9b", "ALL"),
                    ("grok_4_5", "bm25"),
                    ("grok_4_5", "ALL"),
                },
            )
            estimates: dict[tuple[str, str], float] = {}
            for (reader_id, retriever), data in loaded.cells.items():
                output = cell_metric_rows(data, np.ones((2, 1), dtype=np.int32))
                row = find_row(
                    output,
                    method="no_update",
                    query_type="target",
                    metric="unconditional_target_success",
                )
                estimates[(reader_id, retriever)] = float(row["estimate"])
            self.assertEqual(estimates[("qwen3p5_9b", "bm25")], 1.0)
            self.assertEqual(estimates[("qwen3p5_9b", "dense")], 0.0)
            self.assertEqual(estimates[("qwen3p5_9b", "ALL")], 0.5)
            self.assertEqual(estimates[("grok_4_5", "bm25")], 0.0)
            self.assertEqual(estimates[("grok_4_5", "ALL")], 0.0)

    def test_exploratory_flag_propagates_and_blocks_paper_facing_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows: list[dict[str, object]] = []
            add_query(
                rows,
                event_id="e1",
                query_id="q1",
                outcomes={method: CORRECT for method in METHODS},
                exploratory=True,
            )
            metrics_path = write_fixture(
                root / "input",
                rows,
                exploratory=True,
                preregistered_closure=False,
            )
            args = argparse.Namespace(
                input_metrics=metrics_path,
                output_dir=root / "output",
                run_id="exploratory-fixture",
                bootstrap_replicates=8,
                bootstrap_seed=20260714,
            )
            with patch(
                "rae_egta.rag_post_event.llm_cluster_inference.git_metadata",
                return_value={
                    "git_sha": "analysis-sha",
                    "git_dirty": False,
                    "git_status_short": "",
                },
            ):
                manifest = run_analysis(args)
            self.assertTrue(manifest["exploratory"])
            self.assertFalse(manifest["preregistered_closure"])
            self.assertFalse(manifest["paper_facing_eligible"])
            self.assertIn("not a pre-registered CL closure", manifest["claim_boundary"])
            persisted = json.loads(
                (root / "output" / "analysis_manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(persisted["exploratory"])
            self.assertFalse(persisted["paper_facing_eligible"])

    def test_hand_computable_paired_contrast_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            rows: list[dict[str, object]] = []
            for index, current_outcome in enumerate((WRONG, CORRECT)):
                add_query(
                    rows,
                    event_id=f"e{index}",
                    query_id=f"q{index}",
                    outcomes={
                        "no_update": WRONG,
                        "store_only": WRONG,
                        "query_rewrite_update": current_outcome,
                        "postevent_validity_filter": current_outcome,
                    },
                )
            data = load_input(write_fixture(Path(tmp), rows)).cells[
                ("qwen3p5_9b", "bm25")
            ]
            # Replicate differences are [0, 0.5, 1, 0].  NumPy's linear 95%
            # percentile interval is therefore [0, 0.9625].
            weights = np.asarray(
                [[2, 0], [1, 1], [0, 2], [2, 0]], dtype=np.int32
            )
            row = find_row(
                contrast_rows(data, weights),
                current_method="query_rewrite_update",
                old_store_method="no_update",
                query_type="target",
                metric="unconditional_target_success",
            )
            self.assertEqual(row["difference"], 0.5)
            self.assertEqual(row["ci_low"], 0.0)
            self.assertAlmostEqual(float(row["ci_high"]), 0.9625)
            self.assertEqual(row["bootstrap_valid_replicates"], 4)


if __name__ == "__main__":
    unittest.main()
