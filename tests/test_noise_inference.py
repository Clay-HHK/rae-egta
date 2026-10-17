from __future__ import annotations

import argparse
import csv
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rae_egta.rag_post_event.cluster_inference import (
    OLD_METHOD,
    POST_METHOD,
    RQ2_METHODS,
)
from rae_egta.rag_post_event.noise_inference import (
    ALL_EVENT_TYPES,
    CVBD_INCREASE_CEILING,
    EVIDENCE_DELTA_FLOOR,
    FACTOR_SPECIFIC_DEGRADATION,
    HISTORICAL,
    HISTORICAL_DELTA_FLOOR,
    METADATA_INTENT_SENSITIVE_BOUNDARY,
    OBSOLETE,
    OBSOLETE_INCREASE_CEILING,
    Q_EVAL,
    ROBUST_MODERATE,
    STABLE,
    TARGET,
    TARGET_DELTA_FLOOR,
    CONDITION_SPECS,
    NoiseInferenceError,
    analyze_condition,
    build_bootstrap_weights,
    classify_verdict,
    evaluate_checkpoint,
    load_conditions,
    run_analysis,
)


CSV_FIELDS = (
    "seed",
    "event_id",
    "event_type",
    "query_id",
    "query_type",
    "method",
    "target_success",
    "current_valid_accuracy",
    "obsolete_answer_rate",
    "historical_accuracy",
    "old_reference_retention",
    "evidence_hit",
    "obsolete_evidence_hit",
    "cvbd",
)


CLUSTERS = (
    (13, "e_add", "ADD"),
    (13, "e_revise", "REVISE"),
    (21, "e_retire", "RETIRE"),
)


OLD_SCORES = {
    "no_update": 1.0,
    "store_only": 0.6,
    "query_rewrite_update": 0.4,
    "oldref_adapted_preserver": 0.9,
    "postevent_validity_filter": 0.2,
    "intent_aware_postevent_filter": 0.2,
}


CURRENT_SCORES = {
    "no_update": 0.3,
    "store_only": 0.8,
    "query_rewrite_update": 1.0,
    "oldref_adapted_preserver": 0.7,
    "postevent_validity_filter": 1.0,
    "intent_aware_postevent_filter": 1.0,
}


DEFAULT_NOISE = {
    "target_delta": -0.01,
    "historical_delta": -0.01,
    "obsolete_increase": 0.05,
    "evidence_delta": -0.005,
    "cvbd_increase": 0.005,
}


def _queries(event_id: str, event_type: str) -> list[tuple[str, str]]:
    rows = [(f"{event_id}_target", TARGET), (f"{event_id}_stable", STABLE)]
    if event_type != "ADD":
        rows.extend(
            [
                (f"{event_id}_obsolete", OBSOLETE),
                (f"{event_id}_historical", HISTORICAL),
            ]
        )
    return rows


def _clean_value(method: str, query_type: str, metric: str) -> float:
    if metric == "old_reference_retention":
        return OLD_SCORES[method]
    if metric == "current_valid_accuracy":
        return (
            CURRENT_SCORES[method]
            if query_type in {TARGET, STABLE, OBSOLETE}
            else math.nan
        )
    if metric == "target_success":
        if query_type != TARGET:
            return math.nan
        return 0.0 if method == "no_update" else CURRENT_SCORES[method]
    if metric == "historical_accuracy":
        return 1.0 if query_type == HISTORICAL else math.nan
    if metric == "obsolete_answer_rate":
        if query_type != OBSOLETE:
            return math.nan
        return 1.0 if method in {"no_update", OLD_METHOD} else 0.0
    if metric == "evidence_hit":
        return 1.0
    if metric == "obsolete_evidence_hit":
        return 0.0
    if metric == "cvbd":
        return 0.0 if query_type == STABLE else math.nan
    raise AssertionError(metric)


def _condition_rows(
    label: str, changes: dict[str, float] | None = None
) -> list[dict[str, object]]:
    changes = changes or {}
    rows: list[dict[str, object]] = []
    for seed, event_id, event_type in CLUSTERS:
        for query_id, query_type in _queries(event_id, event_type):
            for method in RQ2_METHODS:
                row: dict[str, object] = {
                    "seed": seed,
                    "event_id": event_id,
                    "event_type": event_type,
                    "query_id": query_id,
                    "query_type": query_type,
                    "method": method,
                }
                for metric in CSV_FIELDS[6:]:
                    row[metric] = _clean_value(method, query_type, metric)
                if label != "clean" and method == POST_METHOD:
                    if query_type == TARGET:
                        row["target_success"] = 1.0 + changes["target_delta"]
                        row["current_valid_accuracy"] = 1.0 + changes["target_delta"]
                        row["evidence_hit"] = 1.0 + changes["evidence_delta"]
                    elif query_type == HISTORICAL:
                        row["historical_accuracy"] = 1.0 + changes["historical_delta"]
                    elif query_type == OBSOLETE:
                        row["obsolete_answer_rate"] = changes["obsolete_increase"]
                    elif query_type == STABLE:
                        row["cvbd"] = changes["cvbd_increase"]
                rows.append(row)
    return rows


