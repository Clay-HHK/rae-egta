#!/usr/bin/env python3
"""Document-shaped backend for evolving RAG post-event experiments.

The backend intentionally remains synthetic-but-document-shaped. Every
structured event is grounded into versioned natural-language documents, chunks,
and extractive evidence spans so the runner must go through retrieval, evidence
selection, and answer extraction before it can emit the existing three-state
RAG metrics.
"""

from __future__ import annotations

import heapq
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from rae_egta.rag_post_event.data import (
    ADD,
    CURRENT_VALID_STABLE,
    HISTORICAL,
    NO_CURRENT,
    OBSOLETE_UNQUALIFIED,
    REVISE,
    RETIRE,
    TARGET,
    UNKNOWN,
    KnowledgeEvent,
    Query,
    build_dataset,
    query_retrieval_prompt,
)


DOC_OLD = "old"
DOC_OLD_MISSING = "old_missing"
DOC_CURRENT = "current"
DOC_STABLE = "stable"
DOC_DISTRACTOR = "distractor"
DOC_HISTORICAL = "historical"

STATE_OLD = "old"
STATE_POSTEVENT = "postevent"

STRESS_NONE = "none"
STRESS_LEXICAL_SCHEMA = "lexical_schema"

NO_EVIDENCE = "NO_EVIDENCE"

INTENT_HISTORICAL = "historical"
INTENT_CURRENT_EXPLICIT = "current_explicit"
INTENT_CURRENT_DEFAULT = "current_default"
INTENT_STABLE_CURRENT = "stable_current"

POLICY_OLDREF_ADAPTED = "oldref_adapted_preserver"
POLICY_POSTEVENT_VALIDITY = "postevent_validity_filter"
POLICY_INTENT_AWARE_POSTEVENT = "intent_aware_postevent_filter"

CURRENT_BONUS = 100.0
OLD_REFERENCE_BONUS = 100.0
HISTORICAL_BONUS = 100.0
OBSOLETE_PENALTY = 100.0
SOFT_CURRENT_BONUS = 50.0
SOFT_OBSOLETE_PENALTY = 50.0


@dataclass(frozen=True)
class Document:
    doc_id: str
    event_id: int
    doc_role: str
    version: str
    title: str
    text: str
    answer: str
    query_scope: str


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    doc_id: str
    event_id: int
    doc_role: str
    version: str
    paragraph_id: int
    text: str
    answer: str
    evidence_span: str


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float
    rank: int


@dataclass(frozen=True)
class AnswerTrace:
    answer: str
    retrieved: tuple[RetrievedChunk, ...]
    selected: RetrievedChunk | None
    gold_doc_id: str
    evidence_hit: int
    obsolete_evidence_hit: int
    retrieval_recall_at_k: int

    @property
    def answer_source(self) -> str:
        return self.selected.chunk.chunk_id if self.selected else NO_EVIDENCE

    @property
    def chunk_rank(self) -> int | float:
        return self.selected.rank if self.selected else math.nan


@dataclass(frozen=True)
class DocumentCorpus:
    events: tuple[KnowledgeEvent, ...]
    queries: tuple[Query, ...]
    old_documents: tuple[Document, ...]
    postevent_documents: tuple[Document, ...]

    @property
    def documents(self) -> tuple[Document, ...]:
        seen: set[str] = set()
        out: list[Document] = []
        for doc in [*self.old_documents, *self.postevent_documents]:
            if doc.doc_id in seen:
                continue
            seen.add(doc.doc_id)
            out.append(doc)
        return tuple(out)


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def parse_canonical_answer(text: str) -> str:
    match = re.search(r"Canonical answer:\s*([A-Za-z0-9_]+)", text)
    return match.group(1) if match else ""


def doc_id_for_event(event: KnowledgeEvent, role: str, stable_idx: int | None = None) -> str:
    prefix = f"e{event.event_id:04d}"
    if role == DOC_STABLE:
        if stable_idx is None:
            raise ValueError("stable_idx is required for stable documents")
        return f"{prefix}_stable_{stable_idx}"
    return f"{prefix}_{role}"


def alias_subject(event: KnowledgeEvent) -> str:
    return f"postevent_profile_{event.event_id:04d}"


