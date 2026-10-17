#!/usr/bin/env python3
"""Run the frozen controlled document corpus with an injected reader.

The module reuses ``run_document.method_trace`` for all six registered method
contexts.  Reading is the only replaced stage: callers inject a frozen Qwen,
MiniMax, or Grok adapter, the deterministic extractive control, or a fake
reader in unit tests.  Importing this module performs no model loading, network
access, cache creation, or experiment execution.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import platform
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Protocol

from rae_egta.common.provenance import environment_metadata, git_metadata, stable_json_hash
from rae_egta.rag_post_event.data import (
    CURRENT_VALID_STABLE,
    HISTORICAL,
    OBSOLETE_UNQUALIFIED,
    QUERY_TEMPLATE_VERSION,
    TARGET,
    UNKNOWN,
    KnowledgeEvent,
    Query,
    event_to_json,
    query_reader_prompt,
    query_retrieval_prompt,
    query_to_json,
)
from rae_egta.rag_post_event.dense_retriever import (
    DENSE_RETRIEVER_VERSION,
    QWEN3_EMBEDDING_MODEL,
    DenseDocumentIndex,
    EmbeddingEncoder,
    Qwen3EmbeddingEncoder,
)
from rae_egta.rag_post_event.document_backend import (
    STRESS_LEXICAL_SCHEMA,
    STRESS_NONE,
    DocumentCorpus,
    DocumentIndex,
    RetrievedChunk,
    build_document_corpus,
    chunks_for_document,
    expanded_prompt_for_event,
    infer_query_intent,
    rewrite_prompt_for_event,
    unique_doc_ids,
    unique_versions,
)
from rae_egta.rag_post_event.llm_readers import (
    GROK_READER,
    MINIMAX_READER,
    PROMPT_POLICY_VERSION,
    QWEN_READER,
    ReaderResponse,
    build_reader,
    reader_messages,
    stable_request_payload,
)
from rae_egta.rag_post_event.llm_scoring import (
    ABSTAIN,
    REFUSAL_OR_UNPARSEABLE,
    score_reader_output,
    validate_non_overlapping_canonicals,
)
from rae_egta.rag_post_event.run_document import method_trace
from rae_egta.rag_post_event.subsample import (
    DEFAULT_PILOT_HOLDOUT_OFFSET,
    FORMAL_EVENT_COUNTS,
    FORMAL_SEEDS,
    PILOT_SEED,
    SUBSAMPLE_PROTOCOL_VERSION,
    SubsampleSelection,
    select_headroom_pilot,
    select_subsample,
)


RUNNER_PROTOCOL_VERSION = "controlled_document_llm_v3"
PILOT_GATE_PROTOCOL_VERSION = "controlled_document_llm_pilot_gate_v3"
DENSE_MODEL_SNAPSHOT = "97b0c614"
DETERMINISTIC_EXTRACTIVE_READER = "deterministic_extractive"
EXPLORATORY_NOTE = (
    "EXPLORATORY pilot-informed run; not pre-registered CL closure; "
    "gate query_rewrite_target_success failed at pilot"
)
LOGGER = logging.getLogger(__name__)

REGISTERED_METHODS = (
    "no_update",
    "store_only",
    "query_rewrite_update",
    "oldref_adapted_preserver",
    "postevent_validity_filter",
    "intent_aware_postevent_filter",
)
PILOT_METHODS = (
    "no_update",
    "store_only",
    "query_rewrite_update",
    "postevent_validity_filter",
)
SUPPORTED_RETRIEVERS = ("bm25", "dense")
PILOT_QUERY_COUNT = 58
PILOT_READER_CELL_COUNT = PILOT_QUERY_COUNT * len(PILOT_METHODS)
PILOT_GATE_NAMES = (
    "parse_success_rate",
    "query_rewrite_target_success",
    "store_only_shifted_target_success",
    "no_update_obsolete_rate",
)
FORMAL_RETRIEVER_MATRIX = {
    QWEN_READER: SUPPORTED_RETRIEVERS,
    MINIMAX_READER: ("bm25",),
    GROK_READER: ("bm25",),
    DETERMINISTIC_EXTRACTIVE_READER: SUPPORTED_RETRIEVERS,
}

CSV_FIELDS = [
    "run_id",
    "dataset",
    "protocol_version",
    "exploratory",
    "query_template_version",
    "holdout_offset",
    "subsample_hash",
    "config_hash",
    "seed",
    "event_id",
    "event_type",
    "query_id",
    "query_type",
    "reader_id",
    "model_requested",
    "model_returned",
    "retriever",
    "method",
    "raw_reader_output",
    "normalized_reader_output",
    "parsed_answer",
    "scoring_class",
    "correct_canonical_contained",
    "conflicting_canonical_contained",
    "correct_canonical_answer",
    "conflicting_canonical_answer",
    "old_canonical_answer",
    "new_canonical_answer",
    "clean_event_type",
    "effective_event_type",
    "clean_query_prompt",
    "effective_reader_query",
    "effective_retrieval_prompt",
    "clean_intent",
    "effective_intent",
    "intent_corrupted",
    "clean_gold_doc_id",
    "effective_retrieved_doc_ids",
    "effective_retrieved_chunk_ids",
    "effective_retrieved_versions",
    "effective_retrieval_scores",
    "effective_context_sha256",
    "effective_retrieval_recall_at_k",
    "effective_evidence_hit",
    "effective_obsolete_evidence_hit",
    "clean_record_sha256",
    "effective_provenance_sha256",
    "reader_prompt_sha256",
    "reader_cache_hit",
    "reader_latency_seconds",
    "reader_base_url",
    "reader_prompt_policy_version",
    "answer_source",
    "chunk_rank",
    "stress_axis",
]


class ReaderLike(Protocol):
    """The existing frozen reader contract used by the controlled runner."""

    reader_family: str

    def answer(
        self,
        *,
        query: str,
        retrieved: tuple[RetrievedChunk, ...],
    ) -> Any: ...


@dataclass(frozen=True)
class RunnerConfig:
    """Scientific and substrate configuration for one reader arm."""

    run_id: str
    mode: str = "formal"
    seeds: tuple[int, ...] = FORMAL_SEEDS
    methods: tuple[str, ...] = REGISTERED_METHODS
    retrievers: tuple[str, ...] = SUPPORTED_RETRIEVERS
    source_events_per_seed: int = 1500
    source_stable_queries_per_event: int = 2
    add_fraction: float = 0.10
    revise_fraction: float = 0.50
    retire_fraction: float = 0.40
    distractors_per_event: int = 2
    chunk_noise_paragraphs: int = 0
    top_k: int = 5
    rerank_candidate_k: int = 20
    stress_axis: str = STRESS_LEXICAL_SCHEMA
    dense_model_id: str = QWEN3_EMBEDDING_MODEL
    dense_model_snapshot: str = DENSE_MODEL_SNAPSHOT
    holdout_offset: int = 0
    device: str = "cuda"
    exploratory: bool = False

    def validate(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id must be non-empty")
        if self.mode not in {"pilot", "formal", "test"}:
            raise ValueError(f"Unsupported mode: {self.mode}")
        if self.exploratory and self.mode != "formal":
            raise ValueError("exploratory mode is only valid for formal runs")
        if not self.seeds:
            raise ValueError("At least one seed is required")
        if len(self.methods) != len(set(self.methods)):
            raise ValueError("methods must not contain duplicates")
        unknown_methods = sorted(set(self.methods).difference(REGISTERED_METHODS))
        if unknown_methods:
            raise ValueError(f"Unregistered methods: {unknown_methods}")
        if len(self.retrievers) != len(set(self.retrievers)):
            raise ValueError("retrievers must not contain duplicates")
        unknown_retrievers = sorted(set(self.retrievers).difference(SUPPORTED_RETRIEVERS))
        if unknown_retrievers:
            raise ValueError(f"Unsupported retrievers: {unknown_retrievers}")
        if self.source_events_per_seed <= 0 or self.source_stable_queries_per_event < 2:
            raise ValueError("Source corpus must have positive events and at least two stable queries")
        if self.top_k <= 0 or self.rerank_candidate_k <= 0:
            raise ValueError("top_k and rerank_candidate_k must be positive")
        if self.stress_axis not in {STRESS_NONE, STRESS_LEXICAL_SCHEMA}:
            raise ValueError(f"Unsupported stress axis: {self.stress_axis}")
        if self.holdout_offset < 0:
            raise ValueError("holdout_offset must be non-negative")
        if self.mode == "pilot" and self.holdout_offset == 0:
            raise ValueError("CL2 pilot requires a fresh positive holdout_offset")
        if self.mode != "pilot" and self.holdout_offset != 0:
            raise ValueError("holdout_offset is only valid in pilot mode")
        if "dense" in self.retrievers and self.dense_model_snapshot != DENSE_MODEL_SNAPSHOT:
            raise ValueError(
                f"Block CL freezes the dense model snapshot to {DENSE_MODEL_SNAPSHOT}"
            )


@dataclass(frozen=True)
class _PreparedReaderCell:
    """One canonically ordered cell with retrieval already completed."""

    seed: int
    event: KnowledgeEvent
    query: Query
    method: str
    retriever: str
    trace: Any
    clean_intent: str
    effective_intent: str


class DeterministicExtractiveReader:
    """Same-substrate deterministic control behind the reader interface."""

    reader_family = DETERMINISTIC_EXTRACTIVE_READER
    model_id = "deterministic_extractive_v1"

    def answer(
        self,
        *,
        query: str,
        retrieved: tuple[RetrievedChunk, ...],
    ) -> ReaderResponse:
        messages = reader_messages(query=query, retrieved=retrieved)
        request_payload = stable_request_payload(
            reader_family=self.reader_family,
            model_id=self.model_id,
            messages=messages,
            temperature=0.0,
            max_tokens=64,
            extra_body={},
        )
        answer = next((item.chunk.answer for item in retrieved if item.chunk.answer), UNKNOWN)
        return ReaderResponse(
            answer=answer,
            reader_family=self.reader_family,
            model_requested=self.model_id,
            model_returned=self.model_id,
            prompt_sha256=hashlib.sha256(request_payload.encode("utf-8")).hexdigest(),
            cache_hit=False,
            latency_seconds=0.0,
            base_url="",
            prompt_policy_version=PROMPT_POLICY_VERSION,
        )

    def manifest(self) -> dict[str, object]:
        return {
            "reader_family": self.reader_family,
            "model_id": self.model_id,
            "temperature": 0.0,
            "max_tokens": 64,
            "prompt_policy_version": PROMPT_POLICY_VERSION,
            "cache_backend": "none",
        }


class _MethodCompatibleDenseIndex:
    """Read-only compatibility view for ``run_document.method_trace``.

    The frozen dense index exposes retrieval and chunks but not the
    ``chunks_by_doc`` lookup used by the shared extractive trace builder.  This
    local view supplies that lookup without changing either frozen module.
    """

    def __init__(self, index: DenseDocumentIndex) -> None:
        self._index = index
        self.documents = index.documents
        self.chunks = index.chunks
        self.chunks_by_doc = {
            document.doc_id: chunks_for_document(document)
            for document in index.documents
        }

    def retrieve(self, prompt: str, *, top_k: int) -> tuple[RetrievedChunk, ...]:
        return self._index.retrieve(prompt, top_k=top_k)

    def manifest(self) -> dict[str, object]:
        return self._index.manifest()


def enforce_t4_only() -> None:
    """Apply the same fail-closed macOS convention as existing RAG runners."""

    if platform.system() == "Darwin" and os.environ.get("ALLOW_LOCAL_RAG_DOCUMENT_LLM_RUN") != "1":
        raise SystemExit(
            "Refusing to run controlled document LLM experiments on local macOS. "
            "Use the T4 shell entries under experiments/rag_post_event/. "
            "Set ALLOW_LOCAL_RAG_DOCUMENT_LLM_RUN=1 only for deliberate mocked local debugging."
        )


def _reader_id(reader: ReaderLike) -> str:
    return str(getattr(reader, "reader_family", type(reader).__name__))


def _reader_manifest(reader: ReaderLike) -> dict[str, object]:
    manifest_fn = getattr(reader, "manifest", None)
    if callable(manifest_fn):
        payload = dict(manifest_fn())
    else:
        payload = {"reader_family": _reader_id(reader)}
    return payload


def _static_reader_manifest(reader: ReaderLike) -> dict[str, object]:
    payload = _reader_manifest(reader)
    payload.pop("real_call_count", None)
    payload.pop("cache_hit_count", None)
    return payload


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _truthy(value: object) -> bool:
    return value is True or str(value).casefold() in {"1", "true", "yes"}


def _mean_indicator(rows: list[dict[str, object]], field: str) -> float:
    if not rows:
        return float("nan")
    return sum(_truthy(row.get(field, False)) for row in rows) / len(rows)


def _uniform_row_value(
    rows: list[dict[str, object]],
    field: str,
    *,
    legacy_default: object,
) -> object:
    values = {row[field] for row in rows if field in row and row[field] is not None}
    if len(values) > 1:
        raise ValueError(f"Pilot rows contain multiple {field} values: {sorted(map(str, values))}")
    return next(iter(values)) if values else legacy_default


def build_pilot_gate_summary(rows: list[dict[str, object]]) -> dict[str, object]:
    """Compute the four preregistered pilot gates from all 232 reader cells.

    ``parse_success_rate`` is a parser/format gate: a recognized ``ABSTAIN`` is
    parseable and therefore included as success, while
    ``REFUSAL_OR_UNPARSEABLE`` is not.  Abstention remains a task failure in
    the canonical-containment gates and is reported separately as a diagnostic.
    """

    parse_success = sum(
        str(row.get("scoring_class", "")) != REFUSAL_OR_UNPARSEABLE for row in rows
    ) / len(rows) if rows else float("nan")
    query_rewrite_targets = [
        row
        for row in rows
        if row.get("method") == "query_rewrite_update" and row.get("query_type") == TARGET
    ]
    store_shifted_targets = [
        row
        for row in rows
        if row.get("method") == "store_only"
        and row.get("query_type") == TARGET
        and row.get("event_type") in {"REVISE", "RETIRE"}
    ]
    no_update_obsolete = [
        row
        for row in rows
        if row.get("method") == "no_update" and row.get("query_type") == OBSOLETE_UNQUALIFIED
    ]
    values = {
        "parse_success_rate": parse_success,
        "query_rewrite_target_success": _mean_indicator(
            query_rewrite_targets, "correct_canonical_contained"
        ),
        "store_only_shifted_target_success": _mean_indicator(
            store_shifted_targets, "correct_canonical_contained"
        ),
        "no_update_obsolete_rate": _mean_indicator(
            no_update_obsolete, "conflicting_canonical_contained"
        ),
    }
    diagnostic_values = {
        "abstention_rate": sum(
            str(row.get("scoring_class", "")) == ABSTAIN for row in rows
        ) / len(rows) if rows else float("nan"),
    }
    thresholds = {
        "parse_success_rate": {"operator": ">=", "threshold": 0.95},
        "query_rewrite_target_success": {"operator": ">=", "threshold": 0.70},
        "store_only_shifted_target_success": {"operator": "<=", "threshold": 0.30},
        "no_update_obsolete_rate": {"operator": ">=", "threshold": 0.70},
    }
    gate_pass = {
        "parse_success_rate": values["parse_success_rate"] >= 0.95,
        "query_rewrite_target_success": values["query_rewrite_target_success"] >= 0.70,
        "store_only_shifted_target_success": values["store_only_shifted_target_success"] <= 0.30,
        "no_update_obsolete_rate": values["no_update_obsolete_rate"] >= 0.70,
    }
    cardinality_pass = len(rows) == PILOT_READER_CELL_COUNT
    status = "PASS" if cardinality_pass and all(gate_pass.values()) else "FAIL"
    return {
        "protocol_version": PILOT_GATE_PROTOCOL_VERSION,
        "query_template_version": _uniform_row_value(
            rows,
            "query_template_version",
            legacy_default="unrecorded_legacy",
        ),
        "holdout_offset": int(
            _uniform_row_value(rows, "holdout_offset", legacy_default=0)
        ),
        "subsample_hash": _uniform_row_value(
            rows,
            "subsample_hash",
            legacy_default="unrecorded_legacy",
        ),
        "status": status,
        "terminal_reason": "PASS" if status == "PASS" else "FLOOR_OR_FORMAT_BOUNDARY",
        "reader_cell_count": len(rows),
        "expected_reader_cell_count": PILOT_READER_CELL_COUNT,
        "cardinality_pass": cardinality_pass,
        "gate_values": values,
        "gate_semantics": {
            "parse_success_rate": (
                "scoring_class != REFUSAL_OR_UNPARSEABLE; ABSTAIN counts as "
                "parsed but remains incorrect for canonical-containment task gates"
            ),
        },
        "diagnostic_values": diagnostic_values,
        "gate_thresholds": thresholds,
        "gate_pass": gate_pass,
    }


def write_pilot_gate_artifacts(
    output_dir: Path,
    rows: list[dict[str, object]],
) -> dict[str, object]:
    summary = build_pilot_gate_summary(rows)
    if summary["query_template_version"] != QUERY_TEMPLATE_VERSION:
        raise ValueError("CL2 pilot rows do not record the current query template version")
    if int(summary["holdout_offset"]) <= 0:
        raise ValueError("CL2 pilot rows must record a fresh positive holdout_offset")
    if summary["subsample_hash"] == "unrecorded_legacy":
        raise ValueError("CL2 pilot rows must record the selected-event subsample_hash")
    summary_path = output_dir / "pilot_gate_summary.json"
    gate_manifest_path = output_dir / "pilot_gate_manifest.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    payload: dict[str, object] = {
        "protocol_version": PILOT_GATE_PROTOCOL_VERSION,
        "query_template_version": summary["query_template_version"],
        "holdout_offset": summary["holdout_offset"],
        "subsample_hash": summary["subsample_hash"],
        "status": summary["status"],
        "summary_file": summary_path.name,
        "summary_sha256": _sha256_file(summary_path),
        "gate_names": list(PILOT_GATE_NAMES),
    }
    payload["manifest_payload_sha256"] = stable_json_hash(payload)
    gate_manifest_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "status": summary["status"],
        "manifest_path": str(gate_manifest_path.resolve()),
        "manifest_sha256": _sha256_file(gate_manifest_path),
        "summary_path": str(summary_path.resolve()),
        "summary_sha256": payload["summary_sha256"],
        "query_template_version": summary["query_template_version"],
        "holdout_offset": summary["holdout_offset"],
        "subsample_hash": summary["subsample_hash"],
    }


def validate_pilot_gate_manifest(
    path: Path,
    *,
    require_pass: bool = True,
) -> dict[str, object]:
    """Validate the complete pilot provenance, optionally requiring a PASS."""

    manifest_path = path.resolve()
    if not manifest_path.is_file():
        raise ValueError(f"Pilot gate manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Pilot gate manifest is not valid JSON: {manifest_path}") from exc
    required = {
        "protocol_version",
        "query_template_version",
        "holdout_offset",
        "subsample_hash",
        "status",
        "summary_file",
        "summary_sha256",
        "gate_names",
        "manifest_payload_sha256",
    }
    if not isinstance(manifest, dict) or not required.issubset(manifest):
        raise ValueError("Pilot gate manifest schema is incomplete")
    payload = {key: value for key, value in manifest.items() if key != "manifest_payload_sha256"}
    if stable_json_hash(payload) != manifest["manifest_payload_sha256"]:
        raise ValueError("Pilot gate manifest payload hash mismatch")
    if manifest["protocol_version"] != PILOT_GATE_PROTOCOL_VERSION:
        raise ValueError("Pilot gate protocol version mismatch")
    if manifest["query_template_version"] != QUERY_TEMPLATE_VERSION:
        raise ValueError("Pilot gate query template version mismatch")
    if int(manifest["holdout_offset"]) <= 0:
        raise ValueError("Pilot gate does not bind a fresh positive holdout_offset")
    if str(manifest["subsample_hash"]) in {"", "unrecorded_legacy"}:
        raise ValueError("Pilot gate does not bind the selected-event subsample_hash")
    if manifest["status"] not in {"PASS", "FAIL"}:
        raise ValueError("Pilot gate manifest status must be PASS or FAIL")
    if tuple(manifest["gate_names"]) != PILOT_GATE_NAMES:
        raise ValueError("Pilot gate manifest does not bind the exact four gates")
    summary_name = Path(str(manifest["summary_file"]))
    if summary_name.name != str(summary_name):
        raise ValueError("Pilot gate summary_file must be a sibling basename")
    summary_path = manifest_path.parent / summary_name
    if not summary_path.is_file() or _sha256_file(summary_path) != manifest["summary_sha256"]:
        raise ValueError("Pilot gate summary hash mismatch")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Pilot gate summary is not valid JSON") from exc
    gate_pass = summary.get("gate_pass", {}) if isinstance(summary, dict) else {}
    expected_status = (
        "PASS"
        if summary.get("cardinality_pass") is True
        and set(gate_pass) == set(PILOT_GATE_NAMES)
        and all(gate_pass.values())
        else "FAIL"
    ) if isinstance(summary, dict) else "FAIL"
    if (
        not isinstance(summary, dict)
        or summary.get("protocol_version") != PILOT_GATE_PROTOCOL_VERSION
        or summary.get("query_template_version") != QUERY_TEMPLATE_VERSION
        or summary.get("query_template_version") != manifest["query_template_version"]
        or summary.get("holdout_offset") != manifest["holdout_offset"]
        or summary.get("subsample_hash") != manifest["subsample_hash"]
        or summary.get("status") != manifest["status"]
        or summary.get("status") != expected_status
        or summary.get("reader_cell_count") != PILOT_READER_CELL_COUNT
        or summary.get("cardinality_pass") is not True
        or set(summary.get("gate_values", {})) != set(PILOT_GATE_NAMES)
        or set(gate_pass) != set(PILOT_GATE_NAMES)
    ):
        raise ValueError("Pilot gate summary does not certify the exact four-gate result")
    if require_pass and manifest["status"] != "PASS":
        raise ValueError("Formal mode requires pilot gate status PASS")
    return {
        "status": str(manifest["status"]),
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256_file(manifest_path),
        "summary_path": str(summary_path.resolve()),
        "summary_sha256": str(manifest["summary_sha256"]),
        "query_template_version": str(manifest["query_template_version"]),
        "holdout_offset": int(manifest["holdout_offset"]),
        "subsample_hash": str(manifest["subsample_hash"]),
    }


def validate_formal_matrix(
    reader: str,
    methods: tuple[str, ...],
    retrievers: tuple[str, ...],
) -> None:
    expected_retrievers = FORMAL_RETRIEVER_MATRIX.get(reader)
    if expected_retrievers is None:
        raise ValueError(f"No frozen formal matrix for reader: {reader}")
    if methods != REGISTERED_METHODS or retrievers != expected_retrievers:
        raise ValueError(
            "Formal mode requires the exact frozen matrix: six registered methods; "
            "Qwen and deterministic extractive use BM25+dense, while Grok and "
            "legacy MiniMax use BM25 only"
        )


def resolve_formal_provenance(
    *,
    reader: str,
    methods: tuple[str, ...],
    retrievers: tuple[str, ...],
    pilot_gate_manifest: Path | None,
    exploratory: bool,
) -> dict[str, object]:
    """Apply the frozen formal matrix and the selected pilot-gate policy."""

    if pilot_gate_manifest is None:
        if not exploratory:
            raise ValueError("Formal mode requires --pilot-gate-manifest <path>")
        pilot_gate_provenance: dict[str, object] = {}
    else:
        pilot_gate_provenance = validate_pilot_gate_manifest(
            pilot_gate_manifest,
            require_pass=not exploratory,
        )
    validate_formal_matrix(reader, methods, retrievers)
    return pilot_gate_provenance


def effective_retrieval_prompt(
    *,
    method: str,
    query: Query,
    event: KnowledgeEvent,
    stress_axis: str,
    effective_intent: str,
) -> str:
    """Mirror the frozen method's effective retrieval prompt for provenance."""

    if method in {
        "oldref_adapted_preserver",
        "postevent_validity_filter",
        "intent_aware_postevent_filter",
    }:
        return expanded_prompt_for_event(
            query_retrieval_prompt(query),
            event,
            stress_axis=stress_axis,
            intent=effective_intent,
        )
    if method == "query_rewrite_update":
        return rewrite_prompt_for_event(query, event, stress_axis=stress_axis)
    return query_retrieval_prompt(query)


