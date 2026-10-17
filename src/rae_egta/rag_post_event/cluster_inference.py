#!/usr/bin/env python3
"""Paired event-cluster inference for the controlled document-RAG formal runs.

The preserved metric CSVs contain several rows per query, method, and event.
This module keeps the existing finite-row-weighted estimands while changing the
resampling unit to the complete ``(seed, event_id)`` cluster.  Resampling is
paired across methods and stratified by ``seed x event_type``.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from rae_egta.rag_post_event.summarize import spearman


ADD = "ADD"
REVISE = "REVISE"
RETIRE = "RETIRE"
EVENT_TYPES = (ADD, REVISE, RETIRE)
SHIFTED_EVENT_TYPES = (REVISE, RETIRE)

TARGET = "target"
STABLE = "current_valid_stable"
OBSOLETE = "obsolete_unqualified"
HISTORICAL = "historical"

EXPECTED_SEEDS = (13, 21, 34)
EXPECTED_EVENTS_PER_SEED = 1500
EXPECTED_EVENT_COUNTS = {ADD: 450, REVISE: 2250, RETIRE: 1800}

RQ1_METHODS = (
    "no_update",
    "store_only",
    "stale_index",
    "naive_update",
    "query_rewrite_update",
    "old_ref_reg_0p70",
    "postevent_ref_reg_0p85",
    "old_ref_rewrite_reg_0p70",
    "postevent_rewrite_reg_0p85",
)

RQ2_METHODS = (
    "no_update",
    "store_only",
    "query_rewrite_update",
    "oldref_adapted_preserver",
    "postevent_validity_filter",
    "intent_aware_postevent_filter",
)

OLD_METHOD = "oldref_adapted_preserver"
POST_METHOD = "postevent_validity_filter"
STORE_METHOD = "store_only"

RQ1_METRICS = ("old_reference_retention", "current_valid_accuracy")
CONDITIONAL_METRICS = (
    "target_success",
    "obsolete_answer_rate",
    "evidence_hit",
    "cvbd",
    "historical_accuracy",
)
ALL_METRICS = RQ1_METRICS + CONDITIONAL_METRICS

BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 20_260_714
CONFIDENCE_LEVEL = 0.95
TIE_EPSILON = 1e-12
QUANTILE_METHOD = "linear"
RNG_ALGORITHM = "PCG64"

R2_MATCH_TOLERANCE = 0.03
R2_EVIDENCE_FLOOR = -0.01
R2_CVBD_CEILING = 0.01
R2_HISTORICAL_FLOOR = -0.02
R3_TARGET_HIGH = 0.95
R3_TARGET_LOW = 0.05
R3_OBSOLETE_HIGH = 0.95
R3_OBSOLETE_LOW = 0.05
R3_HISTORICAL_FLOOR = 0.98

REQUIRED_BASE_FIELDS = {
    "seed",
    "event_id",
    "event_type",
    "query_id",
    "query_type",
    "method",
    "old_reference_retention",
    "current_valid_accuracy",
    "target_success",
    "obsolete_answer_rate",
    "evidence_hit",
    "cvbd",
}


class InferenceValidationError(RuntimeError):
    """Raised when a formal input or confirmatory contrast is not paired."""


@dataclass(frozen=True, order=True)
class ClusterKey:
    seed: int
    event_id: str


@dataclass
class Inventory:
    path: Path
    row_count: int
    methods: tuple[str, ...]
    clusters: tuple[ClusterKey, ...]
    event_type_by_cluster: dict[ClusterKey, str]
    cluster_row_counts: dict[ClusterKey, int]
    query_count: int
    query_type_counts: dict[str, int]
    sha256: str


@dataclass
class AggregatedData:
    inventory: Inventory
    methods: tuple[str, ...]
    metrics: tuple[str, ...]
    sums: np.ndarray
    finite_counts: np.ndarray
    seeds: np.ndarray
    event_types: np.ndarray

    def method_index(self, method: str) -> int:
        return self.methods.index(method)

    def metric_index(self, metric: str) -> int:
        return self.metrics.index(metric)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git_state(project_root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.check_output(
                ["git", "-C", str(project_root), *args],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    sha = run("rev-parse", "HEAD")
    status = run("status", "--short") if sha else ""
    return {
        "git_available": bool(sha),
        "git_sha": sha or "unavailable",
        "git_dirty": bool(status),
        "git_status_short": status,
    }


def parse_float(value: str | None) -> float:
    if value is None or value == "" or value.lower() == "nan":
        return math.nan
    try:
        return float(value)
    except ValueError as exc:
        raise InferenceValidationError(f"non-numeric metric value: {value!r}") from exc


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for field in row:
            if field not in fieldnames:
                fieldnames.append(field)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def metric_for_row(query_type: str) -> tuple[str, ...]:
    if query_type == TARGET:
        return ("target_success", "evidence_hit")
    if query_type == OBSOLETE:
        return ("obsolete_answer_rate",)
    if query_type == STABLE:
        return ("cvbd",)
    if query_type == HISTORICAL:
        return ("historical_accuracy",)
    return ()


def audit_input(
    path: Path,
    expected_methods: Sequence[str],
    *,
    require_historical: bool,
    expected_seeds: Sequence[int] | None = None,
    expected_events_per_seed: int | None = None,
    expected_event_counts: Mapping[str, int] | None = None,
) -> Inventory:
    """Audit uniqueness, full paired coverage, and cluster identity."""

    if not path.is_file() or path.stat().st_size == 0:
        raise InferenceValidationError(f"missing or empty input: {path}")
    expected_method_set = set(expected_methods)
    required = set(REQUIRED_BASE_FIELDS)
    if require_historical:
        required.add("historical_accuracy")

    seen_rows: set[tuple[int, str, str, str]] = set()
    cluster_types: dict[ClusterKey, str] = {}
    cluster_method_counts: dict[ClusterKey, Counter[str]] = defaultdict(Counter)
    query_methods: dict[tuple[ClusterKey, str], set[str]] = defaultdict(set)
    query_types: dict[tuple[ClusterKey, str], str] = {}
    cluster_row_counts: Counter[ClusterKey] = Counter()
    methods: set[str] = set()
    row_count = 0

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise InferenceValidationError(f"{path}: missing fields {missing}")
        for row_number, row in enumerate(reader, start=2):
            row_count += 1
            try:
                seed = int(row["seed"])
            except (TypeError, ValueError) as exc:
                raise InferenceValidationError(
                    f"{path}:{row_number}: invalid seed"
                ) from exc
            event_id = row["event_id"]
            event_type = row["event_type"]
            query_id = row["query_id"]
            query_type = row["query_type"]
            method = row["method"]
            if not event_id or not query_id or event_type not in EVENT_TYPES:
                raise InferenceValidationError(
                    f"{path}:{row_number}: invalid event/query identity"
                )
            key = ClusterKey(seed=seed, event_id=event_id)
            row_key = (seed, event_id, query_id, method)
            if row_key in seen_rows:
                raise InferenceValidationError(f"duplicate row key: {row_key}")
            seen_rows.add(row_key)
            previous_event_type = cluster_types.setdefault(key, event_type)
            if previous_event_type != event_type:
                raise InferenceValidationError(f"cluster changes event_type: {key}")
            query_key = (key, query_id)
            previous_query_type = query_types.setdefault(query_key, query_type)
            if previous_query_type != query_type:
                raise InferenceValidationError(f"query changes query_type: {query_key}")
            methods.add(method)
            cluster_method_counts[key][method] += 1
            query_methods[query_key].add(method)
            cluster_row_counts[key] += 1

    if methods != expected_method_set:
        raise InferenceValidationError(
            f"method set mismatch: expected={sorted(expected_method_set)} observed={sorted(methods)}"
        )
    for query_key, observed in query_methods.items():
        if observed != expected_method_set:
            raise InferenceValidationError(
                f"unpaired query coverage at {query_key}: {sorted(observed)}"
            )
    for key, counts in cluster_method_counts.items():
        if set(counts) != expected_method_set or len(set(counts.values())) != 1:
            raise InferenceValidationError(
                f"unpaired cluster/method row coverage at {key}: {dict(counts)}"
            )

    clusters = tuple(sorted(cluster_types))
    observed_seeds = sorted({key.seed for key in clusters})
    if expected_seeds is not None and observed_seeds != sorted(expected_seeds):
        raise InferenceValidationError(
            f"seed mismatch: expected={sorted(expected_seeds)} observed={observed_seeds}"
        )
    if expected_events_per_seed is not None:
        seed_counts = Counter(key.seed for key in clusters)
        expected = {
            seed: expected_events_per_seed for seed in expected_seeds or observed_seeds
        }
        if dict(sorted(seed_counts.items())) != dict(sorted(expected.items())):
            raise InferenceValidationError(
                f"per-seed cluster mismatch: expected={expected} observed={dict(seed_counts)}"
            )
    if expected_event_counts is not None:
        observed = Counter(cluster_types[key] for key in clusters)
        if dict(observed) != dict(expected_event_counts):
            raise InferenceValidationError(
                f"event-type cluster mismatch: expected={dict(expected_event_counts)} observed={dict(observed)}"
            )

    return Inventory(
        path=path,
        row_count=row_count,
        methods=tuple(expected_methods),
        clusters=clusters,
        event_type_by_cluster=cluster_types,
        cluster_row_counts=dict(cluster_row_counts),
        query_count=len(query_methods),
        query_type_counts=dict(Counter(query_types.values())),
        sha256=sha256_file(path),
    )


def audit_source_manifest(path: Path, inventory: Inventory) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        raise InferenceValidationError(f"missing or empty source manifest: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InferenceValidationError(f"invalid source manifest JSON: {path}") from exc
    if int(payload.get("row_count", -1)) != inventory.row_count:
        raise InferenceValidationError(f"source manifest row_count mismatch: {path}")
    if int(payload.get("event_record_count", -1)) != len(inventory.clusters):
        raise InferenceValidationError(
            f"source manifest event_record_count mismatch: {path}"
        )
    if set(payload.get("methods", ())) != set(inventory.methods):
        raise InferenceValidationError(f"source manifest method set mismatch: {path}")
    if payload.get("git", {}).get("git_dirty") is not False:
        raise InferenceValidationError(f"source formal run is not clean: {path}")
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "source_git_sha": payload.get("git", {}).get("git_sha", "unavailable"),
        "source_git_dirty": payload.get("git", {}).get("git_dirty"),
        "dataset_spec_hash": payload.get("dataset_spec_hash", ""),
    }


def aggregate_input(inventory: Inventory) -> AggregatedData:
    """Pre-aggregate finite sums/counts without changing the row-mean estimand."""

    cluster_index = {key: index for index, key in enumerate(inventory.clusters)}
    method_index = {method: index for index, method in enumerate(inventory.methods)}
    metric_index = {metric: index for index, metric in enumerate(ALL_METRICS)}
    shape = (len(inventory.clusters), len(inventory.methods), len(ALL_METRICS))
    sums = np.zeros(shape, dtype=np.float64)
    counts = np.zeros(shape, dtype=np.int64)
    finite_support: dict[tuple[ClusterKey, str, str], set[str]] = defaultdict(set)
    present_support: dict[tuple[ClusterKey, str, str], set[str]] = defaultdict(set)

    with inventory.path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = ClusterKey(seed=int(row["seed"]), event_id=row["event_id"])
            cidx = cluster_index[key]
            method = row["method"]
            midx = method_index[method]
            labels = RQ1_METRICS + metric_for_row(row["query_type"])
            for metric in labels:
                support_key = (key, row["query_id"], metric)
                present_support[support_key].add(method)
                value = parse_float(row.get(metric))
                if math.isfinite(value):
                    sums[cidx, midx, metric_index[metric]] += value
                    counts[cidx, midx, metric_index[metric]] += 1
                    finite_support[support_key].add(method)

    expected = set(inventory.methods)
    confirmatory = {OLD_METHOD, POST_METHOD}
    for support_key, present in present_support.items():
        if present != expected:
            raise InferenceValidationError(
                f"internal query coverage mismatch at {support_key}"
            )
        finite = finite_support.get(support_key, set())
        metric = support_key[2]
        if metric in RQ1_METRICS and finite not in (set(), expected):
            raise InferenceValidationError(
                f"method-specific finite mask for {metric} at {support_key[:2]}: {sorted(finite)}"
            )
        if confirmatory.issubset(expected):
            paired_finite = finite & confirmatory
            if paired_finite not in (set(), confirmatory):
                raise InferenceValidationError(
                    f"unpaired confirmatory finite mask for {metric} at {support_key[:2]}: "
                    f"{sorted(paired_finite)}"
                )

    return AggregatedData(
        inventory=inventory,
        methods=inventory.methods,
        metrics=ALL_METRICS,
        sums=sums,
        finite_counts=counts,
        seeds=np.asarray([key.seed for key in inventory.clusters], dtype=np.int64),
        event_types=np.asarray(
            [inventory.event_type_by_cluster[key] for key in inventory.clusters],
            dtype=object,
        ),
    )


def cluster_mask(
    data: AggregatedData,
    *,
    event_types: Iterable[str] | None = None,
    seed: int | None = None,
) -> np.ndarray:
    mask = np.ones(len(data.inventory.clusters), dtype=bool)
    if event_types is not None:
        mask &= np.isin(data.event_types, list(event_types))
    if seed is not None:
        mask &= data.seeds == seed
    return mask


def point_means(
    data: AggregatedData,
    methods: Sequence[str],
    metrics: Sequence[str],
    mask: np.ndarray,
) -> np.ndarray:
    method_ids = [data.method_index(method) for method in methods]
    metric_ids = [data.metric_index(metric) for metric in metrics]
    sums = data.sums[np.ix_(mask, method_ids, metric_ids)].sum(axis=0)
    counts = data.finite_counts[np.ix_(mask, method_ids, metric_ids)].sum(axis=0)
    return np.divide(
        sums,
        counts,
        out=np.full(sums.shape, np.nan, dtype=np.float64),
        where=counts > 0,
    )


def point_counts(
    data: AggregatedData,
    methods: Sequence[str],
    metrics: Sequence[str],
    mask: np.ndarray,
) -> np.ndarray:
    method_ids = [data.method_index(method) for method in methods]
    metric_ids = [data.metric_index(metric) for metric in metrics]
    return data.finite_counts[np.ix_(mask, method_ids, metric_ids)].sum(axis=0)


def tie_set(
    methods: Sequence[str], scores: Sequence[float], *, epsilon: float = TIE_EPSILON
) -> tuple[str, ...]:
    finite = [
        (method, float(score))
        for method, score in zip(methods, scores)
        if math.isfinite(float(score))
    ]
    if not finite:
        return ()
    optimum = max(score for _, score in finite)
    return tuple(
        sorted(method for method, score in finite if optimum - score <= epsilon)
    )


def ranking_statistics(
    methods: Sequence[str],
    old_scores: Sequence[float],
    current_scores: Sequence[float],
    *,
    epsilon: float = TIE_EPSILON,
) -> dict[str, Any]:
    old_ties = tie_set(methods, old_scores, epsilon=epsilon)
    current_ties = tie_set(methods, current_scores, epsilon=epsilon)
    if not old_ties or not current_ties:
        return {
            "historical_tie_set": old_ties,
            "current_tie_set": current_ties,
            "spearman": math.nan,
            "tie_robust_regret": math.nan,
            "inversion": False,
        }
    score_by_method = {
        method: float(score) for method, score in zip(methods, current_scores)
    }
    best_current = max(score_by_method[method] for method in current_ties)
    old_ties_best_current = max(score_by_method[method] for method in old_ties)
    regret = best_current - old_ties_best_current
    if abs(regret) <= epsilon:
        regret = 0.0
    return {
        "historical_tie_set": old_ties,
        "current_tie_set": current_ties,
        "spearman": spearman(
            [float(v) for v in old_scores], [float(v) for v in current_scores]
        ),
        "tie_robust_regret": regret,
        "inversion": set(old_ties).isdisjoint(current_ties),
    }


def draw_cluster_indices(
    rng: np.random.Generator, n_clusters: int, n_replicates: int
) -> np.ndarray:
    return rng.integers(0, n_clusters, size=(n_replicates, n_clusters), endpoint=False)


def resample_sum_count(
    sums: np.ndarray, counts: np.ndarray, draws: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Apply complete-cluster multiplicities to pre-aggregated sums/counts."""

    if sums.shape != counts.shape or sums.shape[0] == 0:
        raise ValueError("sums/counts must share a non-empty cluster dimension")
    if draws.ndim != 2 or np.any(draws < 0) or np.any(draws >= sums.shape[0]):
        raise ValueError("invalid cluster draws")
    return sums[draws].sum(axis=1), counts[draws].sum(axis=1)