def alias_attribute(event: KnowledgeEvent) -> str:
    return f"release_signal_{event.event_id:04d}"


def uses_lexical_schema_shift(event: KnowledgeEvent, *, stress_axis: str) -> bool:
    return stress_axis == STRESS_LEXICAL_SCHEMA and event.event_type in {REVISE, RETIRE}


def rewrite_prompt_for_event(query: Query, event: KnowledgeEvent, *, stress_axis: str) -> str:
    if not uses_lexical_schema_shift(event, stress_axis=stress_axis):
        return query_retrieval_prompt(query)
    if query.query_type not in {TARGET, OBSOLETE_UNQUALIFIED}:
        return query_retrieval_prompt(query)
    return (
        f"What is the {alias_attribute(event)} of {alias_subject(event)} "
        "in the post-event release notes?"
    )


def infer_query_intent(prompt: str) -> str:
    lowered = prompt.lower()
    historical_cues = (
        "before the update",
        "previously",
        "old version",
        "formerly",
        "prior to",
        "prior version",
        "historical",
    )
    if any(cue in lowered for cue in historical_cues):
        return INTENT_HISTORICAL
    if "stable fact" in lowered:
        return INTENT_STABLE_CURRENT
    current_cue_patterns = (
        r"\bcurrent\b",
        r"\blatest\b",
        r"\bnow\b",
        r"\btoday\b",
        r"\bcurrent version\b",
    )
    if any(re.search(pattern, lowered) for pattern in current_cue_patterns):
        return INTENT_CURRENT_EXPLICIT
    return INTENT_CURRENT_DEFAULT


def expanded_prompt_for_event(prompt: str, event: KnowledgeEvent, *, stress_axis: str, intent: str) -> str:
    if not uses_lexical_schema_shift(event, stress_axis=stress_axis):
        return prompt
    if intent in {INTENT_HISTORICAL, INTENT_STABLE_CURRENT}:
        return prompt
    return (
        f"{prompt} {alias_subject(event)} {alias_attribute(event)} "
        "post-event release notes"
    )


def _doc(
    *,
    event: KnowledgeEvent,
    doc_role: str,
    version: str,
    title: str,
    paragraphs: list[str],
    answer: str,
    query_scope: str,
) -> Document:
    return Document(
        doc_id=doc_id_for_event(event, doc_role),
        event_id=event.event_id,
        doc_role=doc_role,
        version=version,
        title=title,
        text="\n\n".join(paragraphs),
        answer=answer,
        query_scope=query_scope,
    )


def old_fact_document(event: KnowledgeEvent) -> Document:
    role = DOC_OLD_MISSING if event.event_type == ADD else DOC_OLD
    if event.event_type == ADD:
        evidence = (
            f"Evidence: Before the addition, {event.subject} had no recorded "
            f"{event.attribute}. Canonical answer: {UNKNOWN}."
        )
    else:
        evidence = (
            f"Evidence: In the old version, {event.subject} {event.attribute} "
            f"was {event.old_answer}. Canonical answer: {event.old_answer}."
        )
    return _doc(
        event=event,
        doc_role=role,
        version=STATE_OLD,
        title=f"Old record for {event.subject}",
        paragraphs=[
            f"This old-version document tracks {event.attribute} for {event.subject}.",
            evidence,
        ],
        answer=event.old_answer,
        query_scope="current_at_old_snapshot",
    )