def conflicting_answer_for_query(query: Query, event: KnowledgeEvent) -> str:
    """Return the opposite version canonical when the query has one."""

    if query.query_type == HISTORICAL:
        return event.new_answer
    if query.query_type in {TARGET, OBSOLETE_UNQUALIFIED}:
        return event.old_answer
    if query.query_type == CURRENT_VALID_STABLE:
        return ""
    return query.obsolete_answer


def build_controlled_corpora(config: RunnerConfig) -> dict[int, DocumentCorpus]:
    """Build the frozen controlled document substrate without running readers."""

    config.validate()
    return {
        seed: build_document_corpus(
            event_count=config.source_events_per_seed,
            seed=seed,
            stable_queries_per_event=config.source_stable_queries_per_event,
            add_fraction=config.add_fraction,
            revise_fraction=config.revise_fraction,
            retire_fraction=config.retire_fraction,
            distractors_per_event=config.distractors_per_event,
            chunk_noise_paragraphs=config.chunk_noise_paragraphs,
            stress_axis=config.stress_axis,
        )
        for seed in config.seeds
    }


def selection_for_config(
    corpora: Mapping[int, DocumentCorpus],
    config: RunnerConfig,
) -> SubsampleSelection:
    """Build the formal selection, then derive the nested pilot if requested."""

    formal = select_subsample(
        {seed: corpus.events for seed, corpus in corpora.items()},
        {seed: corpus.queries for seed, corpus in corpora.items()},
        event_counts=FORMAL_EVENT_COUNTS,
        mode="formal",
    )
    if config.mode == "pilot":
        return select_headroom_pilot(
            formal,
            seed=PILOT_SEED,
            holdout_offset=config.holdout_offset,
        )
    return formal


