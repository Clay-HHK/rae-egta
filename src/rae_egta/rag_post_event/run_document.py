#!/usr/bin/env python3
"""Run document-shaped evolving RAG post-event experiments.

This runner is the bridge between the structured QA mechanism scaffold and a
real document-level RAG substrate. It still uses a deterministic extractive
answerer, but every answer must come from retrieval and evidence selection over
versioned documents and chunks.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
from pathlib import Path
from typing import Any, Mapping

from rae_egta.common.provenance import environment_metadata, git_metadata, stable_json_hash
from rae_egta.rag_post_event.data import (
    ADD,
    CURRENT_VALID_STABLE,
    HISTORICAL,
    OBSOLETE_UNQUALIFIED,
    QUERY_TEMPLATE_VERSION,
    TARGET,
    UNKNOWN,
    KnowledgeEvent,
    Query,
    event_to_json,
    query_retrieval_prompt,
    query_to_json,
    stable_rng,
)
from rae_egta.rag_post_event.document_backend import (
    DOC_OLD,
    DOC_OLD_MISSING,
    POLICY_INTENT_AWARE_POSTEVENT,
    POLICY_OLDREF_ADAPTED,
    POLICY_POSTEVENT_VALIDITY,
    STRESS_LEXICAL_SCHEMA,
    STRESS_NONE,
    STATE_OLD,
    STATE_POSTEVENT,
    AnswerTrace,
    DocumentCorpus,
    DocumentIndex,
    RetrievedChunk,
    _rerank_bonus,
    answer_from_index,
    build_document_corpus,
    build_indexes,
    chunk_to_json,
    chunks_for_document,
    document_to_json,
    expanded_prompt_for_event,
    gold_doc_id,
    infer_query_intent,
    rewrite_prompt_for_event,
    unique_doc_ids,
    unique_versions,
)
from rae_egta.rag_post_event.noise import (
    CLEAN,
    NOISE_ASSIGNMENT_SEED,
    NOISE_FACTORS,
    SUPPORTED_RATES,
    NoiseAssignment,
    NoiseConfig,
    build_noise_view,
)
from rae_egta.rag_post_event.run_structured import CSV_FIELDS as STRUCTURED_CSV_FIELDS
from rae_egta.rag_post_event.run_structured import exact_match, parse_reg_strength


ROOT = Path(__file__).resolve().parents[3]


DEFAULT_METHODS = [
    "no_update",
    "store_only",
    "stale_index",
    "naive_update",
    "old_ref_reg_0p70",
    "postevent_ref_reg_0p85",
]

DOC_FIELDS = [
    "historical_accuracy",
    "retrieved_doc_ids",
    "retrieved_versions",
    "gold_doc_id",
    "evidence_hit",
    "obsolete_evidence_hit",
    "retrieval_recall_at_k",
    "answer_source",
    "chunk_rank",
    "retrieval_prompt",
    "stress_axis",
    "clean_intent",
    "effective_intent",
    "intent_corrupted",
    "noise_factor",
    "noise_requested_rate",
    "noise_realized_rate",
    "noise_assignment_hash",
]

CSV_FIELDS = [*STRUCTURED_CSV_FIELDS, *DOC_FIELDS]
CURRENT_VALID_TYPES = {TARGET, CURRENT_VALID_STABLE, OBSOLETE_UNQUALIFIED}


def enforce_t4_only() -> None:
    if platform.system() == "Darwin" and os.environ.get("ALLOW_LOCAL_RAG_DOCUMENT_RUN") != "1":
        raise SystemExit(
            "Refusing to run document RAG post-event experiments on local macOS. "
            "Run formal experiments on a Linux server, e.g. through scripts/run_experiments.sh. "
            "Set ALLOW_LOCAL_RAG_DOCUMENT_RUN=1 only for deliberate local debugging."
        )


def answer_from_version_reranked_candidates_with_intent(
    index: DocumentIndex,
    query: Query,
    event: KnowledgeEvent,
    *,
    policy: str,
    stress_axis: str,
    effective_intent: str,
    evaluation_document_labels: Mapping[str, tuple[str, str]],
    top_k: int = 5,
    rerank_candidate_k: int = 20,
) -> AnswerTrace:
    """Run the backend reranker with one explicitly resolved intent.

    The backend's clean helper resolves intent internally.  The noise runner
    must instead feed the same effective label to prompt expansion and rerank
    bonus, so this small adapter keeps that resolution outside both paths.
    """

    prompt = expanded_prompt_for_event(
        query_retrieval_prompt(query),
        event,
        stress_axis=stress_axis,
        intent=effective_intent,
    )
    candidate_k = max(top_k, rerank_candidate_k)
    candidates = index.retrieve(prompt, top_k=candidate_k)
    rescored: list[tuple[float, str, RetrievedChunk]] = []
    for item in candidates:
        final_score = item.score + _rerank_bonus(
            item.chunk,
            intent=effective_intent,
            policy=policy,
        )
        rescored.append((final_score, item.chunk.chunk_id, item))
    reranked = sorted(rescored, key=lambda item: (-item[0], item[1]))
    retrieved = tuple(
        RetrievedChunk(chunk=item.chunk, score=score, rank=rank)
        for rank, (score, _, item) in enumerate(reranked[:top_k], 1)
    )

    selected = None
    for item in retrieved:
        if item.chunk.answer:
            selected = item
            break
        evidence_chunk = next(
            (chunk for chunk in index.chunks_by_doc[item.chunk.doc_id] if chunk.answer),
            None,
        )
        if evidence_chunk is not None:
            selected = RetrievedChunk(chunk=evidence_chunk, score=item.score, rank=item.rank)
            break

    answer = selected.chunk.answer if selected else UNKNOWN
    gold = gold_doc_id(query, event, reference_state=STATE_POSTEVENT)
    retrieved_ids = unique_doc_ids(retrieved)
    obsolete_hit = 0
    if selected and query.query_type in {TARGET, OBSOLETE_UNQUALIFIED} and event.event_type != ADD:
        clean_label = evaluation_document_labels.get(selected.chunk.doc_id)
        if clean_label is None:
            raise RuntimeError(
                "Selected document is absent from the immutable clean evaluation lookup: "
                f"{selected.chunk.doc_id}"
            )
        clean_role, _clean_version = clean_label
        obsolete_hit = int(clean_role in {DOC_OLD, DOC_OLD_MISSING})
    return AnswerTrace(
        answer=answer,
        retrieved=retrieved,
        selected=selected,
        gold_doc_id=gold,
        evidence_hit=int(selected is not None and selected.chunk.doc_id == gold),
        obsolete_evidence_hit=obsolete_hit,
        retrieval_recall_at_k=int(gold in retrieved_ids),
    )


def method_trace(
    *,
    method: str,
    query: Query,
    event: KnowledgeEvent,
    seed: int,
    old_index: DocumentIndex,
    postevent_index: DocumentIndex,
    combined_index: DocumentIndex,
    top_k: int,
    rerank_candidate_k: int,
    stress_axis: str,
    effective_intent: str,
    evaluation_document_labels: Mapping[str, tuple[str, str]],
) -> AnswerTrace:
    rng = stable_rng("document_method_trace", method, seed, query.event_id, query.query_id)
    rewritten_prompt = rewrite_prompt_for_event(query, event, stress_axis=stress_axis)

    def old_trace() -> AnswerTrace:
        return answer_from_index(
            old_index,
            query,
            event,
            reference_state=STATE_OLD,
            gold_reference_state=STATE_POSTEVENT,
            top_k=top_k,
        )

    def postevent_trace(*, rewrite: bool = False) -> AnswerTrace:
        return answer_from_index(
            postevent_index,
            query,
            event,
            reference_state=STATE_POSTEVENT,
            gold_reference_state=STATE_POSTEVENT,
            top_k=top_k,
            prompt_override=rewritten_prompt if rewrite else None,
        )

    def version_reranked_trace(policy: str) -> AnswerTrace:
        return answer_from_version_reranked_candidates_with_intent(
            combined_index,
            query,
            event,
            policy=policy,
            stress_axis=stress_axis,
            effective_intent=effective_intent,
            evaluation_document_labels=evaluation_document_labels,
            top_k=top_k,
            rerank_candidate_k=rerank_candidate_k,
        )

    if method == "no_update":
        return old_trace()
    if method == "store_only":
        return postevent_trace()
    if method == "stale_index":
        return old_trace()
    if method == "naive_update":
        return postevent_trace()
    if method == "query_rewrite_update":
        return postevent_trace(rewrite=True)
    if method == "oldref_adapted_preserver":
        return version_reranked_trace(POLICY_OLDREF_ADAPTED)
    if method == "postevent_validity_filter":
        return version_reranked_trace(POLICY_POSTEVENT_VALIDITY)
    if method == "intent_aware_postevent_filter":
        return version_reranked_trace(POLICY_INTENT_AWARE_POSTEVENT)
    if method.startswith("old_ref_reg"):
        strength = parse_reg_strength(method, 0.70)
        if query.query_type in {TARGET, OBSOLETE_UNQUALIFIED} and event.event_type != ADD:
            return old_trace() if rng.random() < strength else postevent_trace()
        if query.query_type == HISTORICAL:
            return postevent_trace()
        return old_trace()
    if method.startswith("postevent_ref_reg"):
        strength = parse_reg_strength(method, 0.85)
        if query.query_type == HISTORICAL:
            return postevent_trace()
        if query.query_type in CURRENT_VALID_TYPES and event.event_type != ADD:
            return postevent_trace() if rng.random() < strength else old_trace()
        return postevent_trace()
    if method.startswith("old_ref_rewrite_reg"):
        strength = parse_reg_strength(method, 0.70)
        if query.query_type in {TARGET, OBSOLETE_UNQUALIFIED} and event.event_type != ADD:
            return old_trace() if rng.random() < strength else postevent_trace(rewrite=True)
        if query.query_type == HISTORICAL:
            return postevent_trace()
        return old_trace()
    if method.startswith("postevent_rewrite_reg"):
        strength = parse_reg_strength(method, 0.85)
        if query.query_type == HISTORICAL:
            return postevent_trace()
        if query.query_type in CURRENT_VALID_TYPES and event.event_type != ADD:
            return postevent_trace(rewrite=True) if rng.random() < strength else old_trace()
        return postevent_trace(rewrite=True)
    raise ValueError(f"Unknown method: {method}")


def reference_trace(
    *,
    query: Query,
    event: KnowledgeEvent,
    index: DocumentIndex,
    reference_state: str,
    top_k: int,
) -> AnswerTrace:
    return answer_from_index(
        index,
        query,
        event,
        reference_state=reference_state,
        gold_reference_state=reference_state,
        top_k=top_k,
    )


def trace_doc_ids(trace: AnswerTrace) -> str:
    return json.dumps(unique_doc_ids(trace.retrieved), sort_keys=True)


def trace_versions(trace: AnswerTrace) -> str:
    return json.dumps(unique_versions(trace.retrieved), sort_keys=True)


def row_for_query(
    *,
    run_id: str,
    seed: int,
    event: KnowledgeEvent,
    query: Query,
    method: str,
    old_index: DocumentIndex,
    postevent_index: DocumentIndex,
    combined_index: DocumentIndex,
    top_k: int,
    rerank_candidate_k: int,
    stress_axis: str,
    clean_intent: str,
    effective_intent: str,
    noise_assignment: NoiseAssignment,
    evaluation_document_labels: Mapping[str, tuple[str, str]],
) -> dict[str, Any]:
    old_reference = reference_trace(
        query=query,
        event=event,
        index=old_index,
        reference_state=STATE_OLD,
        top_k=top_k,
    )
    postevent_reference = reference_trace(
        query=query,
        event=event,
        index=postevent_index,
        reference_state=STATE_POSTEVENT,
        top_k=top_k,
    )
    updated = method_trace(
        method=method,
        query=query,
        event=event,
        seed=seed,
        old_index=old_index,
        postevent_index=postevent_index,
        combined_index=combined_index,
        top_k=top_k,
        rerank_candidate_k=rerank_candidate_k,
        stress_axis=stress_axis,
        effective_intent=effective_intent,
        evaluation_document_labels=evaluation_document_labels,
    )
    if method in {"oldref_adapted_preserver", "postevent_validity_filter", "intent_aware_postevent_filter"}:
        retrieval_prompt = expanded_prompt_for_event(
            query_retrieval_prompt(query),
            event,
            stress_axis=stress_axis,
            intent=effective_intent,
        )
    elif (
        method == "query_rewrite_update"
        or method.startswith("old_ref_rewrite_reg")
        or method.startswith("postevent_rewrite_reg")
    ):
        retrieval_prompt = rewrite_prompt_for_event(query, event, stress_axis=stress_axis)
    else:
        retrieval_prompt = query_retrieval_prompt(query)

    old_answer = old_reference.answer
    postevent_answer = postevent_reference.answer
    updated_answer = updated.answer
    current_valid = query.query_type in CURRENT_VALID_TYPES
    target = query.query_type == TARGET
    obsolete = query.query_type == OBSOLETE_UNQUALIFIED
    historical = query.query_type == HISTORICAL
    postevent_retention = exact_match(updated_answer, postevent_answer)
    return {
        "run_id": run_id,
        "dataset": "controlled_document_rag",
        "seed": seed,
        "event_id": event.event_id,
        "event_type": event.event_type,
        "query_id": query.query_id,
        "query_type": query.query_type,
        "method": method,
        "old_answer": old_answer,
        "postevent_answer": postevent_answer,
        "updated_answer": updated_answer,
        "target_success": exact_match(updated_answer, query.expected_current) if target else math.nan,
        "current_valid_accuracy": exact_match(updated_answer, query.expected_current) if current_valid else math.nan,
        "historical_accuracy": exact_match(updated_answer, query.expected_current) if historical else math.nan,
        "obsolete_answer_rate": exact_match(updated_answer, query.obsolete_answer) if obsolete else math.nan,
        "old_reference_retention": exact_match(updated_answer, old_answer),
        "postevent_retention": postevent_retention,
        "cvbd": 1.0 - postevent_retention if query.query_type == CURRENT_VALID_STABLE else math.nan,
        "total_drift": 1.0 - exact_match(updated_answer, old_answer),
        "store_response_drift": 1.0 - exact_match(postevent_answer, old_answer),
        "adaptation_side_effect": 1.0 - postevent_retention,
        "retrieved_doc_ids": trace_doc_ids(updated),
        "retrieved_versions": trace_versions(updated),
        "gold_doc_id": updated.gold_doc_id,
        "evidence_hit": updated.evidence_hit,
        "obsolete_evidence_hit": updated.obsolete_evidence_hit,
        "retrieval_recall_at_k": updated.retrieval_recall_at_k,
        "answer_source": updated.answer_source,
        "chunk_rank": updated.chunk_rank,
        "retrieval_prompt": retrieval_prompt,
        "stress_axis": stress_axis,
        "clean_intent": clean_intent,
        "effective_intent": effective_intent,
        "intent_corrupted": int(clean_intent != effective_intent),
        "noise_factor": noise_assignment.factor,
        "noise_requested_rate": noise_assignment.requested_rate,
        "noise_realized_rate": noise_assignment.realized_rate,
        "noise_assignment_hash": noise_assignment.assignment_hash,
    }


def append_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        for row in rows:
            writer.writerow(row)


class PartialProgressWriter:
    def __init__(self, output_dir: Path, *, every_queries: int, fieldnames: list[str]) -> None:
        self.output_dir = output_dir
        self.every_queries = every_queries
        self.fieldnames = fieldnames
        self.partial_path = output_dir / "rag_postevent_metrics_partial.csv"
        self.progress_path = output_dir / "progress.jsonl"
        self.pending_rows: list[dict[str, Any]] = []
        self.cumulative_queries = 0
        self.cumulative_rows = 0

    def begin(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        with self.partial_path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=self.fieldnames).writeheader()
        self.progress_path.write_text("", encoding="utf-8")

    def record_query(self, *, seed: int, query: Query, rows: list[dict[str, Any]]) -> None:
        self.pending_rows.extend(rows)
        self.cumulative_queries += 1
        self.cumulative_rows += len(rows)
        if self.cumulative_queries % self.every_queries == 0:
            self.flush(seed=seed, query=query, reason="interval")

    def finish_seed(self, *, seed: int) -> None:
        self.flush(seed=seed, query=None, reason="seed_end")

    def finish(self) -> None:
        self.flush(seed=None, query=None, reason="final")

    def flush(self, *, seed: int | None, query: Query | None, reason: str) -> None:
        if self.pending_rows:
            append_csv(self.partial_path, self.pending_rows, self.fieldnames)
            self.pending_rows = []
        record = {
            "reason": reason,
            "seed": seed,
            "event_id": query.event_id if query is not None else None,
            "query_id": query.query_id if query is not None else None,
            "cumulative_queries": self.cumulative_queries,
            "cumulative_rows": self.cumulative_rows,
        }
        with self.progress_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
        print(
            "[progress] reason={reason} seed={seed} queries={queries} rows={rows}".format(
                reason=reason,
                seed=seed,
                queries=self.cumulative_queries,
                rows=self.cumulative_rows,
            ),
            flush=True,
        )


def rows_for_seed(
    *,
    run_id: str,
    seed: int,
    clean_corpus: DocumentCorpus,
    condition_corpus: DocumentCorpus,
    clean_intents: dict[tuple[int, str], str],
    effective_intents: dict[tuple[int, str], str],
    noise_assignment: NoiseAssignment,
    methods: list[str],
    retriever: str,
    top_k: int,
    rerank_candidate_k: int,
    stress_axis: str,
    partial_writer: PartialProgressWriter | None = None,
) -> tuple[list[dict[str, Any]], DocumentCorpus]:
    old_index, postevent_index = build_indexes(clean_corpus, retriever=retriever)
    combined_index = DocumentIndex(condition_corpus.documents, retriever=retriever)
    events_by_id = {event.event_id: event for event in clean_corpus.events}
    evaluation_document_labels = {
        document.doc_id: (document.doc_role, document.version)
        for document in clean_corpus.documents
    }
    rows: list[dict[str, Any]] = []
    for query in clean_corpus.queries:
        event = events_by_id[query.event_id]
        intent_key = (seed, query.query_id)
        clean_intent = clean_intents[intent_key]
        effective_intent = effective_intents[intent_key]
        query_rows = []
        for method in methods:
            query_rows.append(
                row_for_query(
                    run_id=run_id,
                    seed=seed,
                    event=event,
                    query=query,
                    method=method,
                    old_index=old_index,
                    postevent_index=postevent_index,
                    combined_index=combined_index,
                    top_k=top_k,
                    rerank_candidate_k=rerank_candidate_k,
                    stress_axis=stress_axis,
                    clean_intent=clean_intent,
                    effective_intent=effective_intent,
                    noise_assignment=noise_assignment,
                    evaluation_document_labels=evaluation_document_labels,
                )
            )
        rows.extend(query_rows)
        if partial_writer is not None:
            partial_writer.record_query(seed=seed, query=query, rows=query_rows)
    if partial_writer is not None:
        partial_writer.finish_seed(seed=seed)
    return rows, condition_corpus


def generate_rows(
    *,
    run_id: str,
    event_count: int,
    seeds: list[int],
    methods: list[str],
    stable_queries_per_event: int,
    add_fraction: float,
    revise_fraction: float,
    retire_fraction: float,
    distractors_per_event: int,
    chunk_noise_paragraphs: int,
    retriever: str,
    top_k: int,
    rerank_candidate_k: int = 20,
    stress_axis: str = STRESS_NONE,
    noise_factor: str = CLEAN,
    noise_rate: float = 0.0,
    noise_assignment_seed: int = NOISE_ASSIGNMENT_SEED,
    output_dir: Path | None = None,
    partial_write_every_queries: int = 0,
    noise_assignment_out: list[NoiseAssignment] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    event_records: list[dict[str, Any]] = []
    query_records: list[dict[str, Any]] = []
    document_records: list[dict[str, Any]] = []
    chunk_records: list[dict[str, Any]] = []
    partial_writer = (
        PartialProgressWriter(output_dir, every_queries=partial_write_every_queries, fieldnames=CSV_FIELDS)
        if output_dir is not None and partial_write_every_queries > 0
        else None
    )
    if partial_writer is not None:
        partial_writer.begin()

    clean_corpora: dict[int, DocumentCorpus] = {}
    for seed in seeds:
        clean_corpora[seed] = build_document_corpus(
            event_count=event_count,
            seed=seed,
            stable_queries_per_event=stable_queries_per_event,
            add_fraction=add_fraction,
            revise_fraction=revise_fraction,
            retire_fraction=retire_fraction,
            distractors_per_event=distractors_per_event,
            chunk_noise_paragraphs=chunk_noise_paragraphs,
            stress_axis=stress_axis,
        )

    clean_intents = {
        (seed, query.query_id): infer_query_intent(query_retrieval_prompt(query))
        for seed, corpus in clean_corpora.items()
        for query in corpus.queries
    }
    noise_view = build_noise_view(
        clean_corpora,
        clean_intents,
        NoiseConfig(
            factor=noise_factor,
            rate=noise_rate,
            assignment_seed=noise_assignment_seed,
        ),
    )
    if noise_assignment_out is not None:
        noise_assignment_out.append(noise_view.assignment)

    for seed in seeds:
        clean_corpus = clean_corpora[seed]
        condition_corpus = noise_view.corpora_by_seed[seed]
        seed_rows, corpus = rows_for_seed(
            run_id=run_id,
            seed=seed,
            clean_corpus=clean_corpus,
            condition_corpus=condition_corpus,
            clean_intents=clean_intents,
            effective_intents=dict(noise_view.effective_intents),
            noise_assignment=noise_view.assignment,
            methods=methods,
            retriever=retriever,
            top_k=top_k,
            rerank_candidate_k=rerank_candidate_k,
            stress_axis=stress_axis,
            partial_writer=partial_writer,
        )
        rows.extend(seed_rows)
        for event in corpus.events:
            record = event_to_json(event)
            record["seed"] = seed
            event_records.append(record)
        for query in corpus.queries:
            record = query_to_json(query)
            record["seed"] = seed
            intent_key = (seed, query.query_id)
            record["clean_intent"] = clean_intents[intent_key]
            record["effective_intent"] = noise_view.effective_intents[intent_key]
            record["intent_corrupted"] = int(
                clean_intents[intent_key] != noise_view.effective_intents[intent_key]
            )
            record["noise_assignment_hash"] = noise_view.assignment.assignment_hash
            query_records.append(record)
        clean_documents = {doc.doc_id: doc for doc in clean_corpus.documents}
        for doc in corpus.documents:
            record = document_to_json(doc)
            record["seed"] = seed
            clean_doc = clean_documents[doc.doc_id]
            record["clean_version"] = clean_doc.version
            record["clean_doc_role"] = clean_doc.doc_role
            record["metadata_corrupted"] = int(
                (doc.version, doc.doc_role) != (clean_doc.version, clean_doc.doc_role)
            )
            record["noise_assignment_hash"] = noise_view.assignment.assignment_hash
            document_records.append(record)
            for chunk in chunks_for_document(doc):
                chunk_record = chunk_to_json(chunk)
                chunk_record["seed"] = seed
                chunk_record["clean_version"] = clean_doc.version
                chunk_record["clean_doc_role"] = clean_doc.doc_role
                chunk_record["metadata_corrupted"] = record["metadata_corrupted"]
                chunk_record["noise_assignment_hash"] = noise_view.assignment.assignment_hash
                chunk_records.append(chunk_record)
    if partial_writer is not None:
        partial_writer.finish()
    return rows, event_records, query_records, document_records, chunk_records


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True) + "\n")


def write_manifest(
    args: argparse.Namespace,
    rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    noise_assignment: NoiseAssignment,
) -> None:
    manifest = {
        "experiment": "rag_post_event_controlled_document_rag",
        "query_template_version": QUERY_TEMPLATE_VERSION,
        "args": vars(args),
        "row_count": len(rows),
        "event_record_count": len(events),
        "document_record_count": len(documents),
        "chunk_record_count": len(chunks),
        "schema_fields": CSV_FIELDS,
        # Compatibility contract: this remains the pre-noise dataset hash.  The
        # condition/schema addendum is recorded separately below, including for
        # clean, so legacy manifest fields project byte-for-byte to the frozen
        # pre-refactor runner.
        "dataset_spec_hash": stable_json_hash(
            {
                "event_count": args.events,
                "seeds": args.seeds,
                "stable_queries_per_event": args.stable_queries_per_event,
                "add_fraction": args.add_fraction,
                "revise_fraction": args.revise_fraction,
                "retire_fraction": args.retire_fraction,
                "distractors_per_event": args.distractors_per_event,
                "chunk_noise_paragraphs": args.chunk_noise_paragraphs,
                "retriever": args.retriever,
                "top_k": args.top_k,
                "rerank_candidate_k": args.rerank_candidate_k,
                "stress_axis": args.stress_axis,
            }
        ),
        "condition_spec_hash": stable_json_hash(
            {
                "dataset_spec_hash": stable_json_hash(
                    {
                        "event_count": args.events,
                        "seeds": args.seeds,
                        "stable_queries_per_event": args.stable_queries_per_event,
                        "add_fraction": args.add_fraction,
                        "revise_fraction": args.revise_fraction,
                        "retire_fraction": args.retire_fraction,
                        "distractors_per_event": args.distractors_per_event,
                        "chunk_noise_paragraphs": args.chunk_noise_paragraphs,
                        "retriever": args.retriever,
                        "top_k": args.top_k,
                        "rerank_candidate_k": args.rerank_candidate_k,
                        "stress_axis": args.stress_axis,
                    }
                ),
                "noise_factor": args.noise_factor,
                "noise_rate": args.noise_rate,
                "noise_assignment_seed": args.noise_assignment_seed,
                "schema_addendum_fields": DOC_FIELDS[-7:],
            }
        ),
        "schema_addendum": {
            "policy": (
                "Noise columns and manifest fields are present in every condition, including clean. "
                "Legacy equivalence projects onto the pre-refactor schema fields."
            ),
            "fields": DOC_FIELDS[-7:],
        },
        "methods": args.methods,
        "noise_assignment": noise_assignment.to_json(),
        "git": git_metadata(),
        "environment": environment_metadata(args.device),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--events", type=int, default=100)
    parser.add_argument("--seeds", type=int, nargs="+", default=[13])
    parser.add_argument(
        "--methods",
        nargs="+",
        default=DEFAULT_METHODS,
    )
    parser.add_argument("--stable-queries-per-event", type=int, default=2)
    parser.add_argument("--add-fraction", type=float, default=0.10)
    parser.add_argument("--revise-fraction", type=float, default=0.50)
    parser.add_argument("--retire-fraction", type=float, default=0.40)
    parser.add_argument("--distractors-per-event", type=int, default=2)
    parser.add_argument("--chunk-noise-paragraphs", type=int, default=0)
    parser.add_argument("--retriever", choices=["bm25", "tfidf"], default="bm25")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rerank-candidate-k", type=int, default=20)
    parser.add_argument("--stress-axis", choices=[STRESS_NONE, STRESS_LEXICAL_SCHEMA], default=STRESS_NONE)
    parser.add_argument("--noise-factor", choices=NOISE_FACTORS, default=CLEAN)
    parser.add_argument("--noise-rate", type=float, choices=SUPPORTED_RATES, default=0.0)
    parser.add_argument("--noise-assignment-seed", type=int, default=NOISE_ASSIGNMENT_SEED)
    parser.add_argument("--partial-write-every-queries", type=int, default=250)
    parser.add_argument("--device", default="cuda")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    enforce_t4_only()
    normalized_noise = NoiseConfig(
        factor=args.noise_factor,
        rate=args.noise_rate,
        assignment_seed=args.noise_assignment_seed,
    ).normalized()
    args.noise_factor = normalized_noise.factor
    args.noise_rate = normalized_noise.rate
    if not args.run_id:
        args.run_id = args.output_dir.name
    noise_assignments: list[NoiseAssignment] = []
    rows, events, queries, documents, chunks = generate_rows(
        run_id=args.run_id,
        event_count=args.events,
        seeds=args.seeds,
        methods=args.methods,
        stable_queries_per_event=args.stable_queries_per_event,
        add_fraction=args.add_fraction,
        revise_fraction=args.revise_fraction,
        retire_fraction=args.retire_fraction,
        distractors_per_event=args.distractors_per_event,
        chunk_noise_paragraphs=args.chunk_noise_paragraphs,
        retriever=args.retriever,
        top_k=args.top_k,
        rerank_candidate_k=args.rerank_candidate_k,
        stress_axis=args.stress_axis,
        noise_factor=args.noise_factor,
        noise_rate=args.noise_rate,
        noise_assignment_seed=args.noise_assignment_seed,
        output_dir=args.output_dir,
        partial_write_every_queries=args.partial_write_every_queries,
        noise_assignment_out=noise_assignments,
    )
    if len(noise_assignments) != 1:
        raise RuntimeError("Expected exactly one noise assignment per runner invocation")
    noise_assignment = noise_assignments[0]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "rag_postevent_metrics.csv", rows, CSV_FIELDS)
    if args.partial_write_every_queries <= 0:
        write_csv(args.output_dir / "rag_postevent_metrics_partial.csv", rows, CSV_FIELDS)
    write_jsonl(args.output_dir / "events.jsonl", events)
    write_jsonl(args.output_dir / "queries.jsonl", queries)
    write_jsonl(args.output_dir / "documents.jsonl", documents)
    write_jsonl(args.output_dir / "chunks.jsonl", chunks)
    (args.output_dir / "noise_assignment.json").write_text(
        json.dumps(noise_assignment.to_json(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_manifest(args, rows, events, documents, chunks, noise_assignment)
    print(f"[done] Document RAG post-event metrics: {args.output_dir / 'rag_postevent_metrics.csv'}")
    print(f"[done] Document RAG post-event manifest: {args.output_dir / 'run_manifest.json'}")


if __name__ == "__main__":
    main()