def current_fact_document(event: KnowledgeEvent, *, stress_axis: str = STRESS_NONE) -> Document:
    if uses_lexical_schema_shift(event, stress_axis=stress_axis):
        subject = alias_subject(event)
        attribute = alias_attribute(event)
        if event.event_type == RETIRE:
            evidence = (
                f"Evidence: The versioned release note retires {subject} "
                f"{attribute}; no current valid answer exists. "
                f"Canonical answer: {NO_CURRENT}."
            )
        else:
            evidence = (
                f"Evidence: In the versioned release note, {subject} "
                f"{attribute} is {event.new_answer}. "
                f"Canonical answer: {event.new_answer}."
            )
        return _doc(
            event=event,
            doc_role=DOC_CURRENT,
            version=STATE_POSTEVENT,
            title=f"Versioned release note for {subject}",
            paragraphs=[
                f"This post-event document uses the updated schema field {attribute}.",
                evidence,
            ],
            answer=event.new_answer,
            query_scope="current_valid_lexical_schema",
        )

    if event.event_type == RETIRE:
        evidence = (
            f"Evidence: The post-event record retires {event.subject} "
            f"{event.attribute}; no current valid answer exists. "
            f"Canonical answer: {NO_CURRENT}."
        )
    else:
        evidence = (
            f"Evidence: In the post-event version, {event.subject} "
            f"{event.attribute} is {event.new_answer}. "
            f"Canonical answer: {event.new_answer}."
        )
    return _doc(
        event=event,
        doc_role=DOC_CURRENT,
        version=STATE_POSTEVENT,
        title=f"Current record for {event.subject}",
        paragraphs=[
            f"This post-event document is the current-valid record for {event.subject}.",
            evidence,
        ],
        answer=event.new_answer,
        query_scope="current_valid",
    )


def stable_documents(event: KnowledgeEvent) -> tuple[Document, ...]:
    docs = []
    for idx, fact in enumerate(event.stable_facts):
        docs.append(
            Document(
                doc_id=doc_id_for_event(event, DOC_STABLE, idx),
                event_id=event.event_id,
                doc_role=DOC_STABLE,
                version="stable",
                title=f"Stable fact {idx} for {event.subject}",
                text="\n\n".join(
                    [
                        f"This stable document is unaffected by the target event for {event.subject}.",
                        (
                            f"Evidence: Stable fact {idx} for {event.subject} is "
                            f"{fact.answer}. Canonical answer: {fact.answer}."
                        ),
                    ]
                ),
                answer=fact.answer,
                query_scope="current_valid_stable",
            )
        )
    return tuple(docs)


def distractor_documents(event: KnowledgeEvent, *, distractors_per_event: int) -> tuple[Document, ...]:
    docs: list[Document] = []
    for idx in range(distractors_per_event):
        answer = f"{event.subject}_distractor_status_{idx}"
        doc_id = f"e{event.event_id:04d}_{DOC_DISTRACTOR}_{idx}"
        docs.append(
            Document(
                doc_id=doc_id,
                event_id=event.event_id,
                doc_role=DOC_DISTRACTOR,
                version=STATE_POSTEVENT,
                title=f"Distractor {idx} near {event.subject}",
                text="\n\n".join(
                    [
                        (
                            f"This document mentions {event.subject} and {event.attribute}, "
                            f"but it is about a neighboring record."
                        ),
                        (
                            f"Evidence: The neighboring record {event.subject}_peer_{idx} "
                            f"{event.attribute} is {answer}. Canonical answer: {answer}."
                        ),
                    ]
                ),
                answer=answer,
                query_scope="distractor",
            )
        )
    return tuple(docs)


def historical_document(event: KnowledgeEvent) -> Document | None:
    if event.event_type not in {RETIRE, REVISE}:
        return None
    return _doc(
        event=event,
        doc_role=DOC_HISTORICAL,
        version="historical_archive",
        title=f"Historical archive for {event.subject}",
        paragraphs=[
            f"This archive is only for explicitly time-qualified historical questions about {event.subject}.",
            (
                f"Evidence: Before the update, {event.subject} {event.attribute} "
                f"was {event.old_answer}. Canonical answer: {event.old_answer}."
            ),
        ],
        answer=event.old_answer,
        query_scope="historical_only",
    )


def noise_document(event: KnowledgeEvent, idx: int) -> Document:
    doc_id = f"e{event.event_id:04d}_chunk_noise_{idx}"
    answer = f"{event.subject}_noise_status_{idx}"
    return Document(
        doc_id=doc_id,
        event_id=event.event_id,
        doc_role=DOC_DISTRACTOR,
        version="noise",
        title=f"Noisy paragraph {idx} near {event.subject}",
        text="\n\n".join(
            [
                (
                    f"Background note {idx}: {event.subject} may appear near terms "
                    f"such as current, previous, status, and update."
                ),
                (
                    f"Evidence: A non-target note for {event.subject}_noise_{idx} "
                    f"{event.attribute} is {answer}. Canonical answer: {answer}."
                ),
            ]
        ),
        answer=answer,
        query_scope="noise",
    )