class DocumentLLMRunner:
    """Controlled-corpus context builder with an injected reader."""

    def __init__(
        self,
        *,
        config: RunnerConfig,
        reader: ReaderLike,
        dense_encoder: EmbeddingEncoder | None = None,
        dense_cache_dir: Path | None = None,
        pilot_gate_provenance: Mapping[str, object] | None = None,
        reader_concurrency: int = 1,
    ) -> None:
        config.validate()
        if reader_concurrency <= 0:
            raise ValueError("reader_concurrency must be positive")
        if "dense" in config.retrievers and dense_encoder is None:
            raise ValueError("dense_encoder is required when the dense retriever is enabled")
        if _reader_id(reader) == MINIMAX_READER and config.retrievers != ("bm25",):
            raise ValueError("MiniMax is frozen to the BM25-only arm in Block CL")
        self.config = config
        self.reader = reader
        self.dense_encoder = dense_encoder
        self.dense_cache_dir = dense_cache_dir
        self.pilot_gate_provenance = dict(pilot_gate_provenance or {})
        self.reader_concurrency = int(reader_concurrency)
        self.index_manifests: list[dict[str, object]] = []

    def config_hash(self, selection: SubsampleSelection) -> str:
        return stable_json_hash(
            {
                "protocol_version": RUNNER_PROTOCOL_VERSION,
                "subsample_hash": selection.subsample_hash,
                "config": asdict(self.config),
                "reader": _static_reader_manifest(self.reader),
            }
        )

    def _dense_cache_path(self, *, seed: int, state: str) -> Path | None:
        if self.dense_cache_dir is None:
            return None
        return self.dense_cache_dir / f"seed_{seed}_{state}_{DENSE_RETRIEVER_VERSION}.npz"

    def _indexes(
        self,
        *,
        corpus: DocumentCorpus,
        seed: int,
        retriever: str,
    ) -> tuple[Any, Any, Any]:
        documents_by_state = {
            "old": corpus.old_documents,
            "postevent": corpus.postevent_documents,
            "combined": corpus.documents,
        }
        indexes: dict[str, Any] = {}
        for state, documents in documents_by_state.items():
            if retriever == "bm25":
                index = DocumentIndex(documents, retriever="bm25")
                manifest: dict[str, object] = {
                    "protocol_version": "document_bm25_v1",
                    "retriever": "bm25",
                    "chunk_count": len(index.chunks),
                    "chunk_fingerprint": stable_json_hash(
                        [(chunk.chunk_id, chunk.text) for chunk in index.chunks]
                    ),
                }
            elif retriever == "dense":
                if self.dense_encoder is None:
                    raise ValueError("dense_encoder is required for dense retrieval")
                dense = DenseDocumentIndex(
                    documents,
                    encoder=self.dense_encoder,
                    embedding_cache_path=self._dense_cache_path(seed=seed, state=state),
                )
                index = _MethodCompatibleDenseIndex(dense)
                manifest = dict(index.manifest())
                manifest["retriever"] = "dense"
                manifest["model_snapshot"] = self.config.dense_model_snapshot
            else:
                raise ValueError(f"Unsupported retriever: {retriever}")
            manifest.update({"seed": seed, "state": state})
            self.index_manifests.append(manifest)
            indexes[state] = index
        return indexes["old"], indexes["postevent"], indexes["combined"]

    def _iter_prepared_cells(
        self,
        *,
        corpora: Mapping[int, DocumentCorpus],
        selection: SubsampleSelection,
    ) -> Iterator[_PreparedReaderCell]:
        selected_by_seed: dict[int, list[Any]] = {}
        for item in selection.selected_events:
            selected_by_seed.setdefault(item.seed, []).append(item)

        for seed in sorted(selected_by_seed):
            corpus = corpora[seed]
            for retriever in self.config.retrievers:
                old_index, postevent_index, combined_index = self._indexes(
                    corpus=corpus,
                    seed=seed,
                    retriever=retriever,
                )
                for item in selected_by_seed[seed]:
                    event = item.event
                    for query in item.queries:
                        clean_intent = infer_query_intent(query_retrieval_prompt(query))
                        effective_intent = clean_intent
                        for method in self.config.methods:
                            trace = method_trace(
                                method=method,
                                query=query,
                                event=event,
                                seed=seed,
                                old_index=old_index,
                                postevent_index=postevent_index,
                                combined_index=combined_index,
                                top_k=self.config.top_k,
                                rerank_candidate_k=self.config.rerank_candidate_k,
                                stress_axis=self.config.stress_axis,
                                effective_intent=effective_intent,
                                evaluation_document_labels={
                                    document.doc_id: (document.doc_role, document.version)
                                    for document in corpus.documents
                                },
                            )
                            yield _PreparedReaderCell(
                                seed=seed,
                                event=event,
                                query=query,
                                method=method,
                                retriever=retriever,
                                trace=trace,
                                clean_intent=clean_intent,
                                effective_intent=effective_intent,
                            )

    def _answer_cell(self, cell: _PreparedReaderCell) -> Any:
        return self.reader.answer(
            query=query_reader_prompt(cell.query),
            retrieved=cell.trace.retrieved,
        )

    def _row_for_cell(
        self,
        *,
        cell: _PreparedReaderCell,
        response: Any,
        selection: SubsampleSelection,
        config_hash: str,
    ) -> dict[str, object]:
        return self._row(
            selection=selection,
            config_hash=config_hash,
            seed=cell.seed,
            event=cell.event,
            query=cell.query,
            method=cell.method,
            retriever=cell.retriever,
            trace=cell.trace,
            response=response,
            clean_intent=cell.clean_intent,
            effective_intent=cell.effective_intent,
        )

    def iter_rows(
        self,
        *,
        corpora: Mapping[int, DocumentCorpus],
        selection: SubsampleSelection,
    ) -> Iterator[dict[str, object]]:
        validate_non_overlapping_canonicals(
            answer
            for item in selection.selected_events
            for query in item.queries
            for answer in (
                item.event.old_answer,
                item.event.new_answer,
                query.expected_old,
                query.expected_postevent,
                query.expected_current,
                query.obsolete_answer,
            )
        )
        missing_seeds = sorted(set(seed for seed, _ in selection.event_keys).difference(corpora))
        if missing_seeds:
            raise ValueError(f"Selection references missing corpora for seeds: {missing_seeds}")
        config_hash = self.config_hash(selection)
        self.index_manifests = []

        cells = self._iter_prepared_cells(corpora=corpora, selection=selection)
        if self.reader_concurrency == 1:
            for cell in cells:
                yield self._row_for_cell(
                    cell=cell,
                    response=self._answer_cell(cell),
                    selection=selection,
                    config_hash=config_hash,
                )
            return

        prepared_cells = list(cells)
        with ThreadPoolExecutor(max_workers=self.reader_concurrency) as executor:
            responses = list(executor.map(self._answer_cell, prepared_cells))
        for cell, response in zip(prepared_cells, responses):
            yield self._row_for_cell(
                cell=cell,
                response=response,
                selection=selection,
                config_hash=config_hash,
            )

    def run_rows(
        self,
        *,
        corpora: Mapping[int, DocumentCorpus],
        selection: SubsampleSelection,
    ) -> list[dict[str, object]]:
        return list(self.iter_rows(corpora=corpora, selection=selection))

    def _row(
        self,
        *,
        selection: SubsampleSelection,
        config_hash: str,
        seed: int,
        event: KnowledgeEvent,
        query: Query,
        method: str,
        retriever: str,
        trace: Any,
        response: Any,
        clean_intent: str,
        effective_intent: str,
    ) -> dict[str, object]:
        raw_output = str(getattr(response, "answer", response))
        conflicting_answer = conflicting_answer_for_query(query, event)
        score = score_reader_output(
            raw_output,
            correct_answer=query.expected_current,
            conflicting_answer=conflicting_answer,
        )
        retrieval_prompt = effective_retrieval_prompt(
            method=method,
            query=query,
            event=event,
            stress_axis=self.config.stress_axis,
            effective_intent=effective_intent,
        )
        contexts = [
            {
                "chunk_id": retrieved.chunk.chunk_id,
                "doc_id": retrieved.chunk.doc_id,
                "rank": retrieved.rank,
                "text": retrieved.chunk.text,
            }
            for retrieved in trace.retrieved
        ]
        context_hash = stable_json_hash(contexts)
        clean_record_hash = stable_json_hash(
            {
                "seed": seed,
                "event": event_to_json(event),
                "query": query_to_json(query),
            }
        )
        effective_provenance_hash = stable_json_hash(
            {
                "clean_record_sha256": clean_record_hash,
                "method": method,
                "retriever": retriever,
                "retrieval_prompt": retrieval_prompt,
                "context_sha256": context_hash,
            }
        )
        chunk_ids = [retrieved.chunk.chunk_id for retrieved in trace.retrieved]
        scores = [retrieved.score for retrieved in trace.retrieved]
        return {
            "run_id": self.config.run_id,
            "dataset": "controlled_document_rag",
            "protocol_version": RUNNER_PROTOCOL_VERSION,
            "exploratory": self.config.exploratory,
            "query_template_version": QUERY_TEMPLATE_VERSION,
            "holdout_offset": selection.holdout_offset,
            "subsample_hash": selection.subsample_hash,
            "config_hash": config_hash,
            "seed": seed,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "query_id": query.query_id,
            "query_type": query.query_type,
            "reader_id": str(getattr(response, "reader_family", _reader_id(self.reader))),
            "model_requested": str(getattr(response, "model_requested", "")),
            "model_returned": str(getattr(response, "model_returned", "")),
            "retriever": retriever,
            "method": method,
            "raw_reader_output": score.raw_output,
            "normalized_reader_output": score.normalized_output,
            "parsed_answer": score.parsed_answer,
            "scoring_class": score.outcome_class,
            "correct_canonical_contained": int(score.correct_contained),
            "conflicting_canonical_contained": int(score.conflicting_contained),
            "correct_canonical_answer": query.expected_current,
            "conflicting_canonical_answer": conflicting_answer,
            "old_canonical_answer": event.old_answer,
            "new_canonical_answer": event.new_answer,
            "clean_event_type": event.event_type,
            "effective_event_type": event.event_type,
            "clean_query_prompt": query_reader_prompt(query),
            "effective_reader_query": query_reader_prompt(query),
            "effective_retrieval_prompt": retrieval_prompt,
            "clean_intent": clean_intent,
            "effective_intent": effective_intent,
            "intent_corrupted": int(clean_intent != effective_intent),
            "clean_gold_doc_id": trace.gold_doc_id,
            "effective_retrieved_doc_ids": json.dumps(unique_doc_ids(trace.retrieved), sort_keys=True),
            "effective_retrieved_chunk_ids": json.dumps(chunk_ids),
            "effective_retrieved_versions": json.dumps(unique_versions(trace.retrieved), sort_keys=True),
            "effective_retrieval_scores": json.dumps(scores),
            "effective_context_sha256": context_hash,
            "effective_retrieval_recall_at_k": trace.retrieval_recall_at_k,
            "effective_evidence_hit": trace.evidence_hit,
            "effective_obsolete_evidence_hit": trace.obsolete_evidence_hit,
            "clean_record_sha256": clean_record_hash,
            "effective_provenance_sha256": effective_provenance_hash,
            "reader_prompt_sha256": str(
                getattr(
                    response,
                    "prompt_sha256",
                    stable_json_hash(
                        {
                            "reader_id": _reader_id(self.reader),
                            "query": query_reader_prompt(query),
                            "contexts": contexts,
                        }
                    ),
                )
            ),
            "reader_cache_hit": int(bool(getattr(response, "cache_hit", False))),
            "reader_latency_seconds": float(getattr(response, "latency_seconds", 0.0)),
            "reader_base_url": str(getattr(response, "base_url", "")),
            "reader_prompt_policy_version": str(
                getattr(response, "prompt_policy_version", PROMPT_POLICY_VERSION)
            ),
            "answer_source": trace.answer_source,
            "chunk_rank": trace.chunk_rank,
            "stress_axis": self.config.stress_axis,
        }

    def manifest(
        self,
        *,
        selection: SubsampleSelection,
        row_count: int,
        corpora: Mapping[int, DocumentCorpus],
    ) -> dict[str, object]:
        manifest = {
            "experiment": "controlled_document_rag_llm_reader",
            "protocol_version": RUNNER_PROTOCOL_VERSION,
            "exploratory": self.config.exploratory,
            "preregistered_closure": self.config.mode == "formal"
            and not self.config.exploratory,
            "query_template_version": QUERY_TEMPLATE_VERSION,
            "holdout_offset": selection.holdout_offset,
            "subsample_protocol_version": SUBSAMPLE_PROTOCOL_VERSION,
            "subsample_hash": selection.subsample_hash,
            "config_hash": self.config_hash(selection),
            "reader_concurrency": self.reader_concurrency,
            "config": asdict(self.config),
            "row_count": row_count,
            "source_event_record_count": sum(len(corpus.events) for corpus in corpora.values()),
            "selected_event_count": len(selection.event_keys),
            "selected_query_count": len(selection.query_keys),
            "schema_fields": CSV_FIELDS,
            "subsample": selection.to_manifest(),
            "reader": _reader_manifest(self.reader),
            "retrieval_indexes": self.index_manifests,
            "git": git_metadata(),
            "environment": environment_metadata(self.config.device),
        }
        if self.config.exploratory:
            manifest["note"] = EXPLORATORY_NOTE
        if self.pilot_gate_provenance:
            manifest["pilot_gate"] = self.pilot_gate_provenance
        return manifest