def _write_condition(
    root: Path,
    *,
    label: str,
    factor: str,
    rate: float,
    changes: dict[str, float] | None,
) -> None:
    condition_dir = root / label
    condition_dir.mkdir(parents=True)
    rows = _condition_rows(label, changes)
    metrics_path = condition_dir / "rag_postevent_metrics.csv"
    with metrics_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    manifest = {
        "row_count": len(rows),
        "event_record_count": len(CLUSTERS),
        "methods": list(RQ2_METHODS),
        "dataset_spec_hash": "unit-shared-dataset",
        "args": {"noise_factor": factor, "noise_rate": rate},
        "noise_assignment": {
            "factor": factor,
            "requested_rate": rate,
            "assignment_hash": f"assignment-{label}",
        },
        "git": {"git_sha": "unit-source", "git_dirty": False},
    }
    (condition_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def make_fixture(
    root: Path, overrides: dict[str, dict[str, float]] | None = None
) -> None:
    overrides = overrides or {}
    for spec in CONDITION_SPECS:
        changes = None
        if spec.label != "clean":
            changes = {**DEFAULT_NOISE, **overrides.get(spec.label, {})}
        _write_condition(
            root,
            label=spec.label,
            factor=spec.factor,
            rate=spec.rate,
            changes=changes,
        )


def _select(
    rows: list[dict[str, object]],
    *,
    row_type: str,
    metric: str,
    method: str,
    query_scope: str,
) -> dict[str, object]:
    matches = [
        row
        for row in rows
        if row["row_type"] == row_type
        and row["metric"] == metric
        and row["method"] == method
        and row["query_scope"] == query_scope
        and row["event_scope"] == ALL_EVENT_TYPES
    ]
    if len(matches) != 1:
        raise AssertionError(matches)
    return matches[0]


def _gate_rows(
    *,
    target_low: float = TARGET_DELTA_FLOOR,
    historical_low: float = HISTORICAL_DELTA_FLOOR,
    obsolete_high: float = OBSOLETE_INCREASE_CEILING,
    reduction_low: float = 1e-9,
    evidence_low: float = EVIDENCE_DELTA_FLOOR,
    cvbd_high: float = CVBD_INCREASE_CEILING,
) -> list[dict[str, object]]:
    def metric_row(
        metric: str,
        query_scope: str,
        *,
        ci_low: float = -1.0,
        ci_high: float = 1.0,
    ) -> dict[str, object]:
        return {
            "row_type": "clean_relative_metric",
            "metric": metric,
            "query_scope": query_scope,
            "method": POST_METHOD,
            "event_scope": ALL_EVENT_TYPES,
            "ci_low": ci_low,
            "ci_high": ci_high,
        }

    return [
        metric_row("target_success", TARGET, ci_low=target_low),
        metric_row("historical_accuracy", HISTORICAL, ci_low=historical_low),
        metric_row("obsolete_answer_rate", OBSOLETE, ci_high=obsolete_high),
        metric_row("evidence_hit", TARGET, ci_low=evidence_low),
        metric_row("cvbd", STABLE, ci_high=cvbd_high),
        {
            "row_type": "postevent_vs_oldref",
            "metric": "obsolete_answer_rate_reduction",
            "query_scope": OBSOLETE,
            "method": f"{OLD_METHOD}-{POST_METHOD}",
            "event_scope": ALL_EVENT_TYPES,
            "ci_low": reduction_low,
            "ci_high": 1.0,
        },
    ]


class NoiseInferenceTest(unittest.TestCase):
    def test_clean_relative_delta_and_paired_obsolete_reduction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_fixture(root)
            conditions = load_conditions(root)
            clean = conditions["clean"]
            weights = build_bootstrap_weights(clean, replicates=200, rng_seed=7)
            rows = analyze_condition(
                clean,
                conditions["metadata_missing_10"],
                weights,
                replicates=200,
            )

            target = _select(
                rows,
                row_type="clean_relative_metric",
                metric="target_success",
                method=POST_METHOD,
                query_scope=TARGET,
            )
            self.assertAlmostEqual(-0.01, float(target["point_estimate"]))
            self.assertAlmostEqual(-0.01, float(target["ci_low"]))
            self.assertAlmostEqual(-0.01, float(target["ci_high"]))
            self.assertEqual(3, target["paired_finite_cluster_count"])

            reduction = _select(
                rows,
                row_type="postevent_vs_oldref",
                metric="obsolete_answer_rate_reduction",
                method=f"{OLD_METHOD}-{POST_METHOD}",
                query_scope=OBSOLETE,
            )
            self.assertAlmostEqual(0.95, float(reduction["point_estimate"]))
            self.assertGreater(float(reduction["ci_low"]), 0.0)

            selection = _select(
                rows,
                row_type="clean_relative_regret",
                metric="tie_robust_method_selection_regret",
                method="__selection__",
                query_scope=Q_EVAL,
            )
            self.assertAlmostEqual(0.7, float(selection["condition_point"]))
            self.assertEqual('["no_update"]', selection["condition_historical_tie_set"])
            self.assertIn(
                "query_rewrite_update",
                selection["condition_current_tie_set"],
            )
            constrained = _select(
                rows,
                row_type="capability_constrained_regret",
                metric="tie_robust_method_selection_regret",
                method="__selection__",
                query_scope=Q_EVAL,
            )
            self.assertAlmostEqual(0.3, float(constrained["condition_point"]))
            self.assertEqual(
                '["oldref_adapted_preserver"]',
                constrained["condition_historical_tie_set"],
            )

    def test_nested_cluster_pairing_rejects_missing_cluster(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_fixture(root)
            condition_dir = root / "intent_error_20"
            metrics_path = condition_dir / "rag_postevent_metrics.csv"
            with metrics_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            rows = [row for row in rows if row["event_id"] != "e_retire"]
            with metrics_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(rows)
            manifest_path = condition_dir / "run_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["row_count"] = len(rows)
            manifest["event_record_count"] = 2
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(NoiseInferenceError, "cluster-key mismatch"):
                load_conditions(root)

    def test_threshold_edges_and_verdict_logic(self) -> None:
        exact = evaluate_checkpoint(_gate_rows())
        self.assertTrue(exact["pass"])
        strict_reduction = evaluate_checkpoint(_gate_rows(reduction_low=0.0))
        self.assertFalse(strict_reduction["pass"])

        families = ("metadata_missing", "metadata_corrupted", "intent_error")
        robust = [
            {"factor": factor, "rate": rate, "pass": True}
            for factor in families
            for rate in (0.05, 0.10, 0.20)
        ]
        self.assertEqual(ROBUST_MODERATE, classify_verdict(robust))

        factor_specific = [dict(item) for item in robust]
        next(
            item
            for item in factor_specific
            if item["factor"] == "intent_error" and item["rate"] == 0.10
        )["pass"] = False
        self.assertEqual(
            FACTOR_SPECIFIC_DEGRADATION, classify_verdict(factor_specific)
        )

        boundary = [dict(item) for item in robust]
        next(
            item
            for item in boundary
            if item["factor"] == "metadata_missing" and item["rate"] == 0.05
        )["pass"] = False
        self.assertEqual(
            METADATA_INTENT_SENSITIVE_BOUNDARY, classify_verdict(boundary)
        )

    def test_run_analysis_emits_robust_moderate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_root = root / "inputs"
            output_dir = root / "output"
            make_fixture(input_root)
            args = argparse.Namespace(
                input_root=input_root,
                output_dir=output_dir,
                run_id="unit_robust",
                bootstrap_replicates=100,
                bootstrap_seed=11,
            )
            with patch(
                "rae_egta.rag_post_event.noise_inference.git_metadata",
                return_value={"git_sha": "unit-analysis", "git_dirty": False},
            ):
                manifest = run_analysis(args)

            self.assertEqual(ROBUST_MODERATE, manifest["verdict"])
            self.assertTrue(manifest["paper_facing_eligible"])
            verdict = json.loads(
                (output_dir / "verdict.json").read_text(encoding="utf-8")
            )
            self.assertEqual(ROBUST_MODERATE, verdict["verdict"])
            self.assertFalse(verdict["twenty_percent_is_hard_gate"])
            for spec in CONDITION_SPECS:
                self.assertTrue(
                    (output_dir / f"condition_{spec.label}_cluster_ci.csv").is_file()
                )

    def test_run_analysis_emits_sensitive_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_root = root / "inputs"
            output_dir = root / "output"
            make_fixture(
                input_root,
                overrides={
                    "intent_error_05": {"target_delta": -0.10},
                    "intent_error_10": {"target_delta": -0.10},
                    "intent_error_20": {"target_delta": -0.10},
                },
            )
            args = argparse.Namespace(
                input_root=input_root,
                output_dir=output_dir,
                run_id="unit_boundary",
                bootstrap_replicates=100,
                bootstrap_seed=13,
            )
            with patch(
                "rae_egta.rag_post_event.noise_inference.git_metadata",
                return_value={"git_sha": "unit-analysis", "git_dirty": False},
            ):
                manifest = run_analysis(args)

            self.assertEqual(
                METADATA_INTENT_SENSITIVE_BOUNDARY, manifest["verdict"]
            )
            verdict = json.loads(
                (output_dir / "verdict.json").read_text(encoding="utf-8")
            )
            intent_five = next(
                gate
                for gate in verdict["condition_gates"]
                if gate["condition"] == "intent_error_05"
            )
            self.assertFalse(intent_five["pass"])
            self.assertFalse(
                intent_five["conditions"]["target_clean_relative_noninferior"]
            )


if __name__ == "__main__":
    unittest.main()