def documents_for_event(
    event: KnowledgeEvent,
    *,
    distractors_per_event: int = 2,
    chunk_noise_paragraphs: int = 0,
    stress_axis: str = STRESS_NONE,
) -> tuple[tuple[Document, ...], tuple[Document, ...]]:
    old_doc = old_fact_document(event)
    current_doc = current_fact_document(event, stress_axis=stress_axis)
    stable = stable_documents(event)
    distractors = distractor_documents(event, distractors_per_event=distractors_per_event)
    noise = tuple(noise_document(event, idx) for idx in range(chunk_noise_paragraphs))
    hist = historical_document(event)

    old_docs = (old_doc, *stable, *distractors, *noise)
    post_docs = (current_doc, *stable, *distractors, *noise)
    if hist is not None:
        post_docs = (*post_docs, hist)
    return old_docs, post_docs


def build_document_corpus(
    *,
    event_count: int,
    seed: int,
    stable_queries_per_event: int = 2,
    add_fraction: float = 0.10,
    revise_fraction: float = 0.50,
    retire_fraction: float = 0.40,
    distractors_per_event: int = 2,
    chunk_noise_paragraphs: int = 0,
    stress_axis: str = STRESS_NONE,
) -> DocumentCorpus:
    events, queries = build_dataset(
        event_count=event_count,
        seed=seed,
        stable_queries_per_event=stable_queries_per_event,
        add_fraction=add_fraction,
        revise_fraction=revise_fraction,
        retire_fraction=retire_fraction,
    )
    old_docs: list[Document] = []
    post_docs: list[Document] = []
    for event in events:
        event_old, event_post = documents_for_event(
            event,
            distractors_per_event=distractors_per_event,
            chunk_noise_paragraphs=chunk_noise_paragraphs,
            stress_axis=stress_axis,
        )
        old_docs.extend(event_old)
        post_docs.extend(event_post)
    return DocumentCorpus(
        events=tuple(events),
        queries=tuple(queries),
        old_documents=tuple(old_docs),
        postevent_documents=tuple(post_docs),
    )


def chunks_for_document(doc: Document) -> tuple[Chunk, ...]:
    chunks = []
    for idx, paragraph in enumerate(doc.text.split("\n\n")):
        answer = parse_canonical_answer(paragraph)
        chunks.append(
            Chunk(
                chunk_id=f"{doc.doc_id}_chunk_{idx}",
                doc_id=doc.doc_id,
                event_id=doc.event_id,
                doc_role=doc.doc_role,
                version=doc.version,
                paragraph_id=idx,
                text=f"{doc.title}. {paragraph}",
                answer=answer,
                evidence_span=paragraph if answer else "",
            )
        )
    return tuple(chunks)


