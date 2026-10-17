import math
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from rae_egta.rag_post_event.data import (  # noqa: E402
    ADD,
    CURRENT_VALID_STABLE,
    HISTORICAL,
    OBSOLETE_UNQUALIFIED,
    QUERY_TEMPLATE_VERSION,
    RETIRE,
    REVISE,
    TARGET,
    KnowledgeEvent,
    StableFact,
    build_dataset,
    queries_for_event,
    query_to_json,
)
from rae_egta.rag_post_event.run_structured import (  # noqa: E402
    CSV_FIELDS,
    DEFAULT_METHODS,
    generate_rows,
    parse_args,
)
from rae_egta.rag_post_event.summarize import (  # noqa: E402
    method_summary,
    legacy_scalar_grid_matched_pairs,
    r2_method_summary,
    legacy_scalar_grid_pair_grid,
    r2_gate_rows,
    r3_event_regret,
    r3_event_type_summary,
    r3_r2_by_event,
    ranking_summary,
)


class RAGPostEventTest(unittest.TestCase):
    def test_versioned_reader_queries_are_natural_for_every_query_type(self) -> None:
        attributes = (
            "current_status",
            "current_owner",
            "status",
            "currently_supported_tier",
            "last_known_state",
        )
        for attribute in attributes:
            with self.subTest(attribute=attribute):
                event = KnowledgeEvent(
                    event_id=7,
                    event_type=REVISE,
                    subject="entity_13_0007",
                    attribute=attribute,
                    old_answer="old_value",
                    new_answer="new_value",
                    stable_facts=(StableFact(key="stable_key", answer="stable_value"),),
                )
                queries = queries_for_event(event)
                by_type = {query.query_type: query for query in queries}

                self.assertEqual(
                    {
                        TARGET,
                        OBSOLETE_UNQUALIFIED,
                        HISTORICAL,
                        CURRENT_VALID_STABLE,
                    },
                    set(by_type),
                )
                attribute_prefix = attribute.replace("_", " ").split()[0].casefold()
                for query in queries:
                    for surface in (query.reader_prompt, query.prompt):
                        lowered = surface.casefold()
                        self.assertNotIn("current current", lowered)
                        self.assertNotRegex(
                            lowered,
                            rf"\b{re.escape(attribute_prefix)}\s+{re.escape(attribute_prefix)}\b",
                        )
                    self.assertEqual(
                        QUERY_TEMPLATE_VERSION,
                        query_to_json(query)["query_template_version"],
                    )

                if attribute == "current_status":
                    self.assertEqual(
                        "As of now, what is the status of entity_13_0007?",
                        by_type[TARGET].reader_prompt,
                    )
                    self.assertEqual(
                        "What is the status of entity_13_0007?",
                        by_type[OBSOLETE_UNQUALIFIED].reader_prompt,
                    )
                    self.assertEqual(
                        "Before the update, what was the status of entity_13_0007?",
                        by_type[HISTORICAL].reader_prompt,
                    )
                    self.assertEqual(
                        "As of now, what is the value of stable fact 0 for entity_13_0007?",
                        by_type[CURRENT_VALID_STABLE].reader_prompt,
                    )
                    self.assertEqual(
                        "The current_status of entity_13_0007: what is current?",
                        by_type[TARGET].prompt,
                    )
                    self.assertEqual(
                        "What is the current_status of entity_13_0007?",
                        by_type[OBSOLETE_UNQUALIFIED].prompt,
                    )

                expected_answer_semantics = {
                    TARGET: ("old_value", "new_value", "new_value", "old_value"),
                    OBSOLETE_UNQUALIFIED: (
                        "old_value",
                        "new_value",
                        "new_value",
                        "old_value",
                    ),
                    HISTORICAL: ("old_value", "old_value", "old_value", "old_value"),
                    CURRENT_VALID_STABLE: (
                        "stable_value",
                        "stable_value",
                        "stable_value",
                        "stable_value",
                    ),
                }
                for query_type, expected in expected_answer_semantics.items():
                    query = by_type[query_type]
                    self.assertEqual(
                        expected,
                        (
                            query.expected_old,
                            query.expected_postevent,
                            query.expected_current,
                            query.obsolete_answer,
                        ),
                    )

    def test_revise_and_retire_events_have_required_query_splits(self) -> None:
        events, queries = build_dataset(event_count=24, seed=13, stable_queries_per_event=2)
        event_types = {event.event_type for event in events}
        self.assertIn(REVISE, event_types)
        self.assertIn(RETIRE, event_types)

        query_types_by_event: dict[int, set[str]] = {}
        for query in queries:
            query_types_by_event.setdefault(query.event_id, set()).add(query.query_type)

        for event in events:
            if event.event_type in {REVISE, RETIRE}:
                query_types = query_types_by_event[event.event_id]
                self.assertIn(CURRENT_VALID_STABLE, query_types)
                self.assertIn(HISTORICAL, query_types)
                self.assertIn(OBSOLETE_UNQUALIFIED, query_types)

    def test_store_only_matches_postevent_reference(self) -> None:
        rows, _ = generate_rows(
            run_id="unit",
            event_count=12,
            seeds=[13],
            methods=["store_only"],
            stable_queries_per_event=2,
            add_fraction=0.10,
            revise_fraction=0.45,
            retire_fraction=0.45,
        )
        self.assertTrue(rows)
        for field in CSV_FIELDS:
            self.assertIn(field, rows[0])
        self.assertTrue(all(row["postevent_retention"] == 1 for row in rows))
        self.assertTrue(all(row["adaptation_side_effect"] == 0.0 for row in rows))
        stable_cvbd = [row["cvbd"] for row in rows if row["query_type"] == CURRENT_VALID_STABLE]
        self.assertTrue(stable_cvbd)
        self.assertTrue(all(value == 0.0 for value in stable_cvbd))

    def test_metric_inversion_summary_detects_old_reference_mismatch(self) -> None:
        rows, _ = generate_rows(
            run_id="unit",
            event_count=120,
            seeds=[13, 21, 34],
            methods=[
                "no_update",
                "store_only",
                "naive_update",
                "replay_old",
                "old_ref_reg_0p70",
                "postevent_ref_reg_0p85",
            ],
            stable_queries_per_event=2,
            add_fraction=0.10,
            revise_fraction=0.45,
            retire_fraction=0.45,
        )
        string_rows = [{key: str(value) for key, value in row.items()} for row in rows]
        methods = method_summary(string_rows)
        ranking = ranking_summary(methods)

        self.assertTrue(ranking["metric_inversion_detected"])
        self.assertEqual(ranking["top_old_reference_method"], "no_update")
        self.assertEqual(ranking["best_current_valid_method"], "store_only")
        self.assertGreater(float(ranking["current_valid_regret_of_top_old"]), 0.0)
        self.assertTrue(math.isfinite(float(ranking["spearman_old_retention_vs_current_valid_accuracy"])))

    def test_default_methods_use_explicit_optimized_versions(self) -> None:
        with patch.object(sys, "argv", ["run_structured.py", "--output-dir", "unused"]):
            args = parse_args()

        self.assertEqual(args.methods, DEFAULT_METHODS)
        self.assertIn("old_ref_reg_0p70", args.methods)
        self.assertIn("postevent_ref_reg_0p85", args.methods)
        self.assertNotIn("old_ref_reg", args.methods)
        self.assertNotIn("postevent_ref_reg", args.methods)

    def test_t4_wrapper_rag_presets_use_explicit_optimized_versions(self) -> None:
        wrapper = (ROOT / "scripts" / "run_experiments.sh").read_text(encoding="utf-8")

        self.assertIn("old_ref_reg_0p70 postevent_ref_reg_0p85", wrapper)
        self.assertIn("rag-doc-postevent-stress-sanity", wrapper)
        self.assertIn("rag-doc-postevent-stress-smoke", wrapper)
        self.assertIn("rag-doc-postevent-stress-robust", wrapper)
        self.assertIn("rag-doc-postevent-stress-formal", wrapper)
        self.assertIn("rag-doc-postevent-stress-legacy-scalar-grid-sanity", wrapper)
        self.assertIn("rag-doc-postevent-stress-legacy-scalar-grid-smoke", wrapper)
        self.assertIn("rag-doc-postevent-stress-legacy-scalar-grid-formal", wrapper)
        self.assertIn("rag-doc-postevent-stress-r2-sanity", wrapper)
        self.assertIn("rag-doc-postevent-stress-r2-smoke", wrapper)
        self.assertIn("rag-doc-postevent-stress-r2-formal", wrapper)
        self.assertIn("--stress-axis lexical_schema", wrapper)
        self.assertIn("--rerank-candidate-k", wrapper)
        self.assertIn("query_rewrite_update", wrapper)
        self.assertIn("oldref_adapted_preserver", wrapper)
        self.assertIn("postevent_validity_filter", wrapper)
        self.assertIn("intent_aware_postevent_filter", wrapper)
        self.assertIn("old_ref_rewrite_reg_0p00", wrapper)
        self.assertIn("old_ref_rewrite_reg_0p70", wrapper)
        self.assertIn("old_ref_rewrite_reg_1p00", wrapper)
        self.assertIn("postevent_rewrite_reg_0p00", wrapper)
        self.assertIn("postevent_rewrite_reg_0p85", wrapper)
        self.assertIn("postevent_rewrite_reg_1p00", wrapper)
        self.assertNotIn(":-store_only naive_update old_ref_reg postevent_ref_reg", wrapper)
        self.assertNotIn(":-no_update store_only naive_update replay_old old_ref_reg postevent_ref_reg", wrapper)

    def test_r2_matched_success_grid_filters_add_and_detects_positive_pair(self) -> None:
        def row(
            method: str,
            event_type: str,
            query_type: str,
            *,
            target_success: str = "nan",
            current_valid_accuracy: str = "nan",
            obsolete_answer_rate: str = "nan",
            evidence_hit: str = "nan",
            obsolete_evidence_hit: str = "nan",
            retrieval_recall_at_k: str = "nan",
            cvbd: str = "nan",
        ) -> dict[str, str]:
            return {
                "method": method,
                "event_type": event_type,
                "query_type": query_type,
                "target_success": target_success,
                "current_valid_accuracy": current_valid_accuracy,
                "obsolete_answer_rate": obsolete_answer_rate,
                "evidence_hit": evidence_hit,
                "obsolete_evidence_hit": obsolete_evidence_hit,
                "retrieval_recall_at_k": retrieval_recall_at_k,
                "cvbd": cvbd,
            }

        rows = [
            row("old_ref_rewrite_reg_0p50", "REVISE", "target", target_success="0.50", evidence_hit="0.80", retrieval_recall_at_k="0.90"),
            row("old_ref_rewrite_reg_0p50", "ADD", "target", target_success="1.00", evidence_hit="1.00", retrieval_recall_at_k="1.00"),
            row("old_ref_rewrite_reg_0p50", "REVISE", "obsolete_unqualified", current_valid_accuracy="0.60", obsolete_answer_rate="0.40", obsolete_evidence_hit="0.40"),
            row("old_ref_rewrite_reg_0p50", "REVISE", "current_valid_stable", current_valid_accuracy="1.00", cvbd="0.00"),
            row("postevent_rewrite_reg_0p50", "REVISE", "target", target_success="0.52", evidence_hit="0.80", retrieval_recall_at_k="0.90"),
            row("postevent_rewrite_reg_0p50", "ADD", "target", target_success="0.00", evidence_hit="0.00", retrieval_recall_at_k="0.00"),
            row("postevent_rewrite_reg_0p50", "REVISE", "obsolete_unqualified", current_valid_accuracy="0.80", obsolete_answer_rate="0.20", obsolete_evidence_hit="0.20"),
            row("postevent_rewrite_reg_0p50", "REVISE", "current_valid_stable", current_valid_accuracy="1.00", cvbd="0.00"),
        ]

        summary = r2_method_summary(rows)
        by_method = {item["method"]: item for item in summary}
        self.assertAlmostEqual(float(by_method["old_ref_rewrite_reg_0p50"]["shifted_target_success"]), 0.50)
        self.assertAlmostEqual(float(by_method["postevent_rewrite_reg_0p50"]["shifted_target_success"]), 0.52)

        pairs = legacy_scalar_grid_pair_grid(summary)
        self.assertEqual(len(pairs), 1)
        self.assertAlmostEqual(float(pairs[0]["abs_target_success_delta"]), 0.02)
        self.assertTrue(pairs[0]["matched_target_success"])
        self.assertTrue(pairs[0]["legacy_scalar_grid_positive"])
        self.assertEqual(legacy_scalar_grid_matched_pairs(pairs), pairs)

    def test_r2_gate_uses_historical_non_regression_and_obsolete_reduction(self) -> None:
        def row(
            method: str,
            query_type: str,
            *,
            target_success: str = "nan",
            current_valid_accuracy: str = "nan",
            historical_accuracy: str = "nan",
            obsolete_answer_rate: str = "nan",
            evidence_hit: str = "nan",
            retrieval_recall_at_k: str = "nan",
            cvbd: str = "nan",
        ) -> dict[str, str]:
            return {
                "method": method,
                "event_type": "REVISE",
                "query_type": query_type,
                "target_success": target_success,
                "current_valid_accuracy": current_valid_accuracy,
                "historical_accuracy": historical_accuracy,
                "obsolete_answer_rate": obsolete_answer_rate,
                "evidence_hit": evidence_hit,
                "retrieval_recall_at_k": retrieval_recall_at_k,
                "cvbd": cvbd,
            }

        rows = [
            row("oldref_adapted_preserver", "target", target_success="0.98", evidence_hit="0.98", retrieval_recall_at_k="0.98"),
            row("oldref_adapted_preserver", "obsolete_unqualified", current_valid_accuracy="0.10", obsolete_answer_rate="0.90"),
            row("oldref_adapted_preserver", "current_valid_stable", current_valid_accuracy="1.00", cvbd="0.00"),
            row("oldref_adapted_preserver", "historical", historical_accuracy="1.00"),
            row("postevent_validity_filter", "target", target_success="1.00", evidence_hit="0.98", retrieval_recall_at_k="1.00"),
            row("postevent_validity_filter", "obsolete_unqualified", current_valid_accuracy="0.95", obsolete_answer_rate="0.05"),
            row("postevent_validity_filter", "current_valid_stable", current_valid_accuracy="1.00", cvbd="0.00"),
            row("postevent_validity_filter", "historical", historical_accuracy="0.99"),
        ]

        summary = r2_method_summary(rows)
        gate = r2_gate_rows(summary)
        self.assertEqual(len(gate), 1)
        self.assertTrue(gate[0]["matched_target_success"])
        self.assertGreaterEqual(float(gate[0]["obsolete_answer_rate_reduction"]), 0.10)
        self.assertGreaterEqual(float(gate[0]["historical_accuracy_delta"]), -0.02)
        self.assertTrue(gate[0]["r2_positive"])

    def test_r3_event_type_boundary_keeps_add_as_negative_control(self) -> None:
        def row(
            method: str,
            event_type: str,
            query_type: str,
            *,
            target_success: str = "nan",
            current_valid_accuracy: str = "nan",
            historical_accuracy: str = "nan",
            obsolete_answer_rate: str = "nan",
            evidence_hit: str = "nan",
            retrieval_recall_at_k: str = "nan",
            cvbd: str = "nan",
            old_reference_retention: str = "0.50",
            postevent_retention: str = "0.50",
        ) -> dict[str, str]:
            return {
                "seed": "13",
                "event_id": f"{event_type}_{method}_{query_type}",
                "method": method,
                "event_type": event_type,
                "query_type": query_type,
                "target_success": target_success,
                "current_valid_accuracy": current_valid_accuracy,
                "historical_accuracy": historical_accuracy,
                "obsolete_answer_rate": obsolete_answer_rate,
                "evidence_hit": evidence_hit,
                "retrieval_recall_at_k": retrieval_recall_at_k,
                "cvbd": cvbd,
                "old_reference_retention": old_reference_retention,
                "postevent_retention": postevent_retention,
            }

        rows: list[dict[str, str]] = []
        for method in ["store_only", "oldref_adapted_preserver", "postevent_validity_filter"]:
            rows.extend(
                [
                    row(
                        method,
                        ADD,
                        "target",
                        target_success="1.00",
                        current_valid_accuracy="1.00",
                        evidence_hit="1.00",
                        retrieval_recall_at_k="1.00",
                        old_reference_retention="0.50",
                        postevent_retention="1.00",
                    ),
                    row(
                        method,
                        ADD,
                        CURRENT_VALID_STABLE,
                        current_valid_accuracy="1.00",
                        evidence_hit="1.00",
                        cvbd="0.00",
                        old_reference_retention="1.00",
                        postevent_retention="1.00",
                    ),
                ]
            )

        for event_type in [REVISE, RETIRE]:
            rows.extend(
                [
                    row("store_only", event_type, "target", target_success="0.00", evidence_hit="0.00", retrieval_recall_at_k="0.00"),
                    row("oldref_adapted_preserver", event_type, "target", target_success="1.00", evidence_hit="1.00", retrieval_recall_at_k="1.00"),
                    row("postevent_validity_filter", event_type, "target", target_success="1.00", evidence_hit="1.00", retrieval_recall_at_k="1.00"),
                    row("oldref_adapted_preserver", event_type, OBSOLETE_UNQUALIFIED, current_valid_accuracy="0.00", obsolete_answer_rate="1.00"),
                    row("postevent_validity_filter", event_type, OBSOLETE_UNQUALIFIED, current_valid_accuracy="1.00", obsolete_answer_rate="0.00"),
                    row("oldref_adapted_preserver", event_type, CURRENT_VALID_STABLE, current_valid_accuracy="1.00", cvbd="0.00"),
                    row("postevent_validity_filter", event_type, CURRENT_VALID_STABLE, current_valid_accuracy="1.00", cvbd="0.00"),
                    row("oldref_adapted_preserver", event_type, HISTORICAL, historical_accuracy="1.00"),
                    row("postevent_validity_filter", event_type, HISTORICAL, historical_accuracy="1.00"),
                ]
            )

        event_summary = r3_event_type_summary(rows)
        add_obsolete = [
            item for item in event_summary if item["event_type"] == ADD and item["query_type"] == OBSOLETE_UNQUALIFIED
        ]
        self.assertEqual(add_obsolete, [])

        regret = {item["event_type"]: item for item in r3_event_regret(rows)}
        self.assertEqual(regret[ADD]["event_boundary_role"], "negative_control")
        self.assertEqual(regret[ADD]["obsolete_unqualified_row_count"], 0)
        self.assertIn("top_old_reference_method", regret[ADD])

        by_event = r3_r2_by_event(rows)
        add_rows = [item for item in by_event if item["event_type"] == ADD]
        shifted_rows = [item for item in by_event if item["event_type"] in {REVISE, RETIRE}]
        self.assertTrue(add_rows)
        self.assertTrue(all(item["event_boundary_role"] == "negative_control" for item in add_rows))
        self.assertTrue(all(item["r3_event_positive"] == "not_applicable" for item in add_rows))
        self.assertTrue(all(item["r3_event_gate_pass"] is True for item in add_rows))
        self.assertTrue(shifted_rows)
        self.assertTrue(all(item["r3_event_positive"] is True for item in shifted_rows))


if __name__ == "__main__":
    unittest.main()