def strata_indices(data: AggregatedData) -> list[tuple[tuple[int, str], np.ndarray]]:
    out: list[tuple[tuple[int, str], np.ndarray]] = []
    for seed in sorted(set(int(value) for value in data.seeds)):
        for event_type in EVENT_TYPES:
            indices = np.flatnonzero(
                (data.seeds == seed) & (data.event_types == event_type)
            )
            if indices.size:
                out.append(((seed, event_type), indices))
    return out


def bootstrap_metric_means(
    data: AggregatedData,
    *,
    methods: Sequence[str],
    metrics: Sequence[str],
    scopes: Mapping[str, Sequence[str]],
    replicates: int,
    rng_seed: int,
    batch_size: int,
) -> dict[str, np.ndarray]:
    """Bootstrap row-weighted method means using one paired draw plan."""

    if replicates <= 0 or batch_size <= 0:
        raise ValueError("replicates and batch_size must be positive")
    method_ids = [data.method_index(method) for method in methods]
    metric_ids = [data.metric_index(metric) for metric in metrics]
    scope_sets = {scope: set(event_types) for scope, event_types in scopes.items()}
    output = {
        scope: np.full(
            (replicates, len(methods), len(metrics)), np.nan, dtype=np.float64
        )
        for scope in scopes
    }
    selected_sums = data.sums[:, method_ids][:, :, metric_ids]
    selected_counts = data.finite_counts[:, method_ids][:, :, metric_ids]
    rng = np.random.Generator(np.random.PCG64(rng_seed))

    for start in range(0, replicates, batch_size):
        stop = min(start + batch_size, replicates)
        batch = stop - start
        numerator = {
            scope: np.zeros((batch, len(methods), len(metrics)), dtype=np.float64)
            for scope in scopes
        }
        denominator = {
            scope: np.zeros((batch, len(methods), len(metrics)), dtype=np.int64)
            for scope in scopes
        }
        for (_, event_type), indices in strata_indices(data):
            draws = draw_cluster_indices(rng, len(indices), batch)
            stratum_sums, stratum_counts = resample_sum_count(
                selected_sums[indices], selected_counts[indices], draws
            )
            for scope, event_types in scope_sets.items():
                if event_type in event_types:
                    numerator[scope] += stratum_sums
                    denominator[scope] += stratum_counts
        for scope in scopes:
            output[scope][start:stop] = np.divide(
                numerator[scope],
                denominator[scope],
                out=np.full(numerator[scope].shape, np.nan, dtype=np.float64),
                where=denominator[scope] > 0,
            )
    return output


