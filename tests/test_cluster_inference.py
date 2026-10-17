from __future__ import annotations

import csv
import math
import os
import random
import tempfile
import unittest
from pathlib import Path

import numpy as np

from rae_egta.rag_post_event.cluster_inference import (
    ADD,
    ALL_METRICS,
    BOOTSTRAP_SEED,
    CONDITIONAL_METRICS,
    EVENT_TYPES,
    EXPECTED_EVENT_COUNTS,
    EXPECTED_EVENTS_PER_SEED,
    EXPECTED_SEEDS,
    OLD_METHOD,
    POST_METHOD,
    REVISE,
    RETIRE,
    RQ1_METHODS,
    RQ1_METRICS,
    RQ2_METHODS,
    STORE_METHOD,
    AggregatedData,
    ClusterKey,
    InferenceValidationError,
    Inventory,
    aggregate_input,
    audit_input,
    bootstrap_metric_means,
    cluster_mask,
    draw_cluster_indices,
    pair_point_values,
    pairwise_rows_and_gates,
    percentile_interval,
    point_means,
    ranking_statistics,
    resample_sum_count,
    tie_set,
)


FIELDS = [
    "seed",
    "event_id",
    "event_type",
    "query_id",
    "query_type",
    "method",
    "old_reference_retention",
    "current_valid_accuracy",
    "target_success",
    "obsolete_answer_rate",
    "evidence_hit",
    "cvbd",
    "historical_accuracy",
]


def row(
    *,
    seed: int,
    event_id: str,
    event_type: str,
    query_id: str,
    query_type: str,
    method: str,
    old: float = 0.0,
    current: float = 0.0,
    target: float | str = "nan",
    obsolete: float | str = "nan",
    evidence: float | str = "nan",
    cvbd: float | str = "nan",
    historical: float | str = "nan",
) -> dict[str, object]:
    return {
        "seed": seed,
        "event_id": event_id,
        "event_type": event_type,
        "query_id": query_id,
        "query_type": query_type,
        "method": method,
        "old_reference_retention": old,
        "current_valid_accuracy": current,
        "target_success": target,
        "obsolete_answer_rate": obsolete,
        "evidence_hit": evidence,
        "cvbd": cvbd,
        "historical_accuracy": historical,
    }