class DocumentIndex:
    def __init__(self, documents: tuple[Document, ...], *, retriever: str = "bm25") -> None:
        self.documents = documents
        self.retriever = retriever
        self.chunks = tuple(chunk for doc in documents for chunk in chunks_for_document(doc))
        self.chunks_by_doc = {
            doc.doc_id: chunks_for_document(doc)
            for doc in documents
        }
        self._retrieve_cache: dict[tuple[str, int], tuple[RetrievedChunk, ...]] = {}
        self._tokens = [tokenize(chunk.text) for chunk in self.chunks]
        self._term_counts = [Counter(tokens) for tokens in self._tokens]
        self._lengths = [len(tokens) for tokens in self._tokens]
        self._doc_freq = self._document_frequency(self._tokens)
        self._avg_len = sum(len(tokens) for tokens in self._tokens) / max(1, len(self._tokens))
        postings: dict[str, list[int]] = defaultdict(list)
        for chunk_idx, tokens in enumerate(self._tokens):
            for term in set(tokens):
                postings[term].append(chunk_idx)
        self._postings = {term: tuple(indices) for term, indices in postings.items()}
        self._chunk_indices_by_id = tuple(sorted(range(len(self.chunks)), key=lambda idx: self.chunks[idx].chunk_id))

    @staticmethod
    def _document_frequency(token_lists: list[list[str]]) -> Counter[str]:
        counts: Counter[str] = Counter()
        for tokens in token_lists:
            counts.update(set(tokens))
        return counts

    def _idf(self, term: str) -> float:
        total = len(self._tokens)
        df = self._doc_freq.get(term, 0)
        return math.log(1.0 + (total - df + 0.5) / (df + 0.5))

    def _bm25_score(self, query_terms: list[str], term_counts: Counter[str], chunk_len: int) -> float:
        if chunk_len <= 0:
            return 0.0
        k1 = 1.5
        b = 0.75
        length_norm = 1.0 - b + b * chunk_len / max(self._avg_len, 1.0)
        score = 0.0
        for term in query_terms:
            tf = term_counts.get(term, 0)
            if tf <= 0:
                continue
            score += self._idf(term) * (tf * (k1 + 1.0)) / (tf + k1 * length_norm)
        return score

    def _tfidf_score(self, query_counts: Counter[str], term_counts: Counter[str], chunk_len: int) -> float:
        if chunk_len <= 0:
            return 0.0
        return sum(query_tf * term_counts.get(term, 0) * self._idf(term) for term, query_tf in query_counts.items())

    def _score_chunk(self, chunk_idx: int, query_terms: list[str], query_counts: Counter[str]) -> float:
        term_counts = self._term_counts[chunk_idx]
        chunk_len = self._lengths[chunk_idx]
        if self.retriever == "tfidf":
            return self._tfidf_score(query_counts, term_counts, chunk_len)
        if self.retriever == "bm25":
            return self._bm25_score(query_terms, term_counts, chunk_len)
        raise ValueError(f"Unknown retriever: {self.retriever}")

    def _rank_scored(self, scored: list[tuple[float, str, Chunk]], *, top_k: int) -> tuple[RetrievedChunk, ...]:
        if top_k <= 0:
            return ()
        ranked = heapq.nsmallest(top_k, scored, key=lambda item: (-item[0], item[1]))
        return tuple(RetrievedChunk(chunk=chunk, score=score, rank=rank) for rank, (score, _, chunk) in enumerate(ranked, 1))

    def retrieve_full_scan(self, prompt: str, *, top_k: int) -> tuple[RetrievedChunk, ...]:
        """Reference implementation for tests: score every chunk, then rank."""
        if top_k <= 0:
            return ()
        query_terms = tokenize(prompt)
        query_counts = Counter(query_terms)
        scored = []
        for chunk_idx, chunk in enumerate(self.chunks):
            score = self._score_chunk(chunk_idx, query_terms, query_counts)
            scored.append((score, chunk.chunk_id, chunk))
        return self._rank_scored(scored, top_k=top_k)

    def _candidate_indices(self, query_terms: list[str]) -> tuple[int, ...]:
        seen: set[int] = set()
        out: list[int] = []
        for term in query_terms:
            for chunk_idx in self._postings.get(term, ()):
                if chunk_idx in seen:
                    continue
                seen.add(chunk_idx)
                out.append(chunk_idx)
        return tuple(out)

    def retrieve(self, prompt: str, *, top_k: int) -> tuple[RetrievedChunk, ...]:
        cache_key = (prompt, top_k)
        if cache_key in self._retrieve_cache:
            return self._retrieve_cache[cache_key]
        if top_k <= 0:
            self._retrieve_cache[cache_key] = ()
            return ()
        query_terms = tokenize(prompt)
        query_counts = Counter(query_terms)
        candidate_indices = self._candidate_indices(query_terms)
        scored = [
            (self._score_chunk(chunk_idx, query_terms, query_counts), self.chunks[chunk_idx].chunk_id, self.chunks[chunk_idx])
            for chunk_idx in candidate_indices
        ]
        needed = min(top_k, len(self.chunks))
        if len(candidate_indices) < needed:
            candidate_set = set(candidate_indices)
            for chunk_idx in self._chunk_indices_by_id:
                if chunk_idx in candidate_set:
                    continue
                chunk = self.chunks[chunk_idx]
                scored.append((0.0, chunk.chunk_id, chunk))
                if len(scored) >= needed:
                    break
        retrieved = self._rank_scored(scored, top_k=top_k)
        self._retrieve_cache[cache_key] = retrieved
        return retrieved