def percentile_interval(
    samples: Sequence[float], confidence: float = CONFIDENCE_LEVEL
) -> tuple[float, float, int]:
    values = np.asarray(samples, dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return math.nan, math.nan, 0
    alpha = (1.0 - confidence) / 2.0
    low, high = np.quantile(values, [alpha, 1.0 - alpha], method=QUANTILE_METHOD)
    return float(low), float(high), int(values.size)


def jsonable(value: Any) -> Any:
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not math.isfinite(float(value)) else float(value)
    if isinstance(value, np.ndarray):
        return [jsonable(item) for item in value.tolist()]
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    return value


def serialize_tie_set(methods: Sequence[str]) -> str:
    return json.dumps(list(methods), separators=(",", ":"))


def seed_estimates(
    data: AggregatedData,
    methods: Sequence[str],
    metrics: Sequence[str],
    event_types: Sequence[str],
) -> dict[int, np.ndarray]:
    return {
        seed: point_means(
            data,
            methods,
            metrics,
            cluster_mask(data, event_types=event_types, seed=seed),
        )
        for seed in EXPECTED_SEEDS
    }


def metric_ci_rows_rq1(
    data: AggregatedData, samples: np.ndarray
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    pooled_mask = cluster_mask(data, event_types=EVENT_TYPES)
    point = point_means(data, RQ1_METHODS, RQ1_METRICS, pooled_mask)
    counts = point_counts(data, RQ1_METHODS, RQ1_METRICS, pooled_mask)
    by_seed = seed_estimates(data, RQ1_METHODS, RQ1_METRICS, EVENT_TYPES)
    rows: list[dict[str, Any]] = []
    for method_index, method in enumerate(RQ1_METHODS):
        for metric_index, metric in enumerate(RQ1_METRICS):
            low, high, valid = percentile_interval(
                samples[:, method_index, metric_index]
            )
            seed_values = [
                float(by_seed[seed][method_index, metric_index])
                for seed in EXPECTED_SEEDS
            ]
            rows.append(
                {
                    "analysis": "RQ1_reference_mismatch",
                    "scope": "pooled",
                    "event_type": "ALL",
                    "method": method,
                    "metric": metric,
                    "estimand": "finite_row_weighted_mean",
                    "point_estimate": float(point[method_index, metric_index]),
                    "ci_low": low,
                    "ci_high": high,
                    "confidence_level": CONFIDENCE_LEVEL,
                    "bootstrap_replicates": len(samples),
                    "valid_replicates": valid,
                    "event_cluster_count": int(pooled_mask.sum()),
                    "finite_row_count": int(counts[method_index, metric_index]),
                    "seed_13_estimate": seed_values[0],
                    "seed_21_estimate": seed_values[1],
                    "seed_34_estimate": seed_values[2],
                    "seed_min": min(seed_values),
                    "seed_max": max(seed_values),
                }
            )

    point_ranking = ranking_statistics(RQ1_METHODS, point[:, 0], point[:, 1])
    spearman_samples = np.empty(len(samples), dtype=np.float64)
    regret_samples = np.empty(len(samples), dtype=np.float64)
    inversion_samples = np.empty(len(samples), dtype=np.float64)
    old_set_counts: Counter[tuple[str, ...]] = Counter()
    current_set_counts: Counter[tuple[str, ...]] = Counter()
    for index in range(len(samples)):
        ranking = ranking_statistics(
            RQ1_METHODS, samples[index, :, 0], samples[index, :, 1]
        )
        spearman_samples[index] = float(ranking["spearman"])
        regret_samples[index] = float(ranking["tie_robust_regret"])
        inversion_samples[index] = float(bool(ranking["inversion"]))
        old_set_counts[tuple(ranking["historical_tie_set"])] += 1
        current_set_counts[tuple(ranking["current_tie_set"])] += 1
    spearman_ci = percentile_interval(spearman_samples)
    regret_ci = percentile_interval(regret_samples)

    ranking_rows: list[dict[str, Any]] = []
    for scope, seed in [
        ("pooled", None),
        *[(f"seed_{seed}", seed) for seed in EXPECTED_SEEDS],
    ]:
        mask = cluster_mask(data, event_types=EVENT_TYPES, seed=seed)
        scores = point_means(data, RQ1_METHODS, RQ1_METRICS, mask)
        ranking = ranking_statistics(RQ1_METHODS, scores[:, 0], scores[:, 1])
        row = {
            "analysis": "RQ1_reference_mismatch",
            "scope": scope,
            "seed": "pooled" if seed is None else seed,
            "event_type": "ALL",
            "method_count": len(RQ1_METHODS),
            "event_cluster_count": int(mask.sum()),
            "historical_tie_set": serialize_tie_set(ranking["historical_tie_set"]),
            "current_tie_set": serialize_tie_set(ranking["current_tie_set"]),
            "spearman": ranking["spearman"],
            "tie_robust_regret": ranking["tie_robust_regret"],
            "inversion": ranking["inversion"],
            "spearman_ci_low": math.nan,
            "spearman_ci_high": math.nan,
            "regret_ci_low": math.nan,
            "regret_ci_high": math.nan,
            "bootstrap_inversion_frequency": math.nan,
        }
        if seed is None:
            row.update(
                {
                    "spearman_ci_low": spearman_ci[0],
                    "spearman_ci_high": spearman_ci[1],
                    "regret_ci_low": regret_ci[0],
                    "regret_ci_high": regret_ci[1],
                    "bootstrap_inversion_frequency": float(inversion_samples.mean()),
                }
            )
        ranking_rows.append(row)

    tie_rows: list[dict[str, Any]] = []
    for reference, counts_by_set in (
        ("historical", old_set_counts),
        ("current_valid", current_set_counts),
    ):
        for methods, count in sorted(
            counts_by_set.items(), key=lambda item: (-item[1], item[0])
        ):
            tie_rows.append(
                {
                    "analysis": "RQ1_reference_mismatch",
                    "scope": "bootstrap",
                    "selection_reference": reference,
                    "tie_set": serialize_tie_set(methods),
                    "replicate_count": count,
                    "replicate_frequency": count / len(samples),
                }
            )
    for method_index, method in enumerate(RQ1_METHODS):
        tie_rows.append(
            {
                "analysis": "RQ1_reference_mismatch",
                "scope": "bootstrap_method_membership",
                "selection_reference": "historical",
                "tie_set": serialize_tie_set((method,)),
                "replicate_count": sum(
                    count
                    for methods, count in old_set_counts.items()
                    if method in methods
                ),
                "replicate_frequency": sum(
                    count
                    for methods, count in old_set_counts.items()
                    if method in methods
                )
                / len(samples),
            }
        )
        tie_rows.append(
            {
                "analysis": "RQ1_reference_mismatch",
                "scope": "bootstrap_method_membership",
                "selection_reference": "current_valid",
                "tie_set": serialize_tie_set((method,)),
                "replicate_count": sum(
                    count
                    for methods, count in current_set_counts.items()
                    if method in methods
                ),
                "replicate_frequency": sum(
                    count
                    for methods, count in current_set_counts.items()
                    if method in methods
                )
                / len(samples),
            }
        )

    summary = {
        "point": point_ranking,
        "spearman": {
            "ci_low": spearman_ci[0],
            "ci_high": spearman_ci[1],
            "valid_replicates": spearman_ci[2],
        },
        "tie_robust_regret": {
            "ci_low": regret_ci[0],
            "ci_high": regret_ci[1],
            "valid_replicates": regret_ci[2],
        },
        "bootstrap_inversion_frequency": float(inversion_samples.mean()),
        "positive_interpretation": bool(
            float(point_ranking["tie_robust_regret"]) > TIE_EPSILON
            and regret_ci[0] > TIE_EPSILON
            and bool(point_ranking["inversion"])
        ),
    }
    return rows, ranking_rows + [], tie_rows, summary


def descriptive_method_rows(data: AggregatedData) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for scope, seed in [
        ("pooled", None),
        *[(f"seed_{seed}", seed) for seed in EXPECTED_SEEDS],
    ]:
        mask = cluster_mask(data, event_types=EVENT_TYPES, seed=seed)
        means = point_means(data, data.methods, RQ1_METRICS, mask)
        counts = point_counts(data, data.methods, RQ1_METRICS, mask)
        for index, method in enumerate(data.methods):
            rows.append(
                {
                    "analysis": "RQ1_reference_mismatch",
                    "scope": scope,
                    "seed": "pooled" if seed is None else seed,
                    "method": method,
                    "event_cluster_count": int(mask.sum()),
                    "raw_row_count": int(
                        data.inventory.row_count
                        if seed is None
                        else sum(
                            count
                            for key, count in data.inventory.cluster_row_counts.items()
                            if key.seed == seed
                        )
                    ),
                    "old_reference_retention": float(means[index, 0]),
                    "old_reference_finite_rows": int(counts[index, 0]),
                    "current_valid_accuracy": float(means[index, 1]),
                    "current_valid_finite_rows": int(counts[index, 1]),
                }
            )
    return rows


def pair_delta_samples(
    scope_samples: np.ndarray, methods: Sequence[str]
) -> dict[str, np.ndarray]:
    old = methods.index(OLD_METHOD)
    post = methods.index(POST_METHOD)
    return {
        "target_success_delta": scope_samples[:, post, 0] - scope_samples[:, old, 0],
        "obsolete_answer_rate_reduction": scope_samples[:, old, 1]
        - scope_samples[:, post, 1],
        "evidence_hit_delta": scope_samples[:, post, 2] - scope_samples[:, old, 2],
        "cvbd_delta": scope_samples[:, post, 3] - scope_samples[:, old, 3],
        "historical_accuracy_delta": scope_samples[:, post, 4]
        - scope_samples[:, old, 4],
    }


def pair_point_values(point: np.ndarray, methods: Sequence[str]) -> dict[str, float]:
    old = methods.index(OLD_METHOD)
    post = methods.index(POST_METHOD)
    return {
        "target_success_delta": float(point[post, 0] - point[old, 0]),
        "obsolete_answer_rate_reduction": float(point[old, 1] - point[post, 1]),
        "evidence_hit_delta": float(point[post, 2] - point[old, 2]),
        "cvbd_delta": float(point[post, 3] - point[old, 3]),
        "historical_accuracy_delta": float(point[post, 4] - point[old, 4]),
    }


def pairwise_rows_and_gates(
    data: AggregatedData,
    samples_by_scope: Mapping[str, np.ndarray],
    methods: Sequence[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    scopes = {
        "SHIFTED_REVISE_RETIRE": SHIFTED_EVENT_TYPES,
        ADD: (ADD,),
        REVISE: (REVISE,),
        RETIRE: (RETIRE,),
    }
    directions = {
        "target_success_delta": "postevent-oldref",
        "obsolete_answer_rate_reduction": "oldref-postevent",
        "evidence_hit_delta": "postevent-oldref",
        "cvbd_delta": "postevent-oldref",
        "historical_accuracy_delta": "postevent-oldref",
    }
    pair_rows: list[dict[str, Any]] = []
    absolute_rows: list[dict[str, Any]] = []
    scope_records: dict[str, dict[str, dict[str, Any]]] = {}
    absolute_records: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}

    for scope, event_types in scopes.items():
        mask = cluster_mask(data, event_types=event_types)
        point = point_means(data, methods, CONDITIONAL_METRICS, mask)
        counts = point_counts(data, methods, CONDITIONAL_METRICS, mask)
        delta_point = pair_point_values(point, methods)
        delta_samples = pair_delta_samples(samples_by_scope[scope], methods)
        by_seed = seed_estimates(data, methods, CONDITIONAL_METRICS, event_types)
        seed_delta = {
            seed: pair_point_values(by_seed[seed], methods) for seed in EXPECTED_SEEDS
        }
        scope_records[scope] = {}
        for metric, values in delta_samples.items():
            structural_na = scope == ADD and metric in {
                "obsolete_answer_rate_reduction",
                "historical_accuracy_delta",
            }
            low, high, valid = percentile_interval(values)
            seed_values = [seed_delta[seed][metric] for seed in EXPECTED_SEEDS]
            if structural_na:
                low = high = math.nan
                valid = 0
            finite_seed_values = [
                value for value in seed_values if math.isfinite(value)
            ]
            record = {
                "analysis": "RQ2_matched_success"
                if scope == "SHIFTED_REVISE_RETIRE"
                else "RQ3_event_boundary",
                "scope": scope,
                "event_type": "REVISE_RETIRE"
                if scope == "SHIFTED_REVISE_RETIRE"
                else scope,
                "event_boundary_role": (
                    "pooled_positive_conflict"
                    if scope == "SHIFTED_REVISE_RETIRE"
                    else "negative_control"
                    if scope == ADD
                    else "positive_conflict"
                ),
                "metric": metric,
                "estimand": "paired_finite_row_weighted_delta",
                "filter": {
                    "target_success_delta": "query_type=target",
                    "obsolete_answer_rate_reduction": "query_type=obsolete_unqualified",
                    "evidence_hit_delta": "query_type=target",
                    "cvbd_delta": "query_type=current_valid_stable",
                    "historical_accuracy_delta": "query_type=historical",
                }[metric],
                "old_method": OLD_METHOD,
                "postevent_method": POST_METHOD,
                "direction": directions[metric],
                "status": "not_applicable" if structural_na else "estimated",
                "point_estimate": math.nan if structural_na else delta_point[metric],
                "ci_low": low,
                "ci_high": high,
                "confidence_level": CONFIDENCE_LEVEL,
                "bootstrap_replicates": len(values),
                "valid_replicates": valid,
                "event_cluster_count": int(mask.sum()),
                "finite_row_count_old": int(
                    counts[
                        methods.index(OLD_METHOD),
                        CONDITIONAL_METRICS.index(
                            {
                                "target_success_delta": "target_success",
                                "obsolete_answer_rate_reduction": "obsolete_answer_rate",
                                "evidence_hit_delta": "evidence_hit",
                                "cvbd_delta": "cvbd",
                                "historical_accuracy_delta": "historical_accuracy",
                            }[metric]
                        ),
                    ]
                ),
                "finite_row_count_postevent": int(
                    counts[
                        methods.index(POST_METHOD),
                        CONDITIONAL_METRICS.index(
                            {
                                "target_success_delta": "target_success",
                                "obsolete_answer_rate_reduction": "obsolete_answer_rate",
                                "evidence_hit_delta": "evidence_hit",
                                "cvbd_delta": "cvbd",
                                "historical_accuracy_delta": "historical_accuracy",
                            }[metric]
                        ),
                    ]
                ),
                "seed_13_estimate": seed_values[0],
                "seed_21_estimate": seed_values[1],
                "seed_34_estimate": seed_values[2],
                "seed_min": min(finite_seed_values) if finite_seed_values else math.nan,
                "seed_max": max(finite_seed_values) if finite_seed_values else math.nan,
            }
            pair_rows.append(record)
            scope_records[scope][metric] = record

        if scope in EVENT_TYPES:
            absolute_records[scope] = {}
            for method in (STORE_METHOD, OLD_METHOD, POST_METHOD):
                method_index = methods.index(method)
                for metric in (
                    "target_success",
                    "obsolete_answer_rate",
                    "historical_accuracy",
                ):
                    metric_index = CONDITIONAL_METRICS.index(metric)
                    structural_na = scope == ADD and metric in {
                        "obsolete_answer_rate",
                        "historical_accuracy",
                    }
                    values = samples_by_scope[scope][:, method_index, metric_index]
                    low, high, valid = percentile_interval(values)
                    seed_values = [
                        float(by_seed[seed][method_index, metric_index])
                        for seed in EXPECTED_SEEDS
                    ]
                    if structural_na:
                        low = high = math.nan
                        valid = 0
                    finite_seed_values = [
                        value for value in seed_values if math.isfinite(value)
                    ]
                    row = {
                        "analysis": "RQ3_event_boundary",
                        "scope": scope,
                        "event_type": scope,
                        "event_boundary_role": "negative_control"
                        if scope == ADD
                        else "positive_conflict",
                        "method": method,
                        "metric": metric,
                        "estimand": "finite_row_weighted_mean",
                        "status": "not_applicable" if structural_na else "estimated",
                        "point_estimate": math.nan
                        if structural_na
                        else float(point[method_index, metric_index]),
                        "ci_low": low,
                        "ci_high": high,
                        "confidence_level": CONFIDENCE_LEVEL,
                        "bootstrap_replicates": len(values),
                        "valid_replicates": valid,
                        "event_cluster_count": int(mask.sum()),
                        "finite_row_count": int(counts[method_index, metric_index]),
                        "seed_13_estimate": seed_values[0],
                        "seed_21_estimate": seed_values[1],
                        "seed_34_estimate": seed_values[2],
                        "seed_min": min(finite_seed_values)
                        if finite_seed_values
                        else math.nan,
                        "seed_max": max(finite_seed_values)
                        if finite_seed_values
                        else math.nan,
                    }
                    absolute_rows.append(row)
                    absolute_records[scope][(method, metric)] = row

    rq2 = scope_records["SHIFTED_REVISE_RETIRE"]
    rq2_gate = {
        "target_ci_within_margin": (
            rq2["target_success_delta"]["ci_low"] >= -R2_MATCH_TOLERANCE
            and rq2["target_success_delta"]["ci_high"] <= R2_MATCH_TOLERANCE
        ),
        "obsolete_reduction_ci_positive": rq2["obsolete_answer_rate_reduction"][
            "ci_low"
        ]
        > 0.0,
        "evidence_ci_noninferior": rq2["evidence_hit_delta"]["ci_low"]
        >= R2_EVIDENCE_FLOOR,
        "cvbd_ci_within_ceiling": rq2["cvbd_delta"]["ci_high"] <= R2_CVBD_CEILING,
        "historical_ci_noninferior": rq2["historical_accuracy_delta"]["ci_low"]
        >= R2_HISTORICAL_FLOOR,
    }
    rq2_gate["pass"] = all(rq2_gate.values())

    rq3_gates: dict[str, Any] = {}
    for event_type in EVENT_TYPES:
        absolute = absolute_records[event_type]
        paired = scope_records[event_type]
        if event_type == ADD:
            conditions = {
                "obsolete_rows_absent": (
                    absolute[(OLD_METHOD, "obsolete_answer_rate")]["finite_row_count"]
                    == 0
                    and absolute[(POST_METHOD, "obsolete_answer_rate")][
                        "finite_row_count"
                    ]
                    == 0
                    and absolute[(STORE_METHOD, "obsolete_answer_rate")][
                        "finite_row_count"
                    ]
                    == 0
                ),
                "store_target_ci_high": absolute[(STORE_METHOD, "target_success")][
                    "ci_low"
                ]
                >= R3_TARGET_HIGH,
                "postevent_target_ci_high": absolute[(POST_METHOD, "target_success")][
                    "ci_low"
                ]
                >= R3_TARGET_HIGH,
            }
        else:
            conditions = {
                "store_target_ci_low": absolute[(STORE_METHOD, "target_success")][
                    "ci_high"
                ]
                <= R3_TARGET_LOW,
                "old_target_ci_high": absolute[(OLD_METHOD, "target_success")]["ci_low"]
                >= R3_TARGET_HIGH,
                "old_obsolete_ci_high": absolute[(OLD_METHOD, "obsolete_answer_rate")][
                    "ci_low"
                ]
                >= R3_OBSOLETE_HIGH,
                "postevent_obsolete_ci_low": absolute[
                    (POST_METHOD, "obsolete_answer_rate")
                ]["ci_high"]
                <= R3_OBSOLETE_LOW,
                "target_ci_within_margin": (
                    paired["target_success_delta"]["ci_low"] >= -R2_MATCH_TOLERANCE
                    and paired["target_success_delta"]["ci_high"] <= R2_MATCH_TOLERANCE
                ),
                "evidence_ci_noninferior": paired["evidence_hit_delta"]["ci_low"]
                >= R2_EVIDENCE_FLOOR,
                "cvbd_ci_within_ceiling": paired["cvbd_delta"]["ci_high"]
                <= R2_CVBD_CEILING,
                "postevent_historical_ci_high": absolute[
                    (POST_METHOD, "historical_accuracy")
                ]["ci_low"]
                >= R3_HISTORICAL_FLOOR,
                "historical_ci_noninferior": paired["historical_accuracy_delta"][
                    "ci_low"
                ]
                >= R2_HISTORICAL_FLOOR,
                "obsolete_reduction_ci_positive": paired[
                    "obsolete_answer_rate_reduction"
                ]["ci_low"]
                > 0.0,
            }
        conditions["pass"] = all(conditions.values())
        rq3_gates[event_type] = conditions

    return pair_rows, absolute_rows, rq2_gate, rq3_gates


def event_ranking_outputs(
    data: AggregatedData,
    samples_by_scope: Mapping[str, np.ndarray],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    ranking_rows: list[dict[str, Any]] = []
    tie_rows: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    for event_type in EVENT_TYPES:
        samples = samples_by_scope[event_type]
        point = point_means(
            data,
            RQ2_METHODS,
            RQ1_METRICS,
            cluster_mask(data, event_types=(event_type,)),
        )
        point_ranking = ranking_statistics(RQ2_METHODS, point[:, 0], point[:, 1])
        regret = np.empty(len(samples), dtype=np.float64)
        rho = np.empty(len(samples), dtype=np.float64)
        inversion = np.empty(len(samples), dtype=np.float64)
        old_counts: Counter[tuple[str, ...]] = Counter()
        current_counts: Counter[tuple[str, ...]] = Counter()
        for index in range(len(samples)):
            rank = ranking_statistics(
                RQ2_METHODS, samples[index, :, 0], samples[index, :, 1]
            )
            regret[index] = float(rank["tie_robust_regret"])
            rho[index] = float(rank["spearman"])
            inversion[index] = float(bool(rank["inversion"]))
            old_counts[tuple(rank["historical_tie_set"])] += 1
            current_counts[tuple(rank["current_tie_set"])] += 1
        regret_ci = percentile_interval(regret)
        rho_ci = percentile_interval(rho)
        for scope, seed in [
            ("pooled", None),
            *[(f"seed_{seed}", seed) for seed in EXPECTED_SEEDS],
        ]:
            scores = point_means(
                data,
                RQ2_METHODS,
                RQ1_METRICS,
                cluster_mask(data, event_types=(event_type,), seed=seed),
            )
            rank = ranking_statistics(RQ2_METHODS, scores[:, 0], scores[:, 1])
            row = {
                "analysis": "RQ3_event_ranking",
                "scope": scope,
                "seed": "pooled" if seed is None else seed,
                "event_type": event_type,
                "method_count": len(RQ2_METHODS),
                "event_cluster_count": int(
                    cluster_mask(data, event_types=(event_type,), seed=seed).sum()
                ),
                "historical_tie_set": serialize_tie_set(rank["historical_tie_set"]),
                "current_tie_set": serialize_tie_set(rank["current_tie_set"]),
                "spearman": rank["spearman"],
                "tie_robust_regret": rank["tie_robust_regret"],
                "inversion": rank["inversion"],
                "spearman_ci_low": math.nan,
                "spearman_ci_high": math.nan,
                "regret_ci_low": math.nan,
                "regret_ci_high": math.nan,
                "bootstrap_inversion_frequency": math.nan,
            }
            if seed is None:
                row.update(
                    {
                        "spearman_ci_low": rho_ci[0],
                        "spearman_ci_high": rho_ci[1],
                        "regret_ci_low": regret_ci[0],
                        "regret_ci_high": regret_ci[1],
                        "bootstrap_inversion_frequency": float(inversion.mean()),
                    }
                )
            ranking_rows.append(row)
        for reference, counts_by_set in (
            ("historical", old_counts),
            ("current_valid", current_counts),
        ):
            for methods, count in sorted(
                counts_by_set.items(), key=lambda item: (-item[1], item[0])
            ):
                tie_rows.append(
                    {
                        "analysis": "RQ3_event_ranking",
                        "scope": event_type,
                        "selection_reference": reference,
                        "tie_set": serialize_tie_set(methods),
                        "replicate_count": count,
                        "replicate_frequency": count / len(samples),
                    }
                )
        summary[event_type] = {
            "point": point_ranking,
            "spearman_ci": rho_ci[:2],
            "regret_ci": regret_ci[:2],
            "bootstrap_inversion_frequency": float(inversion.mean()),
        }
    return ranking_rows, tie_rows, summary


def cluster_inventory_rows(label: str, inventory: Inventory) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for seed in EXPECTED_SEEDS:
        for event_type in EVENT_TYPES:
            keys = [
                key
                for key in inventory.clusters
                if key.seed == seed
                and inventory.event_type_by_cluster[key] == event_type
            ]
            rows.append(
                {
                    "source": label,
                    "seed": seed,
                    "event_type": event_type,
                    "event_cluster_count": len(keys),
                    "raw_row_count": sum(
                        inventory.cluster_row_counts[key] for key in keys
                    ),
                    "methods": len(inventory.methods),
                }
            )
    rows.append(
        {
            "source": label,
            "seed": "ALL",
            "event_type": "ALL",
            "event_cluster_count": len(inventory.clusters),
            "raw_row_count": inventory.row_count,
            "methods": len(inventory.methods),
        }
    )
    return rows


def create_rq1_figure(path: Path, metric_rows: Sequence[Mapping[str, Any]]) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    by_method_metric = {
        (str(row["method"]), str(row["metric"])): row
        for row in metric_rows
        if row["analysis"] == "RQ1_reference_mismatch"
    }
    colors = {
        "no_update": "#000000",
        "stale_index": "#CC79A7",
        "query_rewrite_update": "#0072B2",
    }
    figure, axis = plt.subplots(figsize=(7.0, 4.5))
    for method in RQ1_METHODS:
        old = by_method_metric[(method, "old_reference_retention")]
        current = by_method_metric[(method, "current_valid_accuracy")]
        x = float(old["point_estimate"])
        y = float(current["point_estimate"])
        xerr = np.asarray([[x - float(old["ci_low"])], [float(old["ci_high"]) - x]])
        yerr = np.asarray(
            [[y - float(current["ci_low"])], [float(current["ci_high"]) - y]]
        )
        axis.errorbar(
            x,
            y,
            xerr=xerr,
            yerr=yerr,
            fmt="o",
            capsize=2,
            markersize=5,
            color=colors.get(method, "#E69F00"),
            ecolor=colors.get(method, "#E69F00"),
            alpha=0.9,
            label=method,
        )
    axis.set_xlim(0.0, 1.04)
    axis.set_ylim(0.0, 1.04)
    axis.set_xlabel("Historical-reference retention")
    axis.set_ylabel("Current-valid accuracy")
    axis.grid(alpha=0.25, linewidth=0.6)
    axis.legend(
        loc="upper center",
        bbox_to_anchor=(0.5, -0.17),
        ncol=3,
        fontsize=6.5,
        frameon=False,
        handletextpad=0.4,
        columnspacing=0.9,
    )
    figure.tight_layout(rect=(0.0, 0.16, 1.0, 1.0))
    figure.savefig(path, format="pdf", bbox_inches="tight")
    plt.close(figure)


def fmt(value: Any, digits: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return "n/a" if not math.isfinite(number) else f"{number:.{digits}f}"


def create_report(
    path: Path,
    *,
    run_id: str,
    rq1_summary: Mapping[str, Any],
    rq2_gate: Mapping[str, Any],
    rq3_gates: Mapping[str, Any],
    pair_rows: Sequence[Mapping[str, Any]],
    rq1_inventory: Inventory,
    rq2_inventory: Inventory,
) -> None:
    rq1_point = rq1_summary["point"]
    lines = [
        "# Event-cluster Statistical Inference",
        "",
        f"Run: `{run_id}`",
        "",
        "## Protocol",
        "",
        "- Unit: complete `(seed, event_id)` cluster.",
        "- Strata: `seed x event_type`.",
        f"- Bootstrap: {BOOTSTRAP_REPLICATES:,} paired percentile replicates, seed `{BOOTSTRAP_SEED}`.",
        f"- Tie tolerance: `{TIE_EPSILON}`; full tie sets are reported.",
        "- Estimand: finite-row-weighted mean reconstructed from per-cluster sums/counts.",
        "- Interpretation is conditional on the three fixed seeds and controlled event mix.",
        "",
        "## Input audit",
        "",
        f"- RQ1: {rq1_inventory.row_count:,} rows; {len(rq1_inventory.clusters):,} event clusters; {len(rq1_inventory.methods)} methods.",
        f"- RQ2/RQ3: {rq2_inventory.row_count:,} rows; {len(rq2_inventory.clusters):,} event clusters; {len(rq2_inventory.methods)} methods.",
        "- Duplicate, missing-method, and unpaired finite-mask checks passed.",
        "",
        "## RQ1 reference mismatch",
        "",
        f"- Historical tie set: `{serialize_tie_set(rq1_point['historical_tie_set'])}`.",
        f"- Current-valid tie set: `{serialize_tie_set(rq1_point['current_tie_set'])}`.",
        f"- Tie-robust regret: {fmt(rq1_point['tie_robust_regret'])} "
        f"[95% CI {fmt(rq1_summary['tie_robust_regret']['ci_low'])}, "
        f"{fmt(rq1_summary['tie_robust_regret']['ci_high'])}].",
        f"- Spearman: {fmt(rq1_point['spearman'])} "
        f"[95% CI {fmt(rq1_summary['spearman']['ci_low'])}, {fmt(rq1_summary['spearman']['ci_high'])}].",
        f"- Bootstrap inversion stability frequency: {fmt(rq1_summary['bootstrap_inversion_frequency'])}.",
        f"- Positive controlled interpretation: `{bool(rq1_summary['positive_interpretation'])}`.",
        "",
        "## RQ2 matched-success validity control",
        "",
        "| Metric | Point | 95% CI |",
        "|---|---:|---:|",
    ]
    for row in pair_rows:
        if row["scope"] == "SHIFTED_REVISE_RETIRE":
            lines.append(
                f"| {row['metric']} | {fmt(row['point_estimate'])} | "
                f"[{fmt(row['ci_low'])}, {fmt(row['ci_high'])}] |"
            )
    lines.extend(
        [
            "",
            f"CI-aware RQ2 gate: `{'PASS' if rq2_gate['pass'] else 'FAIL'}`.",
            "",
            "## RQ3 event-type boundary",
            "",
            "| Event type | Role | Gate |",
            "|---|---|---:|",
            f"| ADD | negative control | {'PASS' if rq3_gates[ADD]['pass'] else 'FAIL'} |",
            f"| REVISE | positive conflict | {'PASS' if rq3_gates[REVISE]['pass'] else 'FAIL'} |",
            f"| RETIRE | positive conflict | {'PASS' if rq3_gates[RETIRE]['pass'] else 'FAIL'} |",
            "",
            "ADD contains no obsolete-unqualified or historical rows; those metrics are `not_applicable`, not zero.",
            "",
            "## Boundary",
            "",
            "Degenerate intervals are retained when a statistic is constant under the locked stratified resampling design. "
            "They show uniformity inside this controlled generator and event mix; they do not establish production-RAG, "
            "reader-family, or seed-generating-distribution generalization.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def output_checksums(output_dir: Path) -> None:
    paths = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name != "SHA256SUMS"
    )
    content = "".join(f"{sha256_file(path)}  {path.name}\n" for path in paths)
    (output_dir / "SHA256SUMS").write_text(content, encoding="utf-8")


def run_analysis(args: argparse.Namespace) -> dict[str, Any]:
    if args.output_dir.exists():
        raise InferenceValidationError(
            f"refusing to overwrite output directory: {args.output_dir}"
        )
    args.output_dir.mkdir(parents=True)
    started = utc_now()
    log_lines = [f"[start] {started}", f"[run_id] {args.run_id}"]

    rq1_inventory = audit_input(
        args.rq1_metrics,
        RQ1_METHODS,
        require_historical=False,
        expected_seeds=EXPECTED_SEEDS,
        expected_events_per_seed=EXPECTED_EVENTS_PER_SEED,
        expected_event_counts=EXPECTED_EVENT_COUNTS,
    )
    rq2_inventory = audit_input(
        args.rq2_metrics,
        RQ2_METHODS,
        require_historical=True,
        expected_seeds=EXPECTED_SEEDS,
        expected_events_per_seed=EXPECTED_EVENTS_PER_SEED,
        expected_event_counts=EXPECTED_EVENT_COUNTS,
    )
    rq1_source_manifest = audit_source_manifest(args.rq1_manifest, rq1_inventory)
    rq2_source_manifest = audit_source_manifest(args.rq2_manifest, rq2_inventory)
    rq1_data = aggregate_input(rq1_inventory)
    rq2_data = aggregate_input(rq2_inventory)
    log_lines.append("[inventory] PASS")

    rq1_boot = bootstrap_metric_means(
        rq1_data,
        methods=RQ1_METHODS,
        metrics=RQ1_METRICS,
        scopes={"ALL": EVENT_TYPES},
        replicates=args.bootstrap_replicates,
        rng_seed=args.bootstrap_seed,
        batch_size=args.batch_size,
    )["ALL"]
    method_ci_rows, rq1_ranking_rows, rq1_tie_rows, rq1_summary = metric_ci_rows_rq1(
        rq1_data, rq1_boot
    )
    log_lines.append("[bootstrap] RQ1 complete")

    pair_methods = (STORE_METHOD, OLD_METHOD, POST_METHOD)
    pair_scopes = {
        "SHIFTED_REVISE_RETIRE": SHIFTED_EVENT_TYPES,
        ADD: (ADD,),
        REVISE: (REVISE,),
        RETIRE: (RETIRE,),
    }
    rq2_pair_boot = bootstrap_metric_means(
        rq2_data,
        methods=pair_methods,
        metrics=CONDITIONAL_METRICS,
        scopes=pair_scopes,
        replicates=args.bootstrap_replicates,
        rng_seed=args.bootstrap_seed,
        batch_size=args.batch_size,
    )
    pair_rows, rq3_absolute_rows, rq2_gate, rq3_gates = pairwise_rows_and_gates(
        rq2_data, rq2_pair_boot, pair_methods
    )
    method_ci_rows.extend(rq3_absolute_rows)
    log_lines.append("[bootstrap] RQ2/RQ3 paired and absolute metrics complete")

    rq3_ranking_boot = bootstrap_metric_means(
        rq2_data,
        methods=RQ2_METHODS,
        metrics=RQ1_METRICS,
        scopes={event_type: (event_type,) for event_type in EVENT_TYPES},
        replicates=args.bootstrap_replicates,
        rng_seed=args.bootstrap_seed,
        batch_size=args.batch_size,
    )
    rq3_ranking_rows, rq3_tie_rows, rq3_ranking_summary = event_ranking_outputs(
        rq2_data, rq3_ranking_boot
    )
    ranking_rows = rq1_ranking_rows + rq3_ranking_rows
    tie_rows = rq1_tie_rows + rq3_tie_rows
    log_lines.append("[bootstrap] RQ3 ranking complete")

    inventory_rows = cluster_inventory_rows(
        "RQ1", rq1_inventory
    ) + cluster_inventory_rows("RQ2_RQ3", rq2_inventory)
    write_csv(args.output_dir / "cluster_inventory.csv", inventory_rows)
    write_csv(
        args.output_dir / "method_summary_by_seed.csv",
        descriptive_method_rows(rq1_data),
    )
    write_csv(args.output_dir / "ranking_summary_by_seed.csv", ranking_rows)
    write_csv(args.output_dir / "method_metric_cluster_ci.csv", method_ci_rows)
    write_csv(args.output_dir / "pairwise_delta_cluster_ci.csv", pair_rows)
    write_csv(args.output_dir / "tie_sets.csv", tie_rows)

    bootstrap_config = {
        "bootstrap_replicates": args.bootstrap_replicates,
        "bootstrap_rng_seed": args.bootstrap_seed,
        "confidence_level": CONFIDENCE_LEVEL,
        "interval": "percentile",
        "quantile_method": QUANTILE_METHOD,
        "rng_algorithm": RNG_ALGORITHM,
        "cluster_key": ["seed", "event_id"],
        "strata": ["seed", "event_type"],
        "paired_resampling": True,
        "tie_epsilon": TIE_EPSILON,
        "estimand": "finite_row_weighted_mean_from_cluster_sum_count",
        "batch_size": args.batch_size,
    }
    write_json(args.output_dir / "bootstrap_config.json", bootstrap_config)
    replicate_summary = {
        "rq1": rq1_summary,
        "rq2_gate": rq2_gate,
        "rq3_gates": rq3_gates,
        "rq3_ranking": rq3_ranking_summary,
    }
    write_json(
        args.output_dir / "bootstrap_replicates_summary.json",
        jsonable(replicate_summary),
    )

    create_rq1_figure(
        args.output_dir / "rq1_reference_selection_ci.pdf", method_ci_rows
    )
    create_report(
        args.output_dir / "event_cluster_inference_report.md",
        run_id=args.run_id,
        rq1_summary=rq1_summary,
        rq2_gate=rq2_gate,
        rq3_gates=rq3_gates,
        pair_rows=pair_rows,
        rq1_inventory=rq1_inventory,
        rq2_inventory=rq2_inventory,
    )

    project_root = Path(__file__).resolve().parents[3]
    manifest = {
        "run_id": args.run_id,
        "status": "PASS"
        if rq1_summary["positive_interpretation"]
        and rq2_gate["pass"]
        and all(item["pass"] for item in rq3_gates.values())
        else "COMPLETE_WITH_NONPASS_GATE",
        "started_at_utc": started,
        "completed_at_utc": utc_now(),
        "inputs": {
            "rq1_metrics": {
                "path": str(args.rq1_metrics.resolve()),
                "sha256": rq1_inventory.sha256,
                "raw_rows": rq1_inventory.row_count,
                "event_clusters": len(rq1_inventory.clusters),
                "source_manifest": rq1_source_manifest,
            },
            "rq2_rq3_metrics": {
                "path": str(args.rq2_metrics.resolve()),
                "sha256": rq2_inventory.sha256,
                "raw_rows": rq2_inventory.row_count,
                "event_clusters": len(rq2_inventory.clusters),
                "source_manifest": rq2_source_manifest,
            },
        },
        "source_code": {
            "cluster_inference_sha256": sha256_file(Path(__file__)),
            **git_state(project_root),
        },
        "environment": {
            "hostname": platform.node(),
            "python": sys.version,
            "numpy": np.__version__,
            "platform": platform.platform(),
            "pid": os.getpid(),
        },
        "protocol": bootstrap_config,
        "gates": {
            "rq1_positive_interpretation": rq1_summary["positive_interpretation"],
            "rq2_ci_aware_gate": rq2_gate,
            "rq3_ci_aware_gates": rq3_gates,
        },
        "claim_boundary": (
            "Conditional event-cluster inference for the fixed three seeds and controlled "
            "document-shaped lexical/schema-stress event mix; not production-RAG or "
            "reader-family generalization."
        ),
    }
    write_json(args.output_dir / "analysis_manifest.json", jsonable(manifest))
    log_lines.extend(
        [
            f"[gate] RQ1={rq1_summary['positive_interpretation']}",
            f"[gate] RQ2={rq2_gate['pass']}",
            f"[gate] RQ3={all(item['pass'] for item in rq3_gates.values())}",
            f"[done] {utc_now()}",
        ]
    )
    (args.output_dir / "run.log").write_text(
        "\n".join(log_lines) + "\n", encoding="utf-8"
    )
    output_checksums(args.output_dir)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rq1-metrics", type=Path, required=True)
    parser.add_argument("--rq1-manifest", type=Path, required=True)
    parser.add_argument("--rq2-metrics", type=Path, required=True)
    parser.add_argument("--rq2-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--bootstrap-replicates", type=int, default=BOOTSTRAP_REPLICATES
    )
    parser.add_argument("--bootstrap-seed", type=int, default=BOOTSTRAP_SEED)
    parser.add_argument("--batch-size", type=int, default=128)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = run_analysis(args)
    print(
        json.dumps(
            jsonable({"run_id": args.run_id, "status": manifest["status"]}),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
