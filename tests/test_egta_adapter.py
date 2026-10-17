from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from rae_egta.egta.rae_adapter import adapt_records
from rae_egta.egta.runner import write_jsonl
from rae_egta.egta.schema import ScreeningDisposition, validate_record
from tests.egta_fixtures import migrated_record


def final_record(idx: int, outcome: str, *, dup_idxs: list[int] | None = None) -> dict[str, Any]:
    record = migrated_record(idx, dup_idxs=dup_idxs)
    if outcome == "non_temporal":
        record["screening_disposition"] = ScreeningDisposition.NOT_TIME_SENSITIVE.value
        record["query_temporal_context"]["scope"] = "non_temporal"
        record["query_temporal_context"]["direction"] = "none"
        record["verdict"] = None
        record["event_type"] = "none"
        record["acceptance"]["abstention_reason"] = None
    elif outcome in {"superseded_revision", "superseded_retirement"}:
        record["verdict"] = outcome
        record["event_type"] = "revision" if outcome.endswith("revision") else "retirement"
        record["change_date"] = {"earliest": "2025-01-01", "latest": "2025-01-01", "precision": "day"}
        if outcome.endswith("revision"):
            record["proposed_current_answer"] = {
                "state": "value", "canonical": "New Answer", "aliases": [], "valid_as_of": "2026-07-21",
            }
        else:
            record["proposed_current_answer"] = {
                "state": "absent_or_retired", "canonical": None, "aliases": [], "valid_as_of": "2026-07-21",
            }
        record["acceptance"]["source_supported"] = True
        record["acceptance"]["supersession_accepted"] = True
        record["acceptance"]["conditions"] = {key: True for key in record["acceptance"]["conditions"]}
        record["acceptance"]["abstention_reason"] = None
    elif outcome in {"still_valid", "historically_valid"}:
        record["verdict"] = outcome
        record["event_type"] = "none"
        record["acceptance"]["source_supported"] = True
        record["acceptance"]["abstention_reason"] = None
    else:
        record["verdict"] = outcome
        record["acceptance"]["abstention_reason"] = outcome
    errors = validate_record(record)
    if errors:
        raise AssertionError(errors)
    return record


class EGTAAdapterTest(unittest.TestCase):
    def test_all_outcomes_are_exhaustive_and_unique_records_are_not_recounted(self) -> None:
        outcomes = [
            "superseded_revision",
            "superseded_retirement",
            "still_valid",
            "historically_valid",
            "ambiguous_context",
            "insufficient_evidence",
            "source_conflict",
            "non_temporal",
        ]
        records = [
            final_record(100 + index, outcome, dup_idxs=[100, 999] if index == 0 else None)
            for index, outcome in enumerate(outcomes)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            records_path = root / "records.jsonl"
            write_jsonl(records_path, records)
            output = root / "rae"
            summary = adapt_records(records_path=records_path, output_dir=output)
            artifacts = {path.name for path in output.iterdir()}
            self.assertEqual(7, len(artifacts))
            self.assertEqual(7, summary["artifact_count"])
            partitions = [json.loads(line) for line in (output / "rae_partition.jsonl").read_text().splitlines()]
            self.assertEqual(8, len(partitions))
            self.assertEqual(8, len({row["item_id"] for row in partitions}))
            self.assertEqual(
                {"q_mis", "retention_eligible", "retention_eligible_historical", "unresolved", "non_temporal_pass_through"},
                {row["partition"] for row in partitions},
            )
            self.assertEqual(2, summary["q_mis_count"])
            self.assertEqual(2, summary["retention_eligible_count"])
            self.assertEqual(3, summary["unresolved_count"])
            self.assertEqual(1, summary["non_temporal_pass_through_count"])
            with (output / "row_idx_to_item_id.csv").open(newline="", encoding="utf-8") as handle:
                row_map = list(csv.DictReader(handle))
            self.assertEqual(9, len(row_map))
            self.assertEqual(9, len({row["row_idx"] for row in row_map}))
            self.assertEqual(8, sum(row["is_primary"] == "True" for row in row_map))

    def test_duplicate_row_idx_across_items_is_rejected(self) -> None:
        records = [
            final_record(1, "still_valid", dup_idxs=[1, 99]),
            final_record(2, "still_valid", dup_idxs=[2, 99]),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "records.jsonl"
            write_jsonl(path, records)
            with self.assertRaises(ValueError):
                adapt_records(records_path=path, output_dir=root / "rae")


if __name__ == "__main__":
    unittest.main()