def build_indexes(corpus: DocumentCorpus, *, retriever: str = "bm25") -> tuple[DocumentIndex, DocumentIndex]:
    return (
        DocumentIndex(corpus.old_documents, retriever=retriever),
        DocumentIndex(corpus.postevent_documents, retriever=retriever),
    )


def gold_doc_id(query: Query, event: KnowledgeEvent, *, reference_state: str) -> str:
    if query.query_type == CURRENT_VALID_STABLE:
        idx = int(query.query_id.rsplit("_", 1)[1])
        return doc_id_for_event(event, DOC_STABLE, idx)
    if query.query_type == HISTORICAL:
        return doc_id_for_event(event, DOC_OLD if reference_state == STATE_OLD else DOC_HISTORICAL)
    if reference_state == STATE_OLD:
        role = DOC_OLD_MISSING if event.event_type == ADD else DOC_OLD
        return doc_id_for_event(event, role)
    return doc_id_for_event(event, DOC_CURRENT)


def allowed_roles(query: Query, *, reference_state: str) -> set[str]:
    if query.query_type == CURRENT_VALID_STABLE:
        return {DOC_STABLE}
    if query.query_type == HISTORICAL:
        return {DOC_OLD, DOC_HISTORICAL}
    if query.query_type in {TARGET, OBSOLETE_UNQUALIFIED}:
        if reference_state == STATE_OLD:
            return {DOC_OLD, DOC_OLD_MISSING}
        return {DOC_CURRENT}
    return {DOC_CURRENT}


