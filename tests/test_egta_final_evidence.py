from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from rae_egta.egta.final_evidence import (
    PRESERVER_METHOD,
    VALIDITY_METHOD,
    _case_markdown,
    _method_scores_from_raw_metrics,
    maximum_overcomeable_aligned_gap,
    reversal_threshold,
    selection_statistics,
    summarize_matched_capability,
)


class EGTAFinalEvidenceTest(unittest.TestCase):
    def test_sensitivity_equations(self) -> None:
        self.assertAlmostEqual(0.01 / 1.01, reversal_threshold(0.01, 1.0))
        self.assertAlmostEqual(
            6 / 41,
            maximum_overcomeable_aligned_gap(6 / 47, 1.0),
        )
        with self.assertRaises(ValueError):
            reversal_threshold(-0.01, 1.0)
        with self.assertRaises(ValueError):
            maximum_overcomeable_aligned_gap(1.0, 1.0)

    def test_matched_summary_uses_one_qeval_denominator(self) -> None:
        fields = (
            "method",
            "query_type",
            "row_count",
            "target_success",
            "current_valid_accuracy",
            "historical_accuracy",
            "obsolete_answer_rate",
            "old_reference_retention",
            "cvbd",
            "evidence_hit",
        )
        rows = []
        values = {
            PRESERVER_METHOD: {
                "target": (1, 1, "nan", "nan", 0, "nan", 1),
                "obsolete_unqualified": ("nan", 0, "nan", 1, 1, "nan", 1),
                "current_valid_stable": ("nan", 1, "nan", "nan", 1, 0, 1),
                "historical": ("nan", "nan", 1, "nan", 1, "nan", 1),
            },
            VALIDITY_METHOD: {
                "target": (1, 1, "nan", "nan", 0, "nan", 1),
                "obsolete_unqualified": ("nan", 1, "nan", 0, 0, "nan", 1),
                "current_valid_stable": ("nan", 1, "nan", "nan", 1, 0, 1),
                "historical": ("nan", "nan", 1, "nan", 1, "nan", 1),
            },
        }
        for method, query_rows in values.items():
            for query_type, metrics in query_rows.items():
                row = {
                    "method": method,
                    "query_type": query_type,
                    "row_count": 1,
                }
                row.update(dict(zip(fields[3:], metrics)))
                rows.append(row)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "query_summary.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            summary = summarize_matched_capability(path)

        by_method = {row["method"]: row for row in summary}
        self.assertEqual(3, by_method[PRESERVER_METHOD]["qeval_row_count"])
        self.assertAlmostEqual(
            2 / 3, by_method[PRESERVER_METHOD]["pre_event_retention_qeval"]
        )
        self.assertAlmostEqual(
            2 / 3, by_method[PRESERVER_METHOD]["current_valid_accuracy_qeval"]
        )
        self.assertAlmostEqual(
            1 / 3, by_method[VALIDITY_METHOD]["pre_event_retention_qeval"]
        )
        self.assertAlmostEqual(
            1.0, by_method[VALIDITY_METHOD]["current_valid_accuracy_qeval"]
        )
        selection = selection_statistics(
            summary, feasible_methods=(PRESERVER_METHOD, VALIDITY_METHOD)
        )
        self.assertTrue(selection["ranking_reversal"])
        self.assertAlmostEqual(1 / 3, selection["tie_robust_current_valid_regret"])

    def test_human_case_sheet_is_blind_to_model_decision(self) -> None:
        record = {
            "question": "What is current?",
            "frozen_answer": {"raw": "old"},
            "query_temporal_context": {
                "reference_time": {"latest": "2019-12-31"},
                "audit_as_of": "2026-07-21",
                "anchors": [{"kind": "deictic", "text": "current"}],
            },
            "evidence_sources": [
                {
                    "title": "Source",
                    "publisher": "Publisher",
                    "source_tier": "official_primary",
                    "url": "https://example.invalid",
                    "publication_or_update_date": "2022-01-01",
                    "access_date": "2026-07-21",
                    "content_sha256": "abc",
                    "spans": [
                        {
                            "locator": "p1",
                            "text": "A dated fact.",
                            "supports": ["current_answer"],
                        }
                    ],
                }
            ],
            "verdict": "superseded_revision",
            "proposed_current_answer": {"canonical": "new"},
        }
        sheet = _case_markdown("HR-001", record)
        self.assertIn("What is current?", sheet)
        self.assertIn("A dated fact.", sheet)
        self.assertNotIn("superseded_revision", sheet)
        self.assertNotIn("proposed_current_answer", sheet)
        self.assertNotIn("supports", sheet)

    def test_raw_qeval_requires_rowwise_common_support(self) -> None:
        fields = (
            "seed",
            "event_id",
            "query_id",
            "query_type",
            "method",
            "target_success",
            "obsolete_answer_rate",
            "old_reference_retention",
            "current_valid_accuracy",
        )
        row = {
            "seed": 1,
            "event_id": "event-1",
            "query_id": "query-1",
            "query_type": "current_valid_stable",
            "method": PRESERVER_METHOD,
            "target_success": "nan",
            "obsolete_answer_rate": "nan",
            "old_reference_retention": 1,
            "current_valid_accuracy": "nan",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "metrics.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerow(row)
            with self.assertRaisesRegex(ValueError, "common Q_eval support"):
                _method_scores_from_raw_metrics(path)


if __name__ == "__main__":
    unittest.main()
