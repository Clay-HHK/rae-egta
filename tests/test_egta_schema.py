from __future__ import annotations

import copy
import unittest

from rae_egta.egta.migrate import migrate_legacy_record
from rae_egta.egta.schema import (
    ScreeningDisposition,
    Verdict,
    assert_blind,
    to_blind_view,
    validate_record,
)


def valid_record() -> dict[str, object]:
    row = {
        "idx": 7,
        "dup_idxs": [7, 3],
        "stratum": "A_deictic_pure",
        "loc": "nq question: who is the current fixture holder",
        "loc_ans": "Old Answer",
        "verdict": "SUPERSEDED_CONFIRMED",
        "current_answer_2026": "New Answer",
        "changed_since_freeze": "yes",
        "stale_at_freeze": False,
        "evidence_url": "https://example.test/source",
        "note": "legacy-only note",
    }
    return migrate_legacy_record(
        row,
        input_sha256="a" * 64,
        git={"git_sha": "b" * 40, "git_dirty": False},
        created_at_utc="2026-07-22T00:00:00+00:00",
    )


class EGTASchemaTest(unittest.TestCase):
    def test_valid_record_has_no_violations(self) -> None:
        self.assertEqual([], validate_record(valid_record()))

    def test_invalid_record_reports_shape_and_semantic_violations(self) -> None:
        record = valid_record()
        record["schema_version"] = "egta.v0"
        record["item_id"] = "egta_not-a-hash"
        record["unexpected"] = True
        errors = validate_record(record)
        self.assertTrue(any("unexpected" in error for error in errors))

        del record["unexpected"]
        errors = validate_record(record)
        self.assertIn("schema_version must be 'egta.v1'", errors)
        self.assertIn("item_id must be egta_ followed by a lowercase SHA256", errors)

    def test_verdict_enum_has_exactly_seven_classes_and_excludes_screening(self) -> None:
        expected = {
            "still_valid",
            "superseded_revision",
            "superseded_retirement",
            "historically_valid",
            "ambiguous_context",
            "insufficient_evidence",
            "source_conflict",
        }
        self.assertEqual(expected, {item.value for item in Verdict})
        self.assertNotIn(ScreeningDisposition.NOT_TIME_SENSITIVE.value, expected)

        for verdict in Verdict:
            record = valid_record()
            record["verdict"] = verdict.value
            self.assertEqual([], validate_record(record), verdict.value)

        record = valid_record()
        record["verdict"] = "NOT_TIME_SENSITIVE"
        self.assertIn("verdict is not one of the seven EGTA ontology values", validate_record(record))

    def test_not_time_sensitive_is_a_screening_disposition_only(self) -> None:
        record = valid_record()
        record["legacy"]["legacy_verdict"] = "NOT_TIME_SENSITIVE"
        record["legacy"]["legacy_ontology_candidates"] = []
        record["screening_disposition"] = ScreeningDisposition.NOT_TIME_SENSITIVE.value
        record["query_temporal_context"]["scope"] = "non_temporal"
        record["query_temporal_context"]["direction"] = "none"
        self.assertEqual([], validate_record(record))

        record["verdict"] = Verdict.INSUFFICIENT_EVIDENCE.value
        self.assertIn("NOT_TIME_SENSITIVE must not be encoded as a verdict", validate_record(record))

    def test_blind_projection_rejects_legacy_and_auditor_output_fields(self) -> None:
        view = to_blind_view(valid_record())
        assert_blind(view)
        self.assertEqual("uncertain", view["query_temporal_context"]["scope"])

        leaked = copy.deepcopy(view)
        leaked["legacy"] = {"legacy_verdict": "SUPERSEDED_CONFIRMED"}
        with self.assertRaises(AssertionError):
            assert_blind(leaked)

        leaked = copy.deepcopy(view)
        leaked["auditor_output"] = {"current_answer": "New Answer", "note": "leak"}
        with self.assertRaises(AssertionError):
            assert_blind(leaked)


if __name__ == "__main__":
    unittest.main()