def write_run_outputs(
    *,
    output_dir: Path,
    runner: DocumentLLMRunner,
    corpora: Mapping[int, DocumentCorpus],
    selection: SubsampleSelection,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Stream rows to a partial CSV, finalize it, and write provenance."""

    partial_path = output_dir / "rag_document_llm_metrics_partial.csv"
    final_path = output_dir / "rag_document_llm_metrics.csv"
    manifest_path = output_dir / "run_manifest.json"
    subsample_path = output_dir / "subsample.json"
    occupied = [path for path in (partial_path, final_path, manifest_path, subsample_path) if path.exists()]
    if occupied:
        raise FileExistsError(f"Refusing to reuse existing output artifacts: {occupied}")

    output_dir.mkdir(parents=True, exist_ok=True)
    subsample_manifest = selection.to_manifest()
    if runner.config.exploratory:
        subsample_manifest.update(
            {
                "exploratory": True,
                "preregistered_closure": False,
                "note": EXPLORATORY_NOTE,
            }
        )
    subsample_path.write_text(
        json.dumps(subsample_manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    rows: list[dict[str, object]] = []
    with partial_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in runner.iter_rows(corpora=corpora, selection=selection):
            writer.writerow(row)
            handle.flush()
            rows.append(row)
    shutil.copyfile(partial_path, final_path)
    if runner.config.mode == "pilot":
        pilot_gate = write_pilot_gate_artifacts(output_dir, rows)
        runner.pilot_gate_provenance = pilot_gate
    manifest = runner.manifest(selection=selection, row_count=len(rows), corpora=corpora)
    manifest_text = json.dumps(manifest, indent=2, sort_keys=True, default=str)
    manifest_path.write_text(manifest_text, encoding="utf-8")
    return rows, json.loads(manifest_text)


def _positive_reader_concurrency(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("reader concurrency must be positive")
    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--mode", choices=["pilot", "formal"], default="formal")
    parser.add_argument("--seeds", type=int, nargs="+", default=list(FORMAL_SEEDS))
    parser.add_argument("--methods", nargs="+", choices=REGISTERED_METHODS)
    parser.add_argument("--retrievers", nargs="+", choices=SUPPORTED_RETRIEVERS)
    parser.add_argument("--source-events-per-seed", type=int, default=1500)
    parser.add_argument("--source-stable-queries-per-event", type=int, default=2)
    parser.add_argument("--distractors-per-event", type=int, default=2)
    parser.add_argument("--chunk-noise-paragraphs", type=int, default=0)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rerank-candidate-k", type=int, default=20)
    parser.add_argument("--stress-axis", choices=[STRESS_NONE, STRESS_LEXICAL_SCHEMA], default=STRESS_LEXICAL_SCHEMA)
    parser.add_argument(
        "--reader",
        choices=[QWEN_READER, MINIMAX_READER, GROK_READER, DETERMINISTIC_EXTRACTIVE_READER],
        required=True,
    )
    parser.add_argument("--base-url", action="append", default=[])
    parser.add_argument("--reader-model-id", default="")
    parser.add_argument("--reader-artifact-dir", type=Path)
    parser.add_argument("--api-key-env", default="")
    parser.add_argument("--cache-only", action="store_true")
    parser.add_argument("--reader-concurrency", type=_positive_reader_concurrency, default=1)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--dense-model-path", type=Path)
    parser.add_argument("--dense-model-snapshot", default=DENSE_MODEL_SNAPSHOT)
    parser.add_argument("--dense-cache-dir", type=Path)
    parser.add_argument("--dense-batch-size", type=int, default=8)
    parser.add_argument("--dense-max-length", type=int, default=4096)
    parser.add_argument("--pilot-gate-manifest", type=Path)
    parser.add_argument(
        "--exploratory",
        action="store_true",
        help=(
            "Run the frozen formal matrix without requiring a pilot PASS; "
            "artifacts are labeled as pilot-informed and not pre-registered CL closure"
        ),
    )
    parser.add_argument(
        "--holdout-offset",
        type=int,
        help=(
            "Pilot block offset; CL2 defaults to 1, while offset 0 names the "
            "historical v1/v2 event block and is rejected for new pilot runs"
        ),
    )
    parser.add_argument("--device", default="cuda")
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = parse_args()
    enforce_t4_only()
    if not args.run_id:
        args.run_id = args.output_dir.name

    if args.mode == "pilot":
        if args.exploratory:
            raise SystemExit("--exploratory is only valid with --mode formal")
        if args.reader != QWEN_READER:
            raise SystemExit("The frozen headroom pilot uses the Qwen reader only")
        seeds = (PILOT_SEED,)
        methods = tuple(args.methods or PILOT_METHODS)
        retrievers = tuple(args.retrievers or ("bm25",))
        if methods != PILOT_METHODS or retrievers != ("bm25",):
            raise SystemExit(
                "The frozen pilot requires exactly four methods "
                "(no_update, store_only, query_rewrite_update, postevent_validity_filter) and BM25"
            )
        if args.pilot_gate_manifest is not None:
            raise SystemExit("Pilot mode creates the gate manifest and must not consume one")
        holdout_offset = (
            DEFAULT_PILOT_HOLDOUT_OFFSET
            if args.holdout_offset is None
            else args.holdout_offset
        )
        if holdout_offset <= 0:
            raise SystemExit("CL2 pilot requires --holdout-offset >= 1")
        pilot_gate_provenance: dict[str, object] = {}
    else:
        seeds = tuple(args.seeds)
        if seeds != FORMAL_SEEDS:
            raise SystemExit(f"The frozen formal seeds are {FORMAL_SEEDS}")
        if args.holdout_offset not in {None, 0}:
            raise SystemExit("--holdout-offset is only valid in pilot mode")
        holdout_offset = 0
        methods = tuple(args.methods or REGISTERED_METHODS)
        retrievers = tuple(args.retrievers or FORMAL_RETRIEVER_MATRIX[args.reader])
        try:
            pilot_gate_provenance = resolve_formal_provenance(
                reader=args.reader,
                methods=methods,
                retrievers=retrievers,
                pilot_gate_manifest=args.pilot_gate_manifest,
                exploratory=args.exploratory,
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc

    config = RunnerConfig(
        run_id=args.run_id,
        mode=args.mode,
        seeds=seeds,
        methods=methods,
        retrievers=retrievers,
        source_events_per_seed=args.source_events_per_seed,
        source_stable_queries_per_event=args.source_stable_queries_per_event,
        distractors_per_event=args.distractors_per_event,
        chunk_noise_paragraphs=args.chunk_noise_paragraphs,
        top_k=args.top_k,
        rerank_candidate_k=args.rerank_candidate_k,
        stress_axis=args.stress_axis,
        dense_model_snapshot=args.dense_model_snapshot,
        holdout_offset=holdout_offset,
        device=args.device,
        exploratory=args.exploratory,
    )
    config.validate()
    corpora = build_controlled_corpora(config)
    selection = selection_for_config(corpora, config)

    dense_encoder: EmbeddingEncoder | None = None
    if "dense" in config.retrievers:
        if args.dense_model_path is None:
            raise SystemExit("--dense-model-path is required for the frozen dense arm")
        dense_encoder = Qwen3EmbeddingEncoder(
            model_path=args.dense_model_path,
            device=args.device,
            batch_size=args.dense_batch_size,
            max_length=args.dense_max_length,
        )

    if args.reader == DETERMINISTIC_EXTRACTIVE_READER:
        reader: ReaderLike = DeterministicExtractiveReader()
    else:
        if not args.base_url:
            raise SystemExit("At least one --base-url is required for a frozen LLM reader")
        reader_artifact_dir = args.reader_artifact_dir or (args.output_dir / "reader_artifacts")
        if (
            args.reader == MINIMAX_READER
            and not (reader_artifact_dir / "minimax_reader_cache.jsonl").is_file()
        ):
            raise SystemExit(
                "MiniMax is cache-only in Block CL and requires an existing "
                f"{reader_artifact_dir / 'minimax_reader_cache.jsonl'}"
            )
        reader = build_reader(
            args.reader,
            base_urls=args.base_url,
            output_dir=reader_artifact_dir,
            model_id=args.reader_model_id,
            api_key_env=args.api_key_env,
            require_cache=args.cache_only or args.reader == MINIMAX_READER,
            timeout_seconds=args.timeout_seconds,
            retries=args.retries,
        )

    runner = DocumentLLMRunner(
        config=config,
        reader=reader,
        dense_encoder=dense_encoder,
        dense_cache_dir=args.dense_cache_dir,
        pilot_gate_provenance=pilot_gate_provenance,
        reader_concurrency=args.reader_concurrency,
    )
    rows, _ = write_run_outputs(
        output_dir=args.output_dir,
        runner=runner,
        corpora=corpora,
        selection=selection,
    )
    LOGGER.info("Controlled document LLM artifacts written to %s", args.output_dir)
    # Stdout contract: exactly one machine-readable terminal JSON object.
    sys.stdout.write(
        json.dumps(
            {
                "status": "PASS",
                "exploratory": args.exploratory,
                "preregistered_closure": args.mode == "formal" and not args.exploratory,
                "row_count": len(rows),
                "query_template_version": QUERY_TEMPLATE_VERSION,
                "holdout_offset": selection.holdout_offset,
                "metrics": str(args.output_dir / "rag_document_llm_metrics.csv"),
                "manifest": str(args.output_dir / "run_manifest.json"),
            },
            sort_keys=True,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
