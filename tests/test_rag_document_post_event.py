import csv
import inspect
import json
import math
import sys
import tempfile
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
    NO_CURRENT,
    OBSOLETE_UNQUALIFIED,
    RETIRE,
    REVISE,
    TARGET,
)
from rae_egta.rag_post_event.document_backend import (  # noqa: E402
    DOC_CURRENT,
    DOC_OLD,
    INTENT_CURRENT_DEFAULT,
    INTENT_CURRENT_EXPLICIT,
    INTENT_HISTORICAL,
    INTENT_STABLE_CURRENT,
    STRESS_LEXICAL_SCHEMA,
    STATE_OLD,
    STATE_POSTEVENT,
    alias_attribute,
    alias_subject,
    answer_from_index,
    build_document_corpus,
    build_indexes,
    documents_for_event,
    expanded_prompt_for_event,
    infer_query_intent,
    rewrite_prompt_for_event,
)
from rae_egta.rag_post_event.run_document import (  # noqa: E402
    CSV_FIELDS,
    DEFAULT_METHODS,
    generate_rows,
    parse_args,
)
from rae_egta.rag_post_event.summarize import r3_event_type_summary, r3_r2_by_event  # noqa: E402


class RAGDocumentPostEventTest(unittest.TestCase):
    def _corpus(self):
        return build_document_corpus(
            event_count=24,
            seed=13,
            stable_queries_per_event=2,
            add_fraction=0.10,
            revise_fraction=0.50,
            retire_fraction=0.40,
            distractors_per_event=2,
            chunk_noise_paragraphs=0,
        )

    def test_revise_event_old_and_current_documents_disagree(self) -> None:
        corpus = self._corpus()
        event = next(event for event in corpus.events if event.event_type == REVISE)
        old_docs, post_docs = documents_for_event(event)
        old_doc = next(doc for doc in old_docs if doc.doc_role == DOC_OLD)
        current_doc = next(doc for doc in post_docs if doc.doc_role == DOC_CURRENT)

        self.assertNotEqual(old_doc.answer, current_doc.answer)
        self.assertIn(event.old_answer, old_doc.text)
        self.assertIn(event.new_answer, current_doc.text)

    def test_retire_event_current_answer_is_no_current_valid_answer(self) -> None:
        corpus = self._corpus()
        event = next(event for event in corpus.events if event.event_type == RETIRE)
        _, post_docs = documents_for_event(event)
        current_doc = next(doc for doc in post_docs if doc.doc_role == DOC_CURRENT)

        self.assertEqual(current_doc.answer, NO_CURRENT)
        self.assertIn(NO_CURRENT, current_doc.text)
        self.assertNotIn(f"Canonical answer: {event.old_answer}", current_doc.text)

    def test_stable_documents_are_not_rewritten_by_target_event(self) -> None:
        corpus = self._corpus()
        event = next(event for event in corpus.events if event.event_type in {REVISE, RETIRE})
        old_docs, post_docs = documents_for_event(event)
        old_stable = {doc.doc_id: doc.text for doc in old_docs if doc.doc_role == "stable"}
        post_stable = {doc.doc_id: doc.text for doc in post_docs if doc.doc_role == "stable"}

        self.assertTrue(old_stable)
        self.assertEqual(old_stable, post_stable)

    def test_old_index_retrieves_old_evidence_and_new_index_retrieves_postevent_evidence(self) -> None:
        corpus = self._corpus()
        old_index, postevent_index = build_indexes(corpus)
        event = next(event for event in corpus.events if event.event_type == REVISE)
        query = next(query for query in corpus.queries if query.event_id == event.event_id and query.query_type == TARGET)

        old_trace = answer_from_index(
            old_index,
            query,
            event,
            reference_state=STATE_OLD,
            gold_reference_state=STATE_OLD,
            top_k=5,
        )
        post_trace = answer_from_index(
            postevent_index,
            query,
            event,
            reference_state=STATE_POSTEVENT,
            gold_reference_state=STATE_POSTEVENT,
            top_k=5,
        )

        self.assertEqual(old_trace.answer, event.old_answer)
        self.assertEqual(old_trace.selected.chunk.doc_role, DOC_OLD)
        self.assertEqual(post_trace.answer, event.new_answer)
        self.assertEqual(post_trace.selected.chunk.doc_role, DOC_CURRENT)
        self.assertEqual(post_trace.retrieval_recall_at_k, 1)
        self.assertEqual(post_trace.evidence_hit, 1)

    def test_store_only_matches_postevent_reference_and_zeroes_stable_cvbd(self) -> None:
        rows, _, _, _, _ = generate_rows(
            run_id="unit",
            event_count=12,
            seeds=[13],
            methods=["store_only"],
            stable_queries_per_event=2,
            add_fraction=0.10,
            revise_fraction=0.50,
            retire_fraction=0.40,
            distractors_per_event=2,
            chunk_noise_paragraphs=0,
            retriever="bm25",
            top_k=5,
        )
        self.assertTrue(rows)
        for field in CSV_FIELDS:
            self.assertIn(field, rows[0])
        self.assertTrue(all(row["postevent_retention"] == 1 for row in rows))
        stable_cvbd = [row["cvbd"] for row in rows if row["query_type"] == CURRENT_VALID_STABLE]
        self.assertTrue(stable_cvbd)
        self.assertTrue(all(value == 0.0 for value in stable_cvbd))

    def test_obsolete_answer_rate_is_masked_to_obsolete_unqualified_queries(self) -> None:
        rows, _, _, _, _ = generate_rows(
            run_id="unit",
            event_count=12,
            seeds=[13],
            methods=["no_update"],
            stable_queries_per_event=1,
            add_fraction=0.10,
            revise_fraction=0.50,
            retire_fraction=0.40,
            distractors_per_event=2,
            chunk_noise_paragraphs=0,
            retriever="bm25",
            top_k=5,
        )
        obsolete_rows = [row for row in rows if row["query_type"] == OBSOLETE_UNQUALIFIED]
        other_rows = [row for row in rows if row["query_type"] != OBSOLETE_UNQUALIFIED]

        self.assertTrue(obsolete_rows)
        self.assertTrue(all(math.isfinite(row["obsolete_answer_rate"]) for row in obsolete_rows))
        self.assertTrue(all(math.isnan(row["obsolete_answer_rate"]) for row in other_rows))

    def test_historical_queries_do_not_pollute_current_valid_metric(self) -> None:
        rows, _, _, _, _ = generate_rows(
            run_id="unit",
            event_count=12,
            seeds=[13],
            methods=["store_only"],
            stable_queries_per_event=1,
            add_fraction=0.10,
            revise_fraction=0.50,
            retire_fraction=0.40,
            distractors_per_event=2,
            chunk_noise_paragraphs=0,
            retriever="bm25",
            top_k=5,
        )
        historical = [row for row in rows if row["query_type"] == HISTORICAL]
        obsolete = [row for row in rows if row["query_type"] == OBSOLETE_UNQUALIFIED]

        self.assertTrue(historical)
        self.assertTrue(all(math.isnan(row["current_valid_accuracy"]) for row in historical))
        self.assertTrue(all(row["updated_answer"] == row["old_answer"] for row in historical))
        self.assertTrue(all(row["updated_answer"] == row["postevent_answer"] for row in obsolete))
        self.assertTrue(any(row["updated_answer"] != row["old_answer"] for row in obsolete))

    def test_default_methods_use_explicit_optimized_versions(self) -> None:
        with patch.object(sys, "argv", ["run_document.py", "--output-dir", "unused"]):
            args = parse_args()

        self.assertEqual(args.methods, DEFAULT_METHODS)
        self.assertEqual(args.stress_axis, "none")
        self.assertIn("old_ref_reg_0p70", args.methods)
        self.assertIn("postevent_ref_reg_0p85", args.methods)
        self.assertNotIn("query_rewrite_update", args.methods)
        self.assertNotIn("old_ref_reg", args.methods)
        self.assertNotIn("postevent_ref_reg", args.methods)
        self.assertEqual(args.partial_write_every_queries, 250)
        self.assertEqual(args.rerank_candidate_k, 20)

    def test_lexical_schema_stress_rewrites_current_doc_schema(self) -> None:
        corpus = build_document_corpus(
            event_count=24,
            seed=13,
            stable_queries_per_event=2,
            add_fraction=0.10,
            revise_fraction=0.50,
            retire_fraction=0.40,
            distractors_per_event=2,
            chunk_noise_paragraphs=0,
            stress_axis=STRESS_LEXICAL_SCHEMA,
        )
        event = next(event for event in corpus.events if event.event_type == REVISE)
        query = next(query for query in corpus.queries if query.event_id == event.event_id and query.query_type == TARGET)
        _, post_docs = documents_for_event(event, stress_axis=STRESS_LEXICAL_SCHEMA)
        current_doc = next(doc for doc in post_docs if doc.doc_role == DOC_CURRENT)
        rewritten = rewrite_prompt_for_event(query, event, stress_axis=STRESS_LEXICAL_SCHEMA)

        self.assertIn(alias_subject(event), current_doc.text)
        self.assertIn(alias_attribute(event), current_doc.text)
        self.assertIn(alias_subject(event), rewritten)
        self.assertIn(alias_attribute(event), rewritten)
        self.assertNotEqual(query.prompt, rewritten)

    def test_lexical_schema_stress_makes_store_only_nontrivial_and_rewrite_adapts(self) -> None:
        rows, _, _, _, _ = generate_rows(
            run_id="unit_stress",
            event_count=48,
            seeds=[13],
            methods=[
                "store_only",
                "query_rewrite_update",
                "old_ref_rewrite_reg_0p70",
                "postevent_rewrite_reg_0p85",
            ],
            stable_queries_per_event=2,
            add_fraction=0.10,
            revise_fraction=0.50,
            retire_fraction=0.40,
            distractors_per_event=2,
            chunk_noise_paragraphs=0,
            retriever="bm25",
            top_k=5,
            stress_axis=STRESS_LEXICAL_SCHEMA,
        )

        def avg(method: str, field: str) -> float:
            vals = [
                float(row[field])
                for row in rows
                if row["method"] == method
                and row["query_type"] == TARGET
                and row["event_type"] in {REVISE, RETIRE}
                and math.isfinite(float(row[field]))
            ]
            self.assertTrue(vals)
            return sum(vals) / len(vals)

        store_target = avg("store_only", "target_success")
        rewrite_target = avg("query_rewrite_update", "target_success")
        store_recall = avg("store_only", "retrieval_recall_at_k")
        rewrite_recall = avg("query_rewrite_update", "retrieval_recall_at_k")
        old_ref_target = avg("old_ref_rewrite_reg_0p70", "target_success")
        postevent_target = avg("postevent_rewrite_reg_0p85", "target_success")

        self.assertLess(store_target, 1.0)
        self.assertLess(store_recall, 1.0)
        self.assertGreater(rewrite_target, store_target)
        self.assertGreater(rewrite_recall, store_recall)
        self.assertGreater(postevent_target, old_ref_target)

    def test_r2_intent_inference_and_alias_expansion_are_text_based(self) -> None:
        corpus = build_document_corpus(
            event_count=24,
            seed=13,
            stable_queries_per_event=1,
            add_fraction=0.10,
            revise_fraction=0.50,
            retire_fraction=0.40,
            distractors_per_event=2,
            chunk_noise_paragraphs=0,
            stress_axis=STRESS_LEXICAL_SCHEMA,
        )
        event = next(event for event in corpus.events if event.event_type == REVISE)
        target = next(query for query in corpus.queries if query.event_id == event.event_id and query.query_type == TARGET)
        obsolete = next(
            query
            for query in corpus.queries
            if query.event_id == event.event_id and query.query_type == OBSOLETE_UNQUALIFIED
        )
        historical = next(query for query in corpus.queries if query.event_id == event.event_id and query.query_type == HISTORICAL)
        stable = next(
            query for query in corpus.queries if query.event_id == event.event_id and query.query_type == CURRENT_VALID_STABLE
        )

        self.assertEqual(infer_query_intent(target.prompt), INTENT_CURRENT_EXPLICIT)
        self.assertEqual(infer_query_intent(obsolete.prompt), INTENT_CURRENT_DEFAULT)
        self.assertEqual(infer_query_intent(historical.prompt), INTENT_HISTORICAL)
        self.assertEqual(infer_query_intent(stable.prompt), INTENT_STABLE_CURRENT)

        target_expanded = expanded_prompt_for_event(
            target.prompt,
            event,
            stress_axis=STRESS_LEXICAL_SCHEMA,
            intent=infer_query_intent(target.prompt),
        )
        obsolete_expanded = expanded_prompt_for_event(
            obsolete.prompt,
            event,
            stress_axis=STRESS_LEXICAL_SCHEMA,
            intent=infer_query_intent(obsolete.prompt),
        )
        historical_expanded = expanded_prompt_for_event(
            historical.prompt,
            event,
            stress_axis=STRESS_LEXICAL_SCHEMA,
            intent=infer_query_intent(historical.prompt),
        )
        stable_expanded = expanded_prompt_for_event(
            stable.prompt,
            event,
            stress_axis=STRESS_LEXICAL_SCHEMA,
            intent=infer_query_intent(stable.prompt),
        )
        self.assertIn(alias_subject(event), target_expanded)
        self.assertIn(alias_attribute(event), obsolete_expanded)
        self.assertEqual(historical_expanded, historical.prompt)
        self.assertEqual(stable_expanded, stable.prompt)

        signature = inspect.signature(expanded_prompt_for_event)
        self.assertEqual(list(signature.parameters), ["prompt", "event", "stress_axis", "intent"])

    def test_r2_validity_filter_decouples_obsolete_control_from_target_adaptation(self) -> None:
        rows, _, _, _, _ = generate_rows(
            run_id="unit_r2",
            event_count=48,
            seeds=[13],
            methods=[
                "oldref_adapted_preserver",
                "postevent_validity_filter",
                "intent_aware_postevent_filter",
            ],
            stable_queries_per_event=2,
            add_fraction=0.10,
            revise_fraction=0.50,
            retire_fraction=0.40,
            distractors_per_event=2,
            chunk_noise_paragraphs=0,
            retriever="bm25",
            top_k=5,
            rerank_candidate_k=20,
            stress_axis=STRESS_LEXICAL_SCHEMA,
        )

        def avg(method: str, query_type: str, field: str) -> float:
            vals = [
                float(row[field])
                for row in rows
                if row["method"] == method
                and row["query_type"] == query_type
                and row["event_type"] in {REVISE, RETIRE}
                and math.isfinite(float(row[field]))
            ]
            self.assertTrue(vals)
            return sum(vals) / len(vals)

        old_target = avg("oldref_adapted_preserver", TARGET, "target_success")
        post_target = avg("postevent_validity_filter", TARGET, "target_success")
        old_obsolete = avg("oldref_adapted_preserver", OBSOLETE_UNQUALIFIED, "obsolete_answer_rate")
        post_obsolete = avg("postevent_validity_filter", OBSOLETE_UNQUALIFIED, "obsolete_answer_rate")
        old_historical = avg("oldref_adapted_preserver", HISTORICAL, "historical_accuracy")
        post_historical = avg("postevent_validity_filter", HISTORICAL, "historical_accuracy")
        old_evidence = avg("oldref_adapted_preserver", TARGET, "evidence_hit")
        post_evidence = avg("postevent_validity_filter", TARGET, "evidence_hit")

        self.assertLessEqual(abs(post_target - old_target), 0.03)
        self.assertGreaterEqual(old_obsolete - post_obsolete, 0.10)
        self.assertGreaterEqual(post_evidence - old_evidence, -0.01)
        self.assertGreaterEqual(post_historical - old_historical, -0.02)
        self.assertLess(avg("intent_aware_postevent_filter", OBSOLETE_UNQUALIFIED, "obsolete_answer_rate"), old_obsolete)

    def test_r3_generated_event_type_boundary_from_document_rows(self) -> None:
        rows, _, _, _, _ = generate_rows(
            run_id="unit_r3",
            event_count=60,
            seeds=[13],
            methods=[
                "store_only",
                "oldref_adapted_preserver",
                "postevent_validity_filter",
            ],
            stable_queries_per_event=2,
            add_fraction=0.10,
            revise_fraction=0.50,
            retire_fraction=0.40,
            distractors_per_event=2,
            chunk_noise_paragraphs=0,
            retriever="bm25",
            top_k=5,
            rerank_candidate_k=20,
            stress_axis=STRESS_LEXICAL_SCHEMA,
        )
        string_rows = [{key: str(value) for key, value in row.items()} for row in rows]
        summary = r3_event_type_summary(string_rows)

        def lookup(event_type: str, method: str, query_type: str, field: str) -> float:
            matches = [
                item
                for item in summary
                if item["event_type"] == event_type
                and item["method"] == method
                and item["query_type"] == query_type
            ]
            self.assertEqual(len(matches), 1)
            return float(matches[0][field])

        self.assertFalse(
            [
                item
                for item in summary
                if item["event_type"] == ADD and item["query_type"] == OBSOLETE_UNQUALIFIED
            ]
        )
        self.assertGreaterEqual(lookup(ADD, "store_only", TARGET, "target_success"), 0.95)
        for event_type in [REVISE, RETIRE]:
            self.assertLessEqual(lookup(event_type, "store_only", TARGET, "target_success"), 0.05)
            self.assertGreaterEqual(lookup(event_type, "oldref_adapted_preserver", TARGET, "target_success"), 0.95)
            self.assertGreaterEqual(
                lookup(event_type, "oldref_adapted_preserver", OBSOLETE_UNQUALIFIED, "obsolete_answer_rate"),
                0.95,
            )
            self.assertLessEqual(
                lookup(event_type, "postevent_validity_filter", OBSOLETE_UNQUALIFIED, "obsolete_answer_rate"),
                0.05,
            )
            self.assertGreaterEqual(
                lookup(event_type, "postevent_validity_filter", HISTORICAL, "historical_accuracy"),
                0.98,
            )

        gates = r3_r2_by_event(string_rows)
        add_gates = [item for item in gates if item["event_type"] == ADD]
        shifted_gates = [item for item in gates if item["event_type"] in {REVISE, RETIRE}]
        self.assertTrue(add_gates)
        self.assertTrue(all(item["r3_event_positive"] == "not_applicable" for item in add_gates))
        self.assertTrue(all(item["r3_event_gate_pass"] is True for item in add_gates))
        self.assertTrue(shifted_gates)
        self.assertTrue(all(item["r3_event_positive"] is True for item in shifted_gates))

    def test_optimized_retrieval_exactly_matches_full_scan_oracle(self) -> None:
        corpus = build_document_corpus(
            event_count=48,
            seed=13,
            stable_queries_per_event=2,
            add_fraction=0.10,
            revise_fraction=0.50,
            retire_fraction=0.40,
            distractors_per_event=2,
            chunk_noise_paragraphs=1,
            stress_axis=STRESS_LEXICAL_SCHEMA,
        )
        events_by_id = {event.event_id: event for event in corpus.events}
        revise_event = next(event for event in corpus.events if event.event_type == REVISE)
        retire_event = next(event for event in corpus.events if event.event_type == RETIRE)
        revise_target = next(
            query
            for query in corpus.queries
            if query.event_id == revise_event.event_id and query.query_type == TARGET
        )
        retire_target = next(
            query
            for query in corpus.queries
            if query.event_id == retire_event.event_id and query.query_type == TARGET
        )
        stable_query = next(query for query in corpus.queries if query.query_type == CURRENT_VALID_STABLE)
        historical_query = next(query for query in corpus.queries if query.query_type == HISTORICAL)
        prompt_cases = [
            revise_target.prompt,
            rewrite_prompt_for_event(revise_target, revise_event, stress_axis=STRESS_LEXICAL_SCHEMA),
            retire_target.prompt,
            rewrite_prompt_for_event(retire_target, retire_event, stress_axis=STRESS_LEXICAL_SCHEMA),
            stable_query.prompt,
            historical_query.prompt,
            f"{alias_subject(events_by_id[revise_target.event_id])} no_such_field_token",
            "zzzz_no_matching_token",
        ]

        for retriever in ["bm25", "tfidf"]:
            indexes = build_indexes(corpus, retriever=retriever)
            for index in indexes:
                for prompt in prompt_cases:
                    for top_k in [1, 5, 20]:
                        optimized = index.retrieve(prompt, top_k=top_k)
                        full_scan = index.retrieve_full_scan(prompt, top_k=top_k)
                        self.assertEqual(
                            [(item.chunk.chunk_id, item.rank, item.score) for item in optimized],
                            [(item.chunk.chunk_id, item.rank, item.score) for item in full_scan],
                            msg=f"retriever={retriever} top_k={top_k} prompt={prompt}",
                        )

    def test_streaming_partial_progress_matches_final_rows(self) -> None:
        kwargs = dict(
            run_id="unit_stream",
            event_count=8,
            seeds=[13],
            methods=["store_only", "query_rewrite_update"],
            stable_queries_per_event=1,
            add_fraction=0.10,
            revise_fraction=0.50,
            retire_fraction=0.40,
            distractors_per_event=1,
            chunk_noise_paragraphs=0,
            retriever="bm25",
            top_k=5,
            stress_axis=STRESS_LEXICAL_SCHEMA,
        )
        baseline_rows, _, _, _, _ = generate_rows(**kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            streamed_rows, _, _, _, _ = generate_rows(
                **kwargs,
                output_dir=out_dir,
                partial_write_every_queries=2,
            )
            partial_path = out_dir / "rag_postevent_metrics_partial.csv"
            progress_path = out_dir / "progress.jsonl"
            self.assertTrue(partial_path.exists())
            self.assertTrue(progress_path.exists())
            lines = partial_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(sum(1 for line in lines if line.startswith("run_id,")), 1)
            with partial_path.open(newline="", encoding="utf-8") as f:
                partial_rows = list(csv.DictReader(f))
            self.assertEqual(len(partial_rows), len(streamed_rows))
            self.assertEqual(streamed_rows, baseline_rows)

            progress_records = [
                json.loads(line)
                for line in progress_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(progress_records)
            cumulative_rows = [int(record["cumulative_rows"]) for record in progress_records]
            self.assertEqual(cumulative_rows, sorted(cumulative_rows))
            self.assertEqual(cumulative_rows[-1], len(streamed_rows))


if __name__ == "__main__":
    unittest.main()
