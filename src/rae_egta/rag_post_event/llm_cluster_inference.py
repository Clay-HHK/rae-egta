#!/usr/bin/env python3
"""Paired event-cluster inference for controlled-corpus LLM metrics.

The finite-row-weighted estimands and resampling contract match
``cluster_inference.py``: complete ``(seed, event_id)`` clusters are sampled
with replacement inside ``seed x event_type`` strata, and one draw plan is
shared across methods, query scopes, and metrics.  Readers are deliberately
analyzed separately and are never pooled.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from rae_egta.common.provenance import git_metadata, stable_json_hash
from rae_egta.rag_post_event.cluster_inference import (
    BOOTSTRAP_REPLICATES,
    BOOTSTRAP_SEED,
    CONFIDENCE_LEVEL,
    EVENT_TYPES,
    QUANTILE_METHOD,
    RNG_ALGORITHM,
    ClusterKey,
    draw_cluster_indices,
    jsonable,
    percentile_interval,
    sha256_file,
    write_csv,
    write_json,
)


TARGET = "target"
OBSOLETE = "obsolete_unqualified"
ALL_QUERY_TYPES = "ALL"
ALL_RETRIEVERS = "ALL"

CORRECT = "CORRECT_SIDE_CONTAINMENT"
WRONG = "WRONG_OR_OTHER"
MIXED = "MIXED_VERSION"
ABSTAIN = "ABSTAIN"
REFUSAL = "REFUSAL_OR_UNPARSEABLE"
SCORING_CLASSES = (CORRECT, WRONG, MIXED, ABSTAIN, REFUSAL)
NONCOMMITTED_CLASSES = frozenset((ABSTAIN, REFUSAL))

METRICS = (
    "coverage",
    "selective_accuracy",
    "unconditional_target_success",
    "obsolete_rate",
    "abstention_rate",
    "mixed_rate",
)
CONTRAST_METRICS = ("unconditional_target_success", "selective_accuracy")
CONTRAST_QUERY_TYPES = (TARGET, OBSOLETE)
CURRENT_METHODS = ("query_rewrite_update", "postevent_validity_filter")
OLD_STORE_METHODS = ("no_update", "store_only")
REQUIRED_CONTRAST_METHODS = frozenset((*CURRENT_METHODS, *OLD_STORE_METHODS))
OBSOLETE_QUERY_TYPES = frozenset((TARGET, OBSOLETE))

BOOTSTRAP_BATCH_SIZE = 128
METRICS_FILENAME = "rag_document_llm_metrics.csv"
MANIFEST_FILENAME = "run_manifest.json"

REQUIRED_FIELDS = {
    "run_id",
    "seed",
    "event_id",
    "event_type",
    "query_id",
    "query_type",
    "reader_id",
    "retriever",
    "method",
    "scoring_class",
    "correct_canonical_contained",
    "conflicting_canonical_contained",
    "exploratory",
}


class LLMInferenceError(RuntimeError):
    """Raised when controlled-LLM inputs violate the paired design."""


@dataclass(frozen=True)
class MetricRow:
    seed: int
    event_id: str
    event_type: str
    query_id: str
    query_type: str
    reader_id: str
    retriever: str
    method: str
    scoring_class: str
    conflicting_canonical_contained: bool
    exploratory: bool


@dataclass(frozen=True)
class SourceInput:
    metrics_path: Path
    metrics_sha256: str
    row_count: int
    manifest_path: Path | None
    manifest_sha256: str | None
    source_git_sha: str | None
    source_git_dirty: bool | None
    source_exploratory: bool | None
    source_preregistered_closure: bool | None


@dataclass
class ReaderRetrieverData:
    reader_id: str
    retriever: str
    methods: tuple[str, ...]
    clusters: tuple[ClusterKey, ...]
    seeds: np.ndarray
    event_types: np.ndarray
    query_types: tuple[str, ...]
    numerators: dict[tuple[str, str], np.ndarray]
    denominators: dict[tuple[str, str], np.ndarray]
    row_count: int
    exploratory: bool

    def method_index(self, method: str) -> int:
        return self.methods.index(method)


@dataclass
class LoadedInput:
    input_path: Path
    sources: tuple[SourceInput, ...]
    cells: dict[tuple[str, str], ReaderRetrieverData]
    row_count: int
    exploratory: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_bool(value: object, *, field: str, context: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise LLMInferenceError(f"{context}: invalid {field} boolean {value!r}")


def resolve_metrics_paths(input_metrics: Path) -> tuple[Path, ...]:
    """Resolve one metrics CSV or a formal-root directory of reader runs."""

    if input_metrics.is_file():
        if input_metrics.stat().st_size == 0:
            raise LLMInferenceError(f"empty input metrics: {input_metrics}")
        return (input_metrics.resolve(),)
    if not input_metrics.is_dir():
        raise LLMInferenceError(f"input metrics path does not exist: {input_metrics}")
    paths = tuple(
        sorted(
            path.resolve()
            for path in input_metrics.rglob(METRICS_FILENAME)
            if path.is_file() and path.stat().st_size > 0
        )
    )
    if not paths:
        raise LLMInferenceError(
            f"no non-empty {METRICS_FILENAME} found under {input_metrics}"
        )
    return paths


def _load_source_manifest(
    metrics_path: Path, *, row_count: int, row_exploratory: bool
) -> SourceInput:
    manifest_path = metrics_path.parent / MANIFEST_FILENAME
    if not manifest_path.is_file() or manifest_path.stat().st_size == 0:
        return SourceInput(
            metrics_path=metrics_path,
            metrics_sha256=sha256_file(metrics_path),
            row_count=row_count,
            manifest_path=None,
            manifest_sha256=None,
            source_git_sha=None,
            source_git_dirty=None,
            source_exploratory=None,
            source_preregistered_closure=None,
        )
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LLMInferenceError(f"invalid source manifest JSON: {manifest_path}") from exc
    if int(payload.get("row_count", -1)) != row_count:
        raise LLMInferenceError(f"source manifest row_count mismatch: {manifest_path}")
    source_exploratory = payload.get("exploratory")
    if not isinstance(source_exploratory, bool):
        raise LLMInferenceError(
            f"source manifest lacks boolean exploratory flag: {manifest_path}"
        )
    if source_exploratory != row_exploratory:
        raise LLMInferenceError(
            f"source manifest/row exploratory mismatch: {manifest_path}"
        )
    git = payload.get("git", {})
    source_git_dirty = git.get("git_dirty")
    if not isinstance(source_git_dirty, bool):
        source_git_dirty = None
    preregistered = payload.get("preregistered_closure")
    if not isinstance(preregistered, bool):
        preregistered = None
    return SourceInput(
        metrics_path=metrics_path,
        metrics_sha256=sha256_file(metrics_path),
        row_count=row_count,
        manifest_path=manifest_path.resolve(),
        manifest_sha256=sha256_file(manifest_path),
        source_git_sha=git.get("git_sha"),
        source_git_dirty=source_git_dirty,
        source_exploratory=source_exploratory,
        source_preregistered_closure=preregistered,
    )


def _read_metrics_file(path: Path) -> tuple[list[MetricRow], SourceInput]:
    rows: list[MetricRow] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(REQUIRED_FIELDS - set(reader.fieldnames or ()))
        if missing:
            raise LLMInferenceError(f"{path}: missing fields {missing}")
        for row_number, row in enumerate(reader, start=2):
            context = f"{path}:{row_number}"
            try:
                seed = int(row["seed"])
            except (TypeError, ValueError) as exc:
                raise LLMInferenceError(f"{context}: invalid seed") from exc
            event_id = row["event_id"].strip()
            event_type = row["event_type"].strip()
            query_id = row["query_id"].strip()
            query_type = row["query_type"].strip()
            reader_id = row["reader_id"].strip()
            retriever = row["retriever"].strip()
            method = row["method"].strip()
            scoring_class = row["scoring_class"].strip()
            if (
                not event_id
                or not query_id
                or not query_type
                or not reader_id
                or not retriever
                or not method
                or event_type not in EVENT_TYPES
            ):
                raise LLMInferenceError(f"{context}: invalid row identity")
            if scoring_class not in SCORING_CLASSES:
                raise LLMInferenceError(
                    f"{context}: invalid scoring_class {scoring_class!r}"
                )
            correct_contained = parse_bool(
                row["correct_canonical_contained"],
                field="correct_canonical_contained",
                context=context,
            )
            conflicting_contained = parse_bool(
                row["conflicting_canonical_contained"],
                field="conflicting_canonical_contained",
                context=context,
            )
            exploratory = parse_bool(
                row["exploratory"], field="exploratory", context=context
            )
            if scoring_class == CORRECT and not correct_contained:
                raise LLMInferenceError(
                    f"{context}: CORRECT_SIDE_CONTAINMENT without correct containment"
                )
            if scoring_class == MIXED and not (
                correct_contained and conflicting_contained
            ):
                raise LLMInferenceError(
                    f"{context}: MIXED_VERSION requires both canonical containments"
                )
            rows.append(
                MetricRow(
                    seed=seed,
                    event_id=event_id,
                    event_type=event_type,
                    query_id=query_id,
                    query_type=query_type,
                    reader_id=reader_id,
                    retriever=retriever,
                    method=method,
                    scoring_class=scoring_class,
                    conflicting_canonical_contained=conflicting_contained,
                    exploratory=exploratory,
                )
            )
    if not rows:
        raise LLMInferenceError(f"input contains no metric rows: {path}")
    exploratory_values = {row.exploratory for row in rows}
    if len(exploratory_values) != 1:
        raise LLMInferenceError(f"mixed exploratory flags within source metrics: {path}")
    source = _load_source_manifest(
        path,
        row_count=len(rows),
        row_exploratory=next(iter(exploratory_values)),
    )
    return rows, source


def _metric_contribution(row: MetricRow, metric: str) -> tuple[int, int]:
    committed = row.scoring_class not in NONCOMMITTED_CLASSES
    correct = row.scoring_class == CORRECT
    if metric == "coverage":
        return int(committed), 1
    if metric == "selective_accuracy":
        return int(correct), int(committed)
    if metric == "unconditional_target_success":
        return int(correct), 1
    if metric == "obsolete_rate":
        if row.query_type not in OBSOLETE_QUERY_TYPES:
            return 0, 0
        return int(row.conflicting_canonical_contained), 1
    if metric == "abstention_rate":
        return int(not committed), 1
    if metric == "mixed_rate":
        return int(row.scoring_class == MIXED), 1
    raise KeyError(metric)


def _build_cell(
    reader_id: str, retriever: str, rows: Sequence[MetricRow]
) -> ReaderRetrieverData:
    methods = tuple(sorted({row.method for row in rows}))
    if not REQUIRED_CONTRAST_METHODS.issubset(methods):
        missing = sorted(REQUIRED_CONTRAST_METHODS - set(methods))
        raise LLMInferenceError(
            f"{reader_id}/{retriever}: missing contrast methods {missing}"
        )

    seen_rows: set[tuple[str, int, str, str, str]] = set()
    cluster_types: dict[ClusterKey, str] = {}
    query_types: dict[tuple[ClusterKey, str, str], str] = {}
    query_methods: dict[tuple[ClusterKey, str, str], set[str]] = defaultdict(set)
    for row in rows:
        cluster = ClusterKey(row.seed, row.event_id)
        row_key = (
            row.retriever,
            row.seed,
            row.event_id,
            row.query_id,
            row.method,
        )
        if row_key in seen_rows:
            raise LLMInferenceError(
                f"duplicate row in {reader_id}/{retriever}: {row_key}"
            )
        seen_rows.add(row_key)
        previous_event_type = cluster_types.setdefault(cluster, row.event_type)
        if previous_event_type != row.event_type:
            raise LLMInferenceError(f"cluster changes event_type: {cluster}")
        query_key = (cluster, row.retriever, row.query_id)
        previous_query_type = query_types.setdefault(query_key, row.query_type)
        if previous_query_type != row.query_type:
            raise LLMInferenceError(f"query changes query_type: {query_key}")
        query_methods[query_key].add(row.method)
    expected_methods = set(methods)
    for query_key, observed_methods in query_methods.items():
        if observed_methods != expected_methods:
            raise LLMInferenceError(
                f"unpaired method coverage in {reader_id}/{retriever} at "
                f"{query_key}: {sorted(observed_methods)}"
            )

    clusters = tuple(sorted(cluster_types))
    cluster_index = {key: index for index, key in enumerate(clusters)}
    method_index = {method: index for index, method in enumerate(methods)}
    observed_query_types = tuple(sorted(set(query_types.values())))
    query_scopes = (*observed_query_types, ALL_QUERY_TYPES)
    shape = (len(clusters), len(methods))
    numerators = {
        (query_scope, metric): np.zeros(shape, dtype=np.int64)
        for query_scope in query_scopes
        for metric in METRICS
    }
    denominators = {
        key: np.zeros(shape, dtype=np.int64) for key in numerators
    }
    for row in rows:
        cluster = ClusterKey(row.seed, row.event_id)
        cidx = cluster_index[cluster]
        midx = method_index[row.method]
        for query_scope in (row.query_type, ALL_QUERY_TYPES):
            for metric in METRICS:
                numerator, denominator = _metric_contribution(row, metric)
                numerators[(query_scope, metric)][cidx, midx] += numerator
                denominators[(query_scope, metric)][cidx, midx] += denominator

    return ReaderRetrieverData(
        reader_id=reader_id,
        retriever=retriever,
        methods=methods,
        clusters=clusters,
        seeds=np.asarray([key.seed for key in clusters], dtype=np.int64),
        event_types=np.asarray(
            [cluster_types[key] for key in clusters], dtype=object
        ),
        query_types=observed_query_types,
        numerators=numerators,
        denominators=denominators,
        row_count=len(rows),
        exploratory=any(row.exploratory for row in rows),
    )


def load_input(input_metrics: Path) -> LoadedInput:
    metrics_paths = resolve_metrics_paths(input_metrics)
    all_rows: list[MetricRow] = []
    sources: list[SourceInput] = []
    global_seen: set[tuple[str, str, int, str, str, str]] = set()
    for path in metrics_paths:
        rows, source = _read_metrics_file(path)
        for row in rows:
            identity = (
                row.reader_id,
                row.retriever,
                row.seed,
                row.event_id,
                row.query_id,
                row.method,
            )
            if identity in global_seen:
                raise LLMInferenceError(
                    f"duplicate row identity across input files: {identity}"
                )
            global_seen.add(identity)
        all_rows.extend(rows)
        sources.append(source)

    rows_by_cell: dict[tuple[str, str], list[MetricRow]] = defaultdict(list)
    for row in all_rows:
        rows_by_cell[(row.reader_id, row.retriever)].append(row)
    cells = {
        key: _build_cell(key[0], key[1], rows)
        for key, rows in sorted(rows_by_cell.items())
    }
    rows_by_reader: dict[str, list[MetricRow]] = defaultdict(list)
    for row in all_rows:
        rows_by_reader[row.reader_id].append(row)
    for reader_id, rows in sorted(rows_by_reader.items()):
        cells[(reader_id, ALL_RETRIEVERS)] = _build_cell(
            reader_id, ALL_RETRIEVERS, rows
        )
    return LoadedInput(
        input_path=input_metrics.resolve(),
        sources=tuple(sources),
        cells=cells,
        row_count=len(all_rows),
        exploratory=any(row.exploratory for row in all_rows),
    )


def build_bootstrap_weights(
    data: ReaderRetrieverData,
    *,
    replicates: int,
    rng_seed: int,
    batch_size: int = BOOTSTRAP_BATCH_SIZE,
) -> np.ndarray:
    """Build P0-compatible paired cluster multiplicities.

    This is the same draw contract as ``cluster_inference.bootstrap_metric_means``:
    PCG64, replicate batches, sorted seeds, fixed ``EVENT_TYPES`` order, and
    sampling the original number of clusters with replacement within every
    ``seed x event_type`` stratum.  The resulting weights are shared across all
    methods and metrics for this reader/retriever cell.
    """

    if replicates <= 0 or batch_size <= 0:
        raise ValueError("replicates and batch_size must be positive")
    if not data.clusters:
        raise LLMInferenceError("cannot bootstrap an empty reader/retriever cell")
    weights = np.zeros((replicates, len(data.clusters)), dtype=np.int32)
    rng = np.random.Generator(np.random.PCG64(rng_seed))
    strata: list[np.ndarray] = []
    for seed in sorted(set(int(value) for value in data.seeds)):
        for event_type in EVENT_TYPES:
            indices = np.flatnonzero(
                (data.seeds == seed) & (data.event_types == event_type)
            )
            if indices.size:
                strata.append(indices)
    for start in range(0, replicates, batch_size):
        stop = min(start + batch_size, replicates)
        batch = stop - start
        for indices in strata:
            draws = draw_cluster_indices(rng, len(indices), batch)
            for local_replicate, draw in enumerate(draws):
                weights[start + local_replicate, indices] = np.bincount(
                    draw, minlength=len(indices)
                )
    return weights


def _point_and_samples(
    data: ReaderRetrieverData,
    weights: np.ndarray,
    *,
    method: str,
    query_type: str,
    metric: str,
) -> tuple[float, np.ndarray, int, int, int]:
    midx = data.method_index(method)
    numerators = data.numerators[(query_type, metric)][:, midx]
    denominators = data.denominators[(query_type, metric)][:, midx]
    numerator = int(numerators.sum())
    denominator = int(denominators.sum())
    point = numerator / denominator if denominator else math.nan
    bootstrap_numerators = weights @ numerators
    bootstrap_denominators = weights @ denominators
    samples = np.divide(
        bootstrap_numerators,
        bootstrap_denominators,
        out=np.full(weights.shape[0], np.nan, dtype=np.float64),
        where=bootstrap_denominators > 0,
    )
    supported_clusters = int(np.count_nonzero(denominators))
    return point, samples, numerator, denominator, supported_clusters


def cell_metric_rows(
    data: ReaderRetrieverData, weights: np.ndarray
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for method in data.methods:
        for query_type in (*data.query_types, ALL_QUERY_TYPES):
            for metric in METRICS:
                point, samples, numerator, denominator, supported_clusters = (
                    _point_and_samples(
                        data,
                        weights,
                        method=method,
                        query_type=query_type,
                        metric=metric,
                    )
                )
                if denominator == 0:
                    continue
                ci_low, ci_high, valid_replicates = percentile_interval(samples)
                rows.append(
                    {
                        "reader_id": data.reader_id,
                        "retriever": data.retriever,
                        "method": method,
                        "query_type": query_type,
                        "pooled_query_types": query_type == ALL_QUERY_TYPES,
                        "metric": metric,
                        "estimate": point,
                        "ci_low": ci_low,
                        "ci_high": ci_high,
                        "confidence_level": CONFIDENCE_LEVEL,
                        "numerator": numerator,
                        "denominator": denominator,
                        "event_clusters": len(data.clusters),
                        "supported_event_clusters": supported_clusters,
                        "bootstrap_valid_replicates": valid_replicates,
                        "exploratory": data.exploratory,
                    }
                )
    return rows


def contrast_rows(
    data: ReaderRetrieverData, weights: np.ndarray
) -> list[dict[str, Any]]:
    """Compute current-minus-old contrasts under one paired draw plan."""

    rows: list[dict[str, Any]] = []
    for query_type in CONTRAST_QUERY_TYPES:
        if query_type not in data.query_types:
            continue
        for metric in CONTRAST_METRICS:
            for current_method in CURRENT_METHODS:
                current_point, current_samples, _, current_denominator, _ = (
                    _point_and_samples(
                        data,
                        weights,
                        method=current_method,
                        query_type=query_type,
                        metric=metric,
                    )
                )
                for old_method in OLD_STORE_METHODS:
                    old_point, old_samples, _, old_denominator, _ = (
                        _point_and_samples(
                            data,
                            weights,
                            method=old_method,
                            query_type=query_type,
                            metric=metric,
                        )
                    )
                    if current_denominator == 0 or old_denominator == 0:
                        continue
                    samples = current_samples - old_samples
                    ci_low, ci_high, valid_replicates = percentile_interval(samples)
                    rows.append(
                        {
                            "reader_id": data.reader_id,
                            "retriever": data.retriever,
                            "query_type": query_type,
                            "metric": metric,
                            "current_method": current_method,
                            "old_store_method": old_method,
                            "contrast_direction": "current_retrieval-minus-old_store",
                            "current_estimate": current_point,
                            "old_store_estimate": old_point,
                            "difference": current_point - old_point,
                            "ci_low": ci_low,
                            "ci_high": ci_high,
                            "confidence_level": CONFIDENCE_LEVEL,
                            "current_denominator": current_denominator,
                            "old_store_denominator": old_denominator,
                            "event_clusters": len(data.clusters),
                            "bootstrap_valid_replicates": valid_replicates,
                            "exploratory": data.exploratory,
                        }
                    )
    return rows


def analyze_loaded_input(
    loaded: LoadedInput, *, replicates: int, rng_seed: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    cell_rows: list[dict[str, Any]] = []
    contrasts: list[dict[str, Any]] = []
    for cell_key in sorted(loaded.cells):
        data = loaded.cells[cell_key]
        weights = build_bootstrap_weights(
            data, replicates=replicates, rng_seed=rng_seed
        )
        cell_rows.extend(cell_metric_rows(data, weights))
        contrasts.extend(contrast_rows(data, weights))
    return cell_rows, contrasts


def _source_manifest_entry(source: SourceInput) -> dict[str, Any]:
    return {
        "metrics_path": str(source.metrics_path),
        "metrics_sha256": source.metrics_sha256,
        "row_count": source.row_count,
        "manifest_path": str(source.manifest_path) if source.manifest_path else None,
        "manifest_sha256": source.manifest_sha256,
        "source_git_sha": source.source_git_sha,
        "source_git_dirty": source.source_git_dirty,
        "source_exploratory": source.source_exploratory,
        "source_preregistered_closure": source.source_preregistered_closure,
    }


def run_analysis(args: argparse.Namespace) -> dict[str, Any]:
    if args.output_dir.exists():
        raise LLMInferenceError(
            f"refusing to overwrite output directory: {args.output_dir}"
        )
    if args.bootstrap_replicates <= 0:
        raise LLMInferenceError("bootstrap_replicates must be positive")
    if not args.run_id:
        raise LLMInferenceError("run_id must be non-empty")

    started = utc_now()
    loaded = load_input(args.input_metrics)
    cell_rows, contrasts = analyze_loaded_input(
        loaded,
        replicates=args.bootstrap_replicates,
        rng_seed=args.bootstrap_seed,
    )

    args.output_dir.mkdir(parents=True)
    cell_filename = "llm_cell_cluster_ci.csv"
    contrast_filename = "llm_current_vs_old_contrasts.csv"
    write_csv(args.output_dir / cell_filename, cell_rows)
    write_csv(args.output_dir / contrast_filename, contrasts)

    analysis_git = git_metadata()
    source_manifests_present = all(
        source.manifest_path is not None for source in loaded.sources
    )
    source_git_clean = source_manifests_present and all(
        source.source_git_dirty is False for source in loaded.sources
    )
    analysis_git_clean = analysis_git.get("git_dirty") is False
    paper_facing_eligible = (
        analysis_git_clean and source_git_clean and not loaded.exploratory
    )
    preregistered_closure = (
        not loaded.exploratory
        and source_manifests_present
        and all(
            source.source_preregistered_closure is True
            for source in loaded.sources
        )
    )
    protocol = {
        "cluster_key": ["seed", "event_id"],
        "strata": ["seed", "event_type"],
        "paired_across": ["method", "query_type", "metric", "row"],
        "reader_policy": "separate inference per reader_id; never pool readers",
        "retriever_policy": (
            "separate inference per retriever plus within-reader ALL-retriever pooling"
        ),
        "estimand": "finite-row-weighted ratio after complete-cluster resampling",
        "bootstrap_replicates": args.bootstrap_replicates,
        "bootstrap_rng_seed": args.bootstrap_seed,
        "bootstrap_batch_size": BOOTSTRAP_BATCH_SIZE,
        "rng_algorithm": RNG_ALGORITHM,
        "confidence_level": CONFIDENCE_LEVEL,
        "interval": "percentile",
        "quantile_method": QUANTILE_METHOD,
        "contract_source": (
            "cluster_inference.py bootstrap_metric_means/draw_cluster_indices; "
            "replicated as explicit cluster multiplicities"
        ),
        "noncommitted_scoring_classes": sorted(NONCOMMITTED_CLASSES),
        "obsolete_rate_query_support": sorted(OBSOLETE_QUERY_TYPES),
        "contrast_direction": "current_retrieval-minus-old_store",
        "current_retrieval_methods": list(CURRENT_METHODS),
        "old_store_methods": list(OLD_STORE_METHODS),
        "contrast_query_types": list(CONTRAST_QUERY_TYPES),
        "contrast_metrics": list(CONTRAST_METRICS),
    }
    manifest = {
        "experiment": "controlled_document_llm_event_cluster_inference",
        "run_id": args.run_id,
        "status": "COMPLETE",
        "started_at_utc": started,
        "completed_at_utc": utc_now(),
        "input_metrics": str(args.input_metrics.resolve()),
        "inputs": [_source_manifest_entry(source) for source in loaded.sources],
        "input_row_count": loaded.row_count,
        "reader_ids": sorted({key[0] for key in loaded.cells}),
        "reader_retriever_cells": [
            {"reader_id": key[0], "retriever": key[1]}
            for key in sorted(loaded.cells)
        ],
        "exploratory": loaded.exploratory,
        "preregistered_closure": preregistered_closure,
        "paper_facing_eligible": paper_facing_eligible,
        "paper_facing_requirement": (
            "All source manifests and this analysis must record git_dirty=false, "
            "and every input row must record exploratory=false."
        ),
        "protocol": protocol,
        "protocol_hash": stable_json_hash(protocol),
        "git": analysis_git,
        "source_code": {
            "llm_cluster_inference_path": str(Path(__file__).resolve()),
            "llm_cluster_inference_sha256": sha256_file(Path(__file__)),
        },
        "environment": {
            "command": " ".join([sys.executable, *sys.argv]),
            "hostname": platform.node(),
            "python": sys.version,
            "numpy": np.__version__,
            "platform": platform.platform(),
            "pid": os.getpid(),
        },
        "outputs": [cell_filename, contrast_filename, "analysis_manifest.json", "run.log"],
        "claim_boundary": (
            "Exploratory post-hoc controlled-corpus LLM inference; this output is "
            "not a pre-registered CL closure."
            if loaded.exploratory
            else "Controlled-corpus LLM inference only; no production-RAG or external-corpus claim."
        ),
    }
    write_json(args.output_dir / "analysis_manifest.json", jsonable(manifest))
    (args.output_dir / "run.log").write_text(
        "\n".join(
            [
                f"[start] {started}",
                f"[run_id] {args.run_id}",
                f"[inputs] files={len(loaded.sources)} rows={loaded.row_count}",
                f"[readers] {','.join(manifest['reader_ids'])}",
                "[inventory] PASS: paired method coverage within every reader/retriever",
                f"[bootstrap] B={args.bootstrap_replicates} seed={args.bootstrap_seed}",
                f"[exploratory] {loaded.exploratory}",
                f"[paper_facing_eligible] {paper_facing_eligible}",
                f"[done] {utc_now()}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-metrics", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--bootstrap-replicates", type=int, default=BOOTSTRAP_REPLICATES
    )
    parser.add_argument("--bootstrap-seed", type=int, default=BOOTSTRAP_SEED)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = run_analysis(args)
    print(
        json.dumps(
            {
                "run_id": manifest["run_id"],
                "status": manifest["status"],
                "exploratory": manifest["exploratory"],
                "paper_facing_eligible": manifest["paper_facing_eligible"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