def write_rows(path: Path, rows: list[dict[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def synthetic_data() -> AggregatedData:
    methods = (STORE_METHOD, OLD_METHOD, POST_METHOD)
    clusters = (ClusterKey(13, "0"), ClusterKey(13, "1"), ClusterKey(13, "2"))
    inventory = Inventory(
        path=Path("synthetic.csv"),
        row_count=0,
        methods=methods,
        clusters=clusters,
        event_type_by_cluster={
            clusters[0]: ADD,
            clusters[1]: REVISE,
            clusters[2]: RETIRE,
        },
        cluster_row_counts={key: 0 for key in clusters},
        query_count=0,
        query_type_counts={},
        sha256="synthetic",
    )
    sums = np.zeros((3, 3, len(ALL_METRICS)), dtype=np.float64)
    counts = np.zeros_like(sums, dtype=np.int64)
    for cluster_index in range(3):
        for method_index in range(3):
            for metric in RQ1_METRICS:
                metric_index = ALL_METRICS.index(metric)
                sums[cluster_index, method_index, metric_index] = method_index / 2
                counts[cluster_index, method_index, metric_index] = 1
    target_index = ALL_METRICS.index("target_success")
    evidence_index = ALL_METRICS.index("evidence_hit")
    cvbd_index = ALL_METRICS.index("cvbd")
    obsolete_index = ALL_METRICS.index("obsolete_answer_rate")
    historical_index = ALL_METRICS.index("historical_accuracy")
    for cluster_index in range(3):
        sums[cluster_index, :, target_index] = [1, 1, 1]
        sums[cluster_index, :, evidence_index] = [1, 1, 1]
        sums[cluster_index, :, cvbd_index] = [0, 0, 0]
        counts[cluster_index, :, target_index] = 1
        counts[cluster_index, :, evidence_index] = 1
        counts[cluster_index, :, cvbd_index] = 2
    for cluster_index in (1, 2):
        sums[cluster_index, :, obsolete_index] = [0, 1, 0]
        sums[cluster_index, :, historical_index] = [1, 1, 1]
        counts[cluster_index, :, obsolete_index] = 1
        counts[cluster_index, :, historical_index] = 1
    return AggregatedData(
        inventory=inventory,
        methods=methods,
        metrics=ALL_METRICS,
        sums=sums,
        finite_counts=counts,
        seeds=np.asarray([13, 13, 13]),
        event_types=np.asarray([ADD, REVISE, RETIRE], dtype=object),
    )


class InventoryAndAggregationTests(unittest.TestCase):
    def make_rows(self) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        methods = ("m1", "m2")
        for method in methods:
            rows.append(
                row(
                    seed=13,
                    event_id="same",
                    event_type=ADD,
                    query_id="c1_target",
                    query_type="target",
                    method=method,
                    old=0,
                    current=0,
                    target=1,
                    evidence=1,
                )
            )
            for index in range(3):
                rows.append(
                    row(
                        seed=21,
                        event_id="same",
                        event_type=REVISE,
                        query_id=f"c2_stable_{index}",
                        query_type="current_valid_stable",
                        method=method,
                        old=1,
                        current=1,
                        cvbd=0,
                    )
                )
        return rows

    def test_cluster_key_includes_seed_and_row_weighted_estimand_is_preserved(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "metrics.csv"
            write_rows(path, self.make_rows())
            inventory = audit_input(path, ("m1", "m2"), require_historical=False)
            self.assertEqual(len(inventory.clusters), 2)
            data = aggregate_input(inventory)
            means = point_means(
                data,
                ("m1", "m2"),
                ("old_reference_retention",),
                cluster_mask(data, event_types=(ADD, REVISE)),
            )
            self.assertAlmostEqual(float(means[0, 0]), 0.75)
            self.assertAlmostEqual(float(means[1, 0]), 0.75)
            self.assertNotAlmostEqual(float(means[0, 0]), 0.5)

    def test_input_row_order_does_not_change_point_estimate(self) -> None:
        rows = self.make_rows()
        shuffled = list(rows)
        random.Random(20260714).shuffle(shuffled)
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first.csv"
            second = Path(temp) / "second.csv"
            write_rows(first, rows)
            write_rows(second, shuffled)
            values = []
            for path in (first, second):
                data = aggregate_input(
                    audit_input(path, ("m1", "m2"), require_historical=False)
                )
                values.append(
                    point_means(
                        data,
                        ("m1", "m2"),
                        RQ1_METRICS,
                        cluster_mask(data, event_types=(ADD, REVISE)),
                    )
                )
            np.testing.assert_array_equal(values[0], values[1])

    def test_duplicate_row_fails_closed(self) -> None:
        rows = self.make_rows()
        rows.append(dict(rows[0]))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "duplicate.csv"
            write_rows(path, rows)
            with self.assertRaises(InferenceValidationError):
                audit_input(path, ("m1", "m2"), require_historical=False)

    def test_missing_method_coverage_fails_closed(self) -> None:
        rows = self.make_rows()
        rows = [
            item
            for item in rows
            if not (item["query_id"] == "c1_target" and item["method"] == "m2")
        ]
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "missing.csv"
            write_rows(path, rows)
            with self.assertRaises(InferenceValidationError):
                audit_input(path, ("m1", "m2"), require_historical=False)

    def test_method_specific_finite_mask_fails_closed(self) -> None:
        rows = self.make_rows()
        rows[0]["current_valid_accuracy"] = "nan"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "finite.csv"
            write_rows(path, rows)
            inventory = audit_input(path, ("m1", "m2"), require_historical=False)
            with self.assertRaises(InferenceValidationError):
                aggregate_input(inventory)


class ResamplingAndTieTests(unittest.TestCase):
    def test_complete_cluster_multiplicity_is_retained(self) -> None:
        sums = np.asarray([[[3.0]], [[10.0]]])
        counts = np.asarray([[[2]], [[1]]])
        sampled_sums, sampled_counts = resample_sum_count(
            sums, counts, np.asarray([[0, 0]])
        )
        self.assertEqual(float(sampled_sums[0, 0, 0]), 6.0)
        self.assertEqual(int(sampled_counts[0, 0, 0]), 4)

    def test_paired_methods_use_identical_draws(self) -> None:
        sums = np.asarray([[[1.0], [10.0]], [[2.0], [20.0]]])
        counts = np.ones_like(sums, dtype=np.int64)
        draws = np.asarray([[1, 1], [0, 1]])
        sampled_sums, _ = resample_sum_count(sums, counts, draws)
        np.testing.assert_array_equal(
            sampled_sums[:, :, 0], np.asarray([[4.0, 40.0], [3.0, 30.0]])
        )

    def test_draws_keep_original_stratum_cluster_count(self) -> None:
        draws = draw_cluster_indices(np.random.default_rng(7), 11, 23)
        self.assertEqual(draws.shape, (23, 11))

    def test_exact_and_epsilon_ties_are_set_valued(self) -> None:
        self.assertEqual(tie_set(("b", "a"), (1.0, 1.0)), ("a", "b"))
        self.assertEqual(tie_set(("b", "a"), (1.0, 1.0 + 5e-13)), ("a", "b"))
        self.assertEqual(tie_set(("b", "a"), (1.0, 1.0 + 2e-12)), ("a",))

    def test_lexicographic_order_cannot_change_regret(self) -> None:
        first = ranking_statistics(("b", "a", "c"), (1, 1, 0), (0.1, 0.4, 1.0))
        second = ranking_statistics(("c", "a", "b"), (0, 1, 1), (1.0, 0.4, 0.1))
        self.assertEqual(first["historical_tie_set"], second["historical_tie_set"])
        self.assertAlmostEqual(first["tie_robust_regret"], second["tie_robust_regret"])

    def test_tie_robust_regret_is_conservative_and_overlap_is_not_inversion(
        self,
    ) -> None:
        ranking = ranking_statistics(("a", "b", "c"), (1, 1, 0), (0.1, 0.4, 1.0))
        worst_member_regret = 1.0 - 0.1
        self.assertLessEqual(ranking["tie_robust_regret"], worst_member_regret)
        overlap = ranking_statistics(("a", "b"), (1, 1), (1, 0))
        self.assertFalse(overlap["inversion"])
        self.assertEqual(overlap["tie_robust_regret"], 0.0)

    def test_percentile_interval_is_deterministic_and_keeps_degenerate_interval(
        self,
    ) -> None:
        first = percentile_interval(np.zeros(100))
        second = percentile_interval(np.zeros(100))
        self.assertEqual(first, second)
        self.assertEqual(first, (0.0, 0.0, 100))

    def test_bootstrap_is_deterministic_under_fixed_pcg64_seed(self) -> None:
        data = synthetic_data()
        kwargs = dict(
            data=data,
            methods=data.methods,
            metrics=RQ1_METRICS,
            scopes={"all": EVENT_TYPES},
            replicates=50,
            rng_seed=BOOTSTRAP_SEED,
            batch_size=7,
        )
        first = bootstrap_metric_means(**kwargs)["all"]
        second = bootstrap_metric_means(**kwargs)["all"]
        np.testing.assert_array_equal(first, second)

    def test_add_obsolete_and_historical_are_not_applicable(self) -> None:
        data = synthetic_data()
        scopes = {
            "SHIFTED_REVISE_RETIRE": (REVISE, RETIRE),
            ADD: (ADD,),
            REVISE: (REVISE,),
            RETIRE: (RETIRE,),
        }
        samples = bootstrap_metric_means(
            data,
            methods=data.methods,
            metrics=CONDITIONAL_METRICS,
            scopes=scopes,
            replicates=20,
            rng_seed=BOOTSTRAP_SEED,
            batch_size=5,
        )
        pair_rows, absolute_rows, _, _ = pairwise_rows_and_gates(
            data, samples, data.methods
        )
        add_obsolete = next(
            item
            for item in pair_rows
            if item["scope"] == ADD
            and item["metric"] == "obsolete_answer_rate_reduction"
        )
        self.assertEqual(add_obsolete["status"], "not_applicable")
        self.assertTrue(math.isnan(float(add_obsolete["point_estimate"])))
        add_absolute = [
            item
            for item in absolute_rows
            if item["scope"] == ADD
            and item["metric"] in {"obsolete_answer_rate", "historical_accuracy"}
        ]
        self.assertTrue(add_absolute)
        self.assertTrue(
            all(item["status"] == "not_applicable" for item in add_absolute)
        )


class FormalGoldenIntegrationTest(unittest.TestCase):
    def test_formal_point_estimates_match_preserved_summaries(self) -> None:
        rq1_value = os.environ.get("P0_RQ1_METRICS")
        rq2_value = os.environ.get("P0_RQ2_METRICS")
        if not rq1_value or not rq2_value:
            self.skipTest("formal P0 input paths not supplied")
        rq1_path = Path(rq1_value)
        rq2_path = Path(rq2_value)
        rq1 = aggregate_input(
            audit_input(
                rq1_path,
                RQ1_METHODS,
                require_historical=False,
                expected_seeds=EXPECTED_SEEDS,
                expected_events_per_seed=EXPECTED_EVENTS_PER_SEED,
                expected_event_counts=EXPECTED_EVENT_COUNTS,
            )
        )
        rq1_point = point_means(
            rq1,
            RQ1_METHODS,
            RQ1_METRICS,
            cluster_mask(rq1, event_types=EVENT_TYPES),
        )
        rq1_ranking = ranking_statistics(RQ1_METHODS, rq1_point[:, 0], rq1_point[:, 1])
        self.assertEqual(
            rq1_ranking["historical_tie_set"], ("no_update", "stale_index")
        )
        self.assertEqual(rq1_ranking["current_tie_set"], ("query_rewrite_update",))
        self.assertAlmostEqual(
            rq1_ranking["tie_robust_regret"], 0.4871794871794872, places=12
        )
        self.assertAlmostEqual(rq1_ranking["spearman"], -0.6167939547439611, places=12)

        rq2 = aggregate_input(
            audit_input(
                rq2_path,
                RQ2_METHODS,
                require_historical=True,
                expected_seeds=EXPECTED_SEEDS,
                expected_events_per_seed=EXPECTED_EVENTS_PER_SEED,
                expected_event_counts=EXPECTED_EVENT_COUNTS,
            )
        )
        methods = (STORE_METHOD, OLD_METHOD, POST_METHOD)
        point = point_means(
            rq2,
            methods,
            CONDITIONAL_METRICS,
            cluster_mask(rq2, event_types=(REVISE, RETIRE)),
        )
        deltas = pair_point_values(point, methods)
        self.assertEqual(deltas["target_success_delta"], 0.0)
        self.assertEqual(deltas["obsolete_answer_rate_reduction"], 1.0)
        self.assertEqual(deltas["evidence_hit_delta"], 0.0)
        self.assertEqual(deltas["cvbd_delta"], 0.0)
        self.assertEqual(deltas["historical_accuracy_delta"], 0.0)


if __name__ == "__main__":
    unittest.main()
