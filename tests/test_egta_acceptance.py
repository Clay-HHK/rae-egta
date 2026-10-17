from __future__ import annotations

import copy
import unittest
from unittest import mock

from rae_egta.egta.aggregate import (
    _record_outcome,
    acceptance_conditions,
    abstention_reason,
    choose_decision_pair,
    compatible_answer,
    normalize_answer,
)
from rae_egta.egta._aggregate_stats import wilson_interval
from rae_egta.egta._provenance import git_metadata_ignoring_egta
from rae_egta.egta.evidence import build_evidence_package
from tests.egta_fixtures import judgment, migrated_record, result, source


class EGTAAcceptanceTest(unittest.TestCase):
    def _supersession(self, item_id: str = "egta_fixture") -> dict[str, object]:
        return judgment(
            item_id,
            verdict="superseded_revision",
            event_type="revision",
            proposed_current_answer={"state": "value", "canonical": "New Answer"},
            change_date={"earliest": "2025-01-01", "latest": "2025-01-01", "precision": "day"},
            supporting_evidence_ids=["src-1"],
        )

    def test_all_acceptance_conditions_true_for_valid_pair(self) -> None:
        pair = [self._supersession(), self._supersession()]
        conditions = acceptance_conditions(
            pair,
            reference_time={"earliest": "2024-01-01", "latest": "2024-12-31", "precision": "year"},
            sources=[source()],
            alias_groups=[],
        )
        self.assertTrue(all(conditions.values()), conditions)

    def test_boolean_condition_truth_table(self) -> None:
        reference = {"earliest": "2024-01-01", "latest": "2024-12-31", "precision": "year"}
        sources = [source()]
        base = [self._supersession(), self._supersession()]
        cases = []
        cases.append(("independent_decision_pair", None, sources))
        pair = copy.deepcopy(base)
        pair[1]["verdict"] = "still_valid"
        cases.append(("same_supersession_class", pair, sources))
        pair = copy.deepcopy(base)
        pair[1]["frozen_answer_identified"] = False
        cases.append(("frozen_answer_identified", pair, sources))
        pair = copy.deepcopy(base)
        pair[1]["proposed_current_answer"] = {"state": "value", "canonical": "Other"}
        cases.append(("current_answer_compatible", pair, sources))
        pair = copy.deepcopy(base)
        pair[1]["supporting_evidence_ids"] = []
        cases.append(("timestamped_external_evidence", pair, sources))
        pair = copy.deepcopy(base)
        pair[1]["change_date"] = {"earliest": "2024-06-01", "latest": "2025-01-01", "precision": "interval"}
        cases.append(("change_after_reference", pair, sources))
        pair = copy.deepcopy(base)
        pair[1]["direction"] = "historical"
        cases.append(("current_directed_query", pair, sources))
        pair = copy.deepcopy(base)
        pair[1]["credible_source_conflict"] = True
        cases.append(("no_unresolved_source_conflict", pair, sources))
        pair = copy.deepcopy(base)
        pair[1]["manual_override"] = True
        cases.append(("no_manual_override", pair, sources))

        for expected_false, pair, case_sources in cases:
            with self.subTest(condition=expected_false):
                conditions = acceptance_conditions(
                    pair,
                    reference_time=reference,
                    sources=case_sources,
                    alias_groups=[],
                )
                self.assertFalse(conditions[expected_false], conditions)

    def test_unknown_or_overlapping_reference_interval_rejects(self) -> None:
        pair = [self._supersession(), self._supersession()]
        for reference in (
            {"earliest": None, "latest": None, "precision": "unknown"},
            {"earliest": "2025-01-01", "latest": "2025-12-31", "precision": "year"},
        ):
            with self.subTest(reference=reference):
                conditions = acceptance_conditions(pair, reference_time=reference, sources=[source()], alias_groups=[])
                self.assertFalse(conditions["change_after_reference"])

    def test_abstention_priority_is_conflict_then_ambiguity_then_insufficient(self) -> None:
        default_conditions = acceptance_conditions(
            None,
            reference_time={"latest": None},
            sources=[],
            alias_groups=[],
        )
        conflict = judgment("egta_fixture", verdict="source_conflict", credible_source_conflict=True)
        ambiguous = judgment("egta_fixture", verdict="ambiguous_context", direction="ambiguous")
        self.assertEqual(
            "source_conflict",
            abstention_reason(None, pair_reason="none", conditions=default_conditions, auditor_judgments=[ambiguous, conflict]),
        )
        self.assertEqual(
            "ambiguous_context",
            abstention_reason(None, pair_reason="none", conditions=default_conditions, auditor_judgments=[ambiguous]),
        )
        self.assertEqual(
            "insufficient_evidence",
            abstention_reason(None, pair_reason="none", conditions=default_conditions, auditor_judgments=[]),
        )

    def test_answer_normalization_and_only_prefrozen_aliases(self) -> None:
        self.assertEqual("new answer", normalize_answer("  （New   Answer）! "))
        self.assertTrue(compatible_answer("NEW ANSWER!", "new answer"))
        left = {"state": "value", "canonical": "NYC", "aliases": ["New York City"]}
        right = {"state": "value", "canonical": "New York City"}
        self.assertFalse(compatible_answer(left, right, []), "decision-local aliases must be ignored")
        self.assertTrue(compatible_answer(left, right, [["NYC", "New York City"]]))

    def test_decision_pair_rules(self) -> None:
        a_judgment = self._supersession()
        b_judgment = judgment(
            "egta_fixture",
            verdict="still_valid",
            event_type="none",
            proposed_current_answer={"state": "value", "canonical": "Old Answer"},
        )
        a, b = result(a_judgment), result(b_judgment)
        pair, reason = choose_decision_pair(a, b, result(self._supersession()), alias_groups=[])
        self.assertEqual("auditor_verifier_pair", reason)
        self.assertIsNotNone(pair)

        same_a = result(self._supersession())
        same_b_judgment = self._supersession()
        same_b_judgment["supporting_evidence_ids"] = ["src-2"]
        same_b = result(same_b_judgment)
        pair, reason = choose_decision_pair(same_a, same_b, result(self._supersession()), alias_groups=[])
        self.assertIsNone(pair)
        self.assertEqual("verifier_not_exactly_one_match", reason)

        invalid_a = result(self._supersession(), valid=False)
        valid_b = result(self._supersession())
        pair, reason = choose_decision_pair(invalid_a, valid_b, result(self._supersession()), alias_groups=[])
        self.assertIsNotNone(pair, "one valid auditor plus a matching verifier is two valid judgments")
        self.assertEqual("auditor_verifier_pair", reason)

    def test_wilson_interval_uses_standard_closed_form(self) -> None:
        low, high = wilson_interval(5, 10)
        self.assertAlmostEqual(0.236593, low, places=6)
        self.assertAlmostEqual(0.763407, high, places=6)
        self.assertEqual((None, None), wilson_interval(0, 0))

    def test_egta_git_wrapper_ignores_only_run_artifacts(self) -> None:
        base = {
            "git_sha": "a" * 40,
            "git_dirty": True,
            "git_status_short": "?? audit/\n M src/changed.py",
            "git_dirty_ignored_prefixes": ["raw_runs/"],
        }
        with mock.patch("rae_egta.egta._provenance.git_metadata", return_value=base):
            metadata = git_metadata_ignoring_egta()
        self.assertTrue(metadata["git_dirty"])
        self.assertEqual(" M src/changed.py", metadata["git_status_short"])
        self.assertIn("audit/", metadata["git_dirty_ignored_prefixes"])
        only_egta = {**base, "git_status_short": "?? audit/runs/r0/"}
        with mock.patch("rae_egta.egta._provenance.git_metadata", return_value=only_egta):
            metadata = git_metadata_ignoring_egta()
        self.assertFalse(metadata["git_dirty"])

    def test_record_aggregation_accepts_a_fully_supported_supersession(self) -> None:
        record = migrated_record(77)
        record["query_temporal_context"]["reference_time"] = {
            "earliest": "2024-01-01",
            "latest": "2024-12-31",
            "precision": "year",
            "provenance": "fixture reference interval",
        }
        package = build_evidence_package(
            record["item_id"],
            [source()],
            aliases=[["New Answer", "New-Answer"]],
        )
        decision = self._supersession(record["item_id"])
        a, b = result(decision, aliases=package["aliases"]), result(decision, aliases=package["aliases"])
        a["evidence_package_sha256"] = package["package_sha256"]
        b["evidence_package_sha256"] = package["package_sha256"]
        final, accounting = _record_outcome(record, package, a, b, None)
        self.assertTrue(final["acceptance"]["supersession_accepted"])
        self.assertEqual("superseded_revision", final["verdict"])
        self.assertEqual("New Answer", final["proposed_current_answer"]["canonical"])
        self.assertTrue(accounting["supersession_accepted"])


class NullReferenceEarliestTest(unittest.TestCase):
    def test_null_reference_earliest_does_not_veto(self) -> None:
        from rae_egta.egta._acceptance import _strictly_after_reference

        decision = {"change_date": {"earliest": "2022-01-01", "latest": "2022-12-31"}}
        self.assertTrue(
            _strictly_after_reference(decision, {"earliest": None, "latest": "2019-12-31"})
        )
        self.assertFalse(
            _strictly_after_reference(decision, {"earliest": None, "latest": "2022-06-30"})
        )
        self.assertFalse(
            _strictly_after_reference(decision, {"earliest": None, "latest": None})
        )


if __name__ == "__main__":
    unittest.main()