def unique_doc_ids(retrieved: tuple[RetrievedChunk, ...]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in retrieved:
        doc_id = item.chunk.doc_id
        if doc_id in seen:
            continue
        seen.add(doc_id)
        out.append(doc_id)
    return out


def unique_versions(retrieved: tuple[RetrievedChunk, ...]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in retrieved:
        version = item.chunk.version
        if version in seen:
            continue
        seen.add(version)
        out.append(version)
    return out


def _is_current_evidence(chunk: Chunk) -> bool:
    return (
        (chunk.version == STATE_POSTEVENT and chunk.doc_role == DOC_CURRENT)
        or chunk.version == "stable"
        or chunk.doc_role == DOC_STABLE
    )


def _is_old_evidence(chunk: Chunk) -> bool:
    return chunk.version == STATE_OLD and chunk.doc_role in {DOC_OLD, DOC_OLD_MISSING}


def _is_historical_evidence(chunk: Chunk) -> bool:
    return chunk.version == "historical_archive" or chunk.doc_role == DOC_HISTORICAL


def _rerank_bonus(chunk: Chunk, *, intent: str, policy: str) -> float:
    is_current = _is_current_evidence(chunk)
    is_old = _is_old_evidence(chunk)
    is_historical = _is_historical_evidence(chunk)

    if intent == INTENT_STABLE_CURRENT:
        return CURRENT_BONUS if chunk.doc_role == DOC_STABLE else 0.0

    if policy == POLICY_OLDREF_ADAPTED:
        if intent == INTENT_HISTORICAL:
            return HISTORICAL_BONUS if is_old or is_historical else 0.0
        if intent == INTENT_CURRENT_EXPLICIT:
            return CURRENT_BONUS if is_current else 0.0
        return OLD_REFERENCE_BONUS if is_old or is_historical else 0.0

    if policy == POLICY_POSTEVENT_VALIDITY:
        if intent == INTENT_HISTORICAL:
            return HISTORICAL_BONUS if is_old or is_historical else 0.0
        if is_current:
            return CURRENT_BONUS
        if is_old or is_historical:
            return -OBSOLETE_PENALTY
        return 0.0

    if policy == POLICY_INTENT_AWARE_POSTEVENT:
        if intent == INTENT_HISTORICAL:
            return HISTORICAL_BONUS if is_old or is_historical else 0.0
        if intent == INTENT_CURRENT_DEFAULT:
            if is_current:
                return SOFT_CURRENT_BONUS
            if is_old or is_historical:
                return -SOFT_OBSOLETE_PENALTY
            return 0.0
        if is_current:
            return CURRENT_BONUS
        if is_old or is_historical:
            return -OBSOLETE_PENALTY
        return 0.0

    raise ValueError(f"Unknown version rerank policy: {policy}")


def answer_from_version_reranked_candidates(
    index: DocumentIndex,
    query: Query,
    event: KnowledgeEvent,
    *,
    policy: str,
    stress_axis: str,
    top_k: int = 5,
    rerank_candidate_k: int = 20,
) -> AnswerTrace:
    retrieval_prompt = query_retrieval_prompt(query)
    intent = infer_query_intent(retrieval_prompt)
    prompt = expanded_prompt_for_event(
        retrieval_prompt,
        event,
        stress_axis=stress_axis,
        intent=intent,
    )
    candidate_k = max(top_k, rerank_candidate_k)
    candidates = index.retrieve(prompt, top_k=candidate_k)
    rescored: list[tuple[float, str, RetrievedChunk]] = []
    for item in candidates:
        final_score = item.score + _rerank_bonus(item.chunk, intent=intent, policy=policy)
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
        evidence_chunk = next((chunk for chunk in index.chunks_by_doc[item.chunk.doc_id] if chunk.answer), None)
        if evidence_chunk is not None:
            selected = RetrievedChunk(chunk=evidence_chunk, score=item.score, rank=item.rank)
            break

    answer = selected.chunk.answer if selected else UNKNOWN
    gold = gold_doc_id(query, event, reference_state=STATE_POSTEVENT)
    retrieved_ids = unique_doc_ids(retrieved)
    obsolete_hit = 0
    if selected and query.query_type in {TARGET, OBSOLETE_UNQUALIFIED} and event.event_type != ADD:
        obsolete_hit = int(selected.chunk.doc_role in {DOC_OLD, DOC_OLD_MISSING})
    return AnswerTrace(
        answer=answer,
        retrieved=retrieved,
        selected=selected,
        gold_doc_id=gold,
        evidence_hit=int(selected is not None and selected.chunk.doc_id == gold),
        obsolete_evidence_hit=obsolete_hit,
        retrieval_recall_at_k=int(gold in retrieved_ids),
    )


def answer_from_index(
    index: DocumentIndex,
    query: Query,
    event: KnowledgeEvent,
    *,
    reference_state: str,
    gold_reference_state: str,
    top_k: int = 5,
    prompt_override: str | None = None,
) -> AnswerTrace:
    retrieved = index.retrieve(prompt_override or query_retrieval_prompt(query), top_k=top_k)
    roles = allowed_roles(query, reference_state=reference_state)
    selected = None
    for item in retrieved:
        if item.chunk.doc_role not in roles:
            continue
        if item.chunk.answer:
            selected = item
            break
        evidence_chunk = next((chunk for chunk in index.chunks_by_doc[item.chunk.doc_id] if chunk.answer), None)
        if evidence_chunk is not None:
            selected = RetrievedChunk(chunk=evidence_chunk, score=item.score, rank=item.rank)
            break
    answer = selected.chunk.answer if selected else UNKNOWN
    gold = gold_doc_id(query, event, reference_state=gold_reference_state)
    retrieved_ids = unique_doc_ids(retrieved)
    obsolete_hit = 0
    if selected and query.query_type in {TARGET, OBSOLETE_UNQUALIFIED} and event.event_type != ADD:
        obsolete_hit = int(selected.chunk.doc_role in {DOC_OLD, DOC_OLD_MISSING})
    return AnswerTrace(
        answer=answer,
        retrieved=retrieved,
        selected=selected,
        gold_doc_id=gold,
        evidence_hit=int(selected is not None and selected.chunk.doc_id == gold),
        obsolete_evidence_hit=obsolete_hit,
        retrieval_recall_at_k=int(gold in retrieved_ids),
    )


def document_to_json(doc: Document) -> dict[str, Any]:
    return asdict(doc)


def chunk_to_json(chunk: Chunk) -> dict[str, Any]:
    return asdict(chunk)
