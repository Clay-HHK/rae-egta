#!/usr/bin/env python3
"""Clean-relative paired event-cluster inference for Block N noise runs.

The ten Block N conditions share the same ``(seed, event_id)`` clusters and
query/method rows.  For ordinary metrics this module first forms a mean inside
each condition/method/event cluster, subtracts the clean cluster mean, and then
bootstraps those paired cluster deltas.  Whole clusters are resampled with
replacement inside ``seed x event_type`` strata.

The resampling contract, RNG, percentile interval, and tie rule are imported
from :mod:`cluster_inference`.  Regret is nonlinear, so it is recomputed from
the complete paired method scores inside every shared bootstrap replicate
rather than being reduced to independently bootstrapped method summaries.
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
    OLD_METHOD,
    POST_METHOD,
    QUANTILE_METHOD,
    RNG_ALGORITHM,
    RQ2_METHODS,
    TIE_EPSILON,
    ClusterKey,
    draw_cluster_indices,
    jsonable,
    percentile_interval,
    ranking_statistics,
    sha256_file,
    write_csv,
    write_json,
)


TARGET = "target"
STABLE = "current_valid_stable"
OBSOLETE = "obsolete_unqualified"
HISTORICAL = "historical"
Q_EVAL = "Q_eval"
ALL_QUERY_TYPES = "ALL"
ALL_EVENT_TYPES = "ALL"
SELECTION_CAPABILITY_FLOOR = 0.10

ROBUST_MODERATE = "ROBUST_MODERATE"
FACTOR_SPECIFIC_DEGRADATION = "FACTOR_SPECIFIC_DEGRADATION"
METADATA_INTENT_SENSITIVE_BOUNDARY = "METADATA_INTENT_SENSITIVE_BOUNDARY"

TARGET_DELTA_FLOOR = -0.03
HISTORICAL_DELTA_FLOOR = -0.02
OBSOLETE_INCREASE_CEILING = 0.10
# These secondary non-regression margins are the already frozen RQ2 margins.
EVIDENCE_DELTA_FLOOR = -0.01
CVBD_INCREASE_CEILING = 0.01

BOOTSTRAP_BATCH_SIZE = 128


class NoiseInferenceError(RuntimeError):
    """Raised when Block N inputs violate the frozen paired design."""


@dataclass(frozen=True)
class ConditionSpec:
    label: str
    factor: str
    rate: float


CONDITION_SPECS = (
    ConditionSpec("clean", "clean", 0.0),
    ConditionSpec("metadata_missing_05", "metadata_missing", 0.05),
    ConditionSpec("metadata_missing_10", "metadata_missing", 0.10),
    ConditionSpec("metadata_missing_20", "metadata_missing", 0.20),
    ConditionSpec("metadata_corrupted_05", "metadata_corrupted", 0.05),
    ConditionSpec("metadata_corrupted_10", "metadata_corrupted", 0.10),
    ConditionSpec("metadata_corrupted_20", "metadata_corrupted", 0.20),
    ConditionSpec("intent_error_05", "intent_error", 0.05),
    ConditionSpec("intent_error_10", "intent_error", 0.10),
    ConditionSpec("intent_error_20", "intent_error", 0.20),
)


@dataclass(frozen=True)
class MetricSpec:
    metric: str
    query_scope: str


# Query-conditioned metrics retain their scientific denominator.  Current-valid
# accuracy is also reported for each finite component and pooled over them.
OUTPUT_METRICS = (
    MetricSpec("target_success", TARGET),
    MetricSpec("obsolete_answer_rate", OBSOLETE),
    MetricSpec("historical_accuracy", HISTORICAL),
    MetricSpec("current_valid_accuracy", ALL_QUERY_TYPES),
    MetricSpec("current_valid_accuracy", Q_EVAL),
    MetricSpec("current_valid_accuracy", TARGET),
    MetricSpec("current_valid_accuracy", STABLE),
    MetricSpec("old_reference_retention", Q_EVAL),
    MetricSpec("evidence_hit", TARGET),
    MetricSpec("obsolete_evidence_hit", OBSOLETE),
    MetricSpec("cvbd", STABLE),
)

REGRET_QUERY_SCOPES = (ALL_QUERY_TYPES, Q_EVAL, TARGET, STABLE)
EVENT_SCOPES: dict[str, tuple[str, ...]] = {
    ALL_EVENT_TYPES: EVENT_TYPES,
    **{event_type: (event_type,) for event_type in EVENT_TYPES},
}

REQUIRED_FIELDS = {
    "seed",
    "event_id",
    "event_type",
    "query_id",
    "query_type",
    "method",
    "target_success",
    "current_valid_accuracy",
    "obsolete_answer_rate",
    "historical_accuracy",
    "old_reference_retention",
    "evidence_hit",
    "obsolete_evidence_hit",
    "cvbd",
}


@dataclass
class ConditionData:
    spec: ConditionSpec
    metrics_path: Path
    manifest_path: Path
    row_count: int
    methods: tuple[str, ...]
    clusters: tuple[ClusterKey, ...]
    seeds: np.ndarray
    event_types: np.ndarray
    event_type_by_cluster: dict[ClusterKey, str]
    query_type_counts: dict[str, int]
    sums: dict[tuple[str, str], np.ndarray]
    counts: dict[tuple[str, str], np.ndarray]
    metrics_sha256: str
    manifest_sha256: str
    dataset_spec_hash: str
    source_git_sha: str | None
    source_git_dirty: bool
    assignment_hash: str

    def cluster_means(self, metric: str, query_scope: str) -> np.ndarray:
        key = (metric, query_scope)
        sums = self.sums[key]
        counts = self.counts[key]
        return np.divide(
            sums,
            counts,
            out=np.full(sums.shape, np.nan, dtype=np.float64),
            where=counts > 0,
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_float(value: str | None) -> float:
    if value is None or value == "" or value.lower() == "nan":
        return math.nan
    try:
        return float(value)
    except ValueError as exc:
        raise NoiseInferenceError(f"non-numeric metric value: {value!r}") from exc


def _aggregation_specs() -> tuple[tuple[str, str], ...]:
    specs = [(item.metric, item.query_scope) for item in OUTPUT_METRICS]
    specs.extend(
        (metric, query_scope)
        for query_scope in REGRET_QUERY_SCOPES
        for metric in ("old_reference_retention", "current_valid_accuracy")
    )
    return tuple(dict.fromkeys(specs))


AGGREGATION_SPECS = _aggregation_specs()


def _matches_query_scope(query_type: str, query_scope: str) -> bool:
    if query_scope == Q_EVAL:
        return query_type in {TARGET, STABLE, OBSOLETE}
    return query_scope == ALL_QUERY_TYPES or query_type == query_scope


def _audit_manifest(
    path: Path,
    spec: ConditionSpec,
    *,
    row_count: int,
    cluster_count: int,
    methods: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not path.is_file() or path.stat().st_size == 0:
        raise NoiseInferenceError(f"missing or empty source manifest: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise NoiseInferenceError(f"invalid source manifest JSON: {path}") from exc

    if int(payload.get("row_count", -1)) != row_count:
        raise NoiseInferenceError(f"source manifest row_count mismatch: {path}")
    if int(payload.get("event_record_count", -1)) != cluster_count:
        raise NoiseInferenceError(
            f"source manifest event_record_count mismatch: {path}"
        )
    if set(payload.get("methods", ())) != set(methods):
        raise NoiseInferenceError(f"source manifest method set mismatch: {path}")

    args = payload.get("args", {})
    assignment = payload.get("noise_assignment", {})
    observed_factor = assignment.get("factor", args.get("noise_factor"))
    observed_rate = assignment.get("requested_rate", args.get("noise_rate"))
    if observed_factor != spec.factor:
        raise NoiseInferenceError(
            f"condition factor mismatch for {spec.label}: {observed_factor!r}"
        )
    try:
        rate_matches = math.isclose(
            float(observed_rate), spec.rate, rel_tol=0.0, abs_tol=1e-12
        )
    except (TypeError, ValueError):
        rate_matches = False
    if not rate_matches:
        raise NoiseInferenceError(
            f"condition rate mismatch for {spec.label}: {observed_rate!r}"
        )

    source_git = payload.get("git", {})
    if source_git.get("git_dirty") is not False:
        raise NoiseInferenceError(f"source formal run is not clean: {path}")
    return payload, assignment


def _read_condition(
    input_root: Path, spec: ConditionSpec
) -> tuple[ConditionData, tuple[tuple[Any, ...], ...]]:
    condition_dir = input_root / spec.label
    metrics_path = condition_dir / "rag_postevent_metrics.csv"
    manifest_path = condition_dir / "run_manifest.json"
    if not metrics_path.is_file() or metrics_path.stat().st_size == 0:
        raise NoiseInferenceError(f"missing or empty condition metrics: {metrics_path}")

    rows: list[dict[str, str]] = []
    identities: list[tuple[Any, ...]] = []
    seen_rows: set[tuple[int, str, str, str]] = set()
    cluster_types: dict[ClusterKey, str] = {}
    query_types: dict[tuple[ClusterKey, str], str] = {}
    query_methods: dict[tuple[ClusterKey, str], set[str]] = defaultdict(set)
    observed_methods: set[str] = set()

    with metrics_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(REQUIRED_FIELDS - set(reader.fieldnames or ()))
        if missing:
            raise NoiseInferenceError(f"{metrics_path}: missing fields {missing}")
        for row_number, row in enumerate(reader, start=2):
            try:
                seed = int(row["seed"])
            except (TypeError, ValueError) as exc:
                raise NoiseInferenceError(
                    f"{metrics_path}:{row_number}: invalid seed"
                ) from exc
            event_id = row["event_id"]
            event_type = row["event_type"]
            query_id = row["query_id"]
            query_type = row["query_type"]
            method = row["method"]
            if not event_id or not query_id or event_type not in EVENT_TYPES:
                raise NoiseInferenceError(
                    f"{metrics_path}:{row_number}: invalid event/query identity"
                )
            if method not in RQ2_METHODS:
                raise NoiseInferenceError(
                    f"{metrics_path}:{row_number}: unexpected method {method!r}"
                )

            cluster = ClusterKey(seed=seed, event_id=event_id)
            row_key = (seed, event_id, query_id, method)
            if row_key in seen_rows:
                raise NoiseInferenceError(f"duplicate row key: {row_key}")
            seen_rows.add(row_key)
            previous_event_type = cluster_types.setdefault(cluster, event_type)
            if previous_event_type != event_type:
                raise NoiseInferenceError(f"cluster changes event_type: {cluster}")
            query_key = (cluster, query_id)
            previous_query_type = query_types.setdefault(query_key, query_type)
            if previous_query_type != query_type:
                raise NoiseInferenceError(f"query changes query_type: {query_key}")
            query_methods[query_key].add(method)
            observed_methods.add(method)
            identities.append(
                (seed, event_id, event_type, query_id, query_type, method)
            )
            rows.append(row)

    expected_methods = set(RQ2_METHODS)
    if observed_methods != expected_methods:
        raise NoiseInferenceError(
            f"{spec.label}: method set mismatch; expected={sorted(expected_methods)} "
            f"observed={sorted(observed_methods)}"
        )
    for query_key, methods in query_methods.items():
        if methods != expected_methods:
            raise NoiseInferenceError(
                f"{spec.label}: unpaired method coverage at {query_key}: "
                f"{sorted(methods)}"
            )

    clusters = tuple(sorted(cluster_types))
    cluster_index = {key: index for index, key in enumerate(clusters)}
    method_index = {method: index for index, method in enumerate(RQ2_METHODS)}
    shape = (len(clusters), len(RQ2_METHODS))
    sums = {
        key: np.zeros(shape, dtype=np.float64) for key in AGGREGATION_SPECS
    }
    counts = {key: np.zeros(shape, dtype=np.int64) for key in AGGREGATION_SPECS}

    for row in rows:
        cluster = ClusterKey(seed=int(row["seed"]), event_id=row["event_id"])
        cidx = cluster_index[cluster]
        midx = method_index[row["method"]]
        query_type = row["query_type"]
        for metric, query_scope in AGGREGATION_SPECS:
            if not _matches_query_scope(query_type, query_scope):
                continue
            value = parse_float(row.get(metric))
            if math.isfinite(value):
                sums[(metric, query_scope)][cidx, midx] += value
                counts[(metric, query_scope)][cidx, midx] += 1

    payload, assignment = _audit_manifest(
        manifest_path,
        spec,
        row_count=len(rows),
        cluster_count=len(clusters),
        methods=RQ2_METHODS,
    )
    source_git = payload.get("git", {})
    data = ConditionData(
        spec=spec,
        metrics_path=metrics_path,
        manifest_path=manifest_path,
        row_count=len(rows),
        methods=RQ2_METHODS,
        clusters=clusters,
        seeds=np.asarray([key.seed for key in clusters], dtype=np.int64),
        event_types=np.asarray(
            [cluster_types[key] for key in clusters], dtype=object
        ),
        event_type_by_cluster=cluster_types,
        query_type_counts=dict(Counter(query_types.values())),
        sums=sums,
        counts=counts,
        metrics_sha256=sha256_file(metrics_path),
        manifest_sha256=sha256_file(manifest_path),
        dataset_spec_hash=str(payload.get("dataset_spec_hash", "")),
        source_git_sha=source_git.get("git_sha"),
        source_git_dirty=bool(source_git.get("git_dirty")),
        assignment_hash=str(assignment.get("assignment_hash", "")),
    )
    return data, tuple(sorted(identities))


def load_conditions(input_root: Path) -> dict[str, ConditionData]:
    """Load all fixed conditions and enforce exact nested-design pairing."""

    clean, clean_identities = _read_condition(input_root, CONDITION_SPECS[0])
    data_by_label = {clean.spec.label: clean}
    for spec in CONDITION_SPECS[1:]:
        condition, identities = _read_condition(input_root, spec)
        if condition.clusters != clean.clusters:
            missing = sorted(set(clean.clusters) - set(condition.clusters))[:3]
            extra = sorted(set(condition.clusters) - set(clean.clusters))[:3]
            raise NoiseInferenceError(
                f"{spec.label}: cluster-key mismatch against clean; "
                f"missing={missing} extra={extra}"
            )
        if not np.array_equal(condition.event_types, clean.event_types):
            raise NoiseInferenceError(
                f"{spec.label}: event_type assignment mismatch against clean"
            )
        if identities != clean_identities:
            missing = sorted(set(clean_identities) - set(identities))[:3]
            extra = sorted(set(identities) - set(clean_identities))[:3]
            raise NoiseInferenceError(
                f"{spec.label}: row identity mismatch against clean; "
                f"missing={missing} extra={extra}"
            )
        if condition.dataset_spec_hash != clean.dataset_spec_hash:
            raise NoiseInferenceError(
                f"{spec.label}: dataset_spec_hash mismatch against clean"
            )
        data_by_label[spec.label] = condition
    return data_by_label


def build_bootstrap_weights(
    clean: ConditionData,
    *,
    replicates: int,
    rng_seed: int,
    batch_size: int = BOOTSTRAP_BATCH_SIZE,
) -> np.ndarray:
    """Create the shared cluster multiplicities under the P0 resampling contract.

    This mirrors ``cluster_inference.bootstrap_metric_means`` exactly: PCG64,
    batches, sorted seeds, fixed event-type order, and within-stratum draws of
    the original cluster count with replacement.  One plan is shared by clean,
    every noise condition, every method, and every metric.
    """

    if replicates <= 0 or batch_size <= 0:
        raise ValueError("replicates and batch_size must be positive")
    if len(clean.clusters) == 0:
        raise NoiseInferenceError("clean condition has no event clusters")
    weights = np.zeros((replicates, len(clean.clusters)), dtype=np.int16)
    rng = np.random.Generator(np.random.PCG64(rng_seed))
    strata: list[np.ndarray] = []
    for seed in sorted(set(int(value) for value in clean.seeds)):
        for event_type in EVENT_TYPES:
            indices = np.flatnonzero(
                (clean.seeds == seed) & (clean.event_types == event_type)
            )
            if indices.size:
                if indices.size > np.iinfo(weights.dtype).max:
                    raise NoiseInferenceError("stratum too large for bootstrap weights")
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


def _event_mask(data: ConditionData, event_scope: str) -> np.ndarray:
    return np.isin(data.event_types, EVENT_SCOPES[event_scope])


def _bootstrap_cluster_mean(
    values: np.ndarray, valid: np.ndarray, weights: np.ndarray, mask: np.ndarray
) -> np.ndarray:
    selected_weights = weights[:, mask].astype(np.float64, copy=False)
    selected_values = np.where(valid[mask], values[mask], 0.0)
    numerator = np.einsum(
        "rc,cm->rm", selected_weights, selected_values, optimize=False
    )
    denominator = np.einsum(
        "rc,cm->rm",
        selected_weights,
        valid[mask].astype(np.float64),
        optimize=False,
    )
    return np.divide(
        numerator,
        denominator,
        out=np.full(numerator.shape, np.nan, dtype=np.float64),
        where=denominator > 0,
    )


def _seed_estimates(
    values: np.ndarray,
    valid: np.ndarray,
    data: ConditionData,
    event_mask: np.ndarray,
) -> dict[str, float | None]:
    estimates: dict[str, float | None] = {}
    for seed in sorted(set(int(value) for value in data.seeds)):
        mask = event_mask & (data.seeds == seed) & valid
        estimates[str(seed)] = float(values[mask].mean()) if mask.any() else None
    return estimates


def _metric_rows(
    clean: ConditionData,
    condition: ConditionData,
    weights: np.ndarray,
    *,
    replicates: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for metric_spec in OUTPUT_METRICS:
        clean_values = clean.cluster_means(
            metric_spec.metric, metric_spec.query_scope
        )
        condition_values = condition.cluster_means(
            metric_spec.metric, metric_spec.query_scope
        )
        paired = np.isfinite(clean_values) & np.isfinite(condition_values)
        delta = condition_values - clean_values
        for event_scope in EVENT_SCOPES:
            event_mask = _event_mask(clean, event_scope)
            scope_paired = paired & event_mask[:, None]
            samples = _bootstrap_cluster_mean(
                delta, paired, weights, event_mask
            )
            for method_index, method in enumerate(clean.methods):
                valid = scope_paired[:, method_index]
                values = delta[:, method_index]
                low, high, valid_replicates = percentile_interval(
                    samples[:, method_index]
                )
                seed_estimates = _seed_estimates(
                    values,
                    paired[:, method_index],
                    clean,
                    event_mask,
                )
                finite_seed_values = [
                    value for value in seed_estimates.values() if value is not None
                ]
                rows.append(
                    {
                        "row_type": "clean_relative_metric",
                        "condition": condition.spec.label,
                        "factor": condition.spec.factor,
                        "rate": condition.spec.rate,
                        "event_scope": event_scope,
                        "query_scope": metric_spec.query_scope,
                        "method": method,
                        "metric": metric_spec.metric,
                        "direction": "noise-clean",
                        "estimand": "mean_of_paired_event_cluster_mean_deltas",
                        "status": "estimated" if valid.any() else "not_applicable",
                        "clean_point": float(
                            clean_values[:, method_index][valid].mean()
                        )
                        if valid.any()
                        else math.nan,
                        "condition_point": float(
                            condition_values[:, method_index][valid].mean()
                        )
                        if valid.any()
                        else math.nan,
                        "point_estimate": float(values[valid].mean())
                        if valid.any()
                        else math.nan,
                        "ci_low": low,
                        "ci_high": high,
                        "confidence_level": CONFIDENCE_LEVEL,
                        "bootstrap_replicates": replicates,
                        "valid_replicates": valid_replicates,
                        "event_cluster_count": int(event_mask.sum()),
                        "paired_finite_cluster_count": int(valid.sum()),
                        "seed_estimates_json": json.dumps(
                            seed_estimates, sort_keys=True, separators=(",", ":")
                        ),
                        "seed_min": min(finite_seed_values)
                        if finite_seed_values
                        else math.nan,
                        "seed_max": max(finite_seed_values)
                        if finite_seed_values
                        else math.nan,
                    }
                )
    return rows


def _weighted_scores(
    data: ConditionData,
    metric: str,
    query_scope: str,
    weights: np.ndarray,
    event_mask: np.ndarray,
) -> np.ndarray:
    sums = data.sums[(metric, query_scope)][event_mask]
    counts = data.counts[(metric, query_scope)][event_mask]
    selected_weights = weights[:, event_mask].astype(np.float64, copy=False)
    numerator = np.einsum(
        "rc,cm->rm", selected_weights, sums, optimize=False
    )
    denominator = np.einsum(
        "rc,cm->rm", selected_weights, counts, optimize=False
    )
    return np.divide(
        numerator,
        denominator,
        out=np.full(numerator.shape, np.nan, dtype=np.float64),
        where=denominator > 0,
    )


def _point_scores(
    data: ConditionData,
    metric: str,
    query_scope: str,
    event_mask: np.ndarray,
) -> np.ndarray:
    sums = data.sums[(metric, query_scope)][event_mask].sum(axis=0)
    counts = data.counts[(metric, query_scope)][event_mask].sum(axis=0)
    return np.divide(
        sums,
        counts,
        out=np.full(sums.shape, np.nan, dtype=np.float64),
        where=counts > 0,
    )


def _regret_samples(old_scores: np.ndarray, current_scores: np.ndarray) -> np.ndarray:
    """Vectorized exact tie-robust regret used by ``ranking_statistics``."""

    if old_scores.shape != current_scores.shape or old_scores.ndim != 2:
        raise ValueError("old/current score samples must be equally shaped matrices")
    valid = np.all(np.isfinite(old_scores), axis=1) & np.all(
        np.isfinite(current_scores), axis=1
    )
    result = np.full(old_scores.shape[0], np.nan, dtype=np.float64)
    if not valid.any():
        return result
    old = old_scores[valid]
    current = current_scores[valid]
    old_optimum = old.max(axis=1)
    old_ties = old_optimum[:, None] - old <= TIE_EPSILON
    best_current = current.max(axis=1)
    best_current_inside_old_ties = np.where(old_ties, current, -np.inf).max(axis=1)
    regret = best_current - best_current_inside_old_ties
    regret[np.abs(regret) <= TIE_EPSILON] = 0.0
    result[valid] = regret
    return result


def _capability_constrained_regret_samples(
    old_scores: np.ndarray,
    current_scores: np.ndarray,
    target_scores: np.ndarray,
    *,
    floor: float,
) -> np.ndarray:
    """Tie-robust regret after a replicate-specific capability floor."""

    if (
        old_scores.shape != current_scores.shape
        or old_scores.shape != target_scores.shape
        or old_scores.ndim != 2
    ):
        raise ValueError("old/current/target samples must be equally shaped matrices")
    finite = (
        np.all(np.isfinite(old_scores), axis=1)
        & np.all(np.isfinite(current_scores), axis=1)
        & np.all(np.isfinite(target_scores), axis=1)
    )
    feasible = target_scores + TIE_EPSILON >= floor
    valid = finite & feasible.any(axis=1)
    result = np.full(old_scores.shape[0], np.nan, dtype=np.float64)
    if not valid.any():
        return result
    old = old_scores[valid]
    current = current_scores[valid]
    allowed = feasible[valid]
    old_optimum = np.where(allowed, old, -np.inf).max(axis=1)
    old_ties = allowed & (old_optimum[:, None] - old <= TIE_EPSILON)
    best_current = np.where(allowed, current, -np.inf).max(axis=1)
    best_current_inside_old_ties = np.where(
        old_ties, current, -np.inf
    ).max(axis=1)
    regret = best_current - best_current_inside_old_ties
    regret[np.abs(regret) <= TIE_EPSILON] = 0.0
    result[valid] = regret
    return result


def _point_ranking(
    data: ConditionData, query_scope: str, event_mask: np.ndarray
) -> dict[str, Any]:
    old_scores = _point_scores(
        data, "old_reference_retention", query_scope, event_mask
    )
    current_scores = _point_scores(
        data, "current_valid_accuracy", query_scope, event_mask
    )
    if not np.all(np.isfinite(old_scores)) or not np.all(np.isfinite(current_scores)):
        return {
            "historical_tie_set": (),
            "current_tie_set": (),
            "tie_robust_regret": math.nan,
            "inversion": False,
        }
    return ranking_statistics(data.methods, old_scores, current_scores)


def _point_capability_ranking(
    data: ConditionData,
    query_scope: str,
    event_mask: np.ndarray,
    *,
    floor: float,
) -> dict[str, Any]:
    old_scores = _point_scores(
        data, "old_reference_retention", query_scope, event_mask
    )
    current_scores = _point_scores(
        data, "current_valid_accuracy", query_scope, event_mask
    )
    target_scores = _point_scores(data, "target_success", TARGET, event_mask)
    if not (
        np.all(np.isfinite(old_scores))
        and np.all(np.isfinite(current_scores))
        and np.all(np.isfinite(target_scores))
    ):
        return {
            "historical_tie_set": (),
            "current_tie_set": (),
            "tie_robust_regret": math.nan,
            "inversion": False,
            "feasible_methods": (),
        }
    feasible_indices = [
        index
        for index, score in enumerate(target_scores)
        if float(score) + TIE_EPSILON >= floor
    ]
    if not feasible_indices:
        return {
            "historical_tie_set": (),
            "current_tie_set": (),
            "tie_robust_regret": math.nan,
            "inversion": False,
            "feasible_methods": (),
        }
    methods = tuple(data.methods[index] for index in feasible_indices)
    result = ranking_statistics(
        methods,
        [float(old_scores[index]) for index in feasible_indices],
        [float(current_scores[index]) for index in feasible_indices],
    )
    return {**result, "feasible_methods": methods}


def _regret_rows(
    clean: ConditionData,
    condition: ConditionData,
    weights: np.ndarray,
    *,
    replicates: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for query_scope in REGRET_QUERY_SCOPES:
        for event_scope in EVENT_SCOPES:
            event_mask = _event_mask(clean, event_scope)
            clean_old = _weighted_scores(
                clean,
                "old_reference_retention",
                query_scope,
                weights,
                event_mask,
            )
            clean_current = _weighted_scores(
                clean,
                "current_valid_accuracy",
                query_scope,
                weights,
                event_mask,
            )
            condition_old = _weighted_scores(
                condition,
                "old_reference_retention",
                query_scope,
                weights,
                event_mask,
            )
            condition_current = _weighted_scores(
                condition,
                "current_valid_accuracy",
                query_scope,
                weights,
                event_mask,
            )
            clean_samples = _regret_samples(clean_old, clean_current)
            condition_samples = _regret_samples(condition_old, condition_current)
            delta_samples = condition_samples - clean_samples
            low, high, valid_replicates = percentile_interval(delta_samples)
            clean_point = _point_ranking(clean, query_scope, event_mask)
            condition_point = _point_ranking(condition, query_scope, event_mask)
            clean_regret = float(clean_point["tie_robust_regret"])
            condition_regret = float(condition_point["tie_robust_regret"])
            point = condition_regret - clean_regret

            seed_estimates: dict[str, float | None] = {}
            for seed in sorted(set(int(value) for value in clean.seeds)):
                seed_mask = event_mask & (clean.seeds == seed)
                clean_seed = _point_ranking(clean, query_scope, seed_mask)
                condition_seed = _point_ranking(condition, query_scope, seed_mask)
                clean_value = float(clean_seed["tie_robust_regret"])
                condition_value = float(condition_seed["tie_robust_regret"])
                seed_estimates[str(seed)] = (
                    condition_value - clean_value
                    if math.isfinite(clean_value) and math.isfinite(condition_value)
                    else None
                )
            finite_seed_values = [
                value for value in seed_estimates.values() if value is not None
            ]
            rows.append(
                {
                    "row_type": "clean_relative_regret",
                    "condition": condition.spec.label,
                    "factor": condition.spec.factor,
                    "rate": condition.spec.rate,
                    "event_scope": event_scope,
                    "query_scope": query_scope,
                    "method": "__selection__",
                    "metric": "tie_robust_method_selection_regret",
                    "direction": "noise-clean",
                    "estimand": "paired_bootstrap_recomputed_nonlinear_regret_delta",
                    "status": "estimated"
                    if math.isfinite(point) and valid_replicates > 0
                    else "not_computable",
                    "clean_point": clean_regret,
                    "condition_point": condition_regret,
                    "point_estimate": point,
                    "ci_low": low,
                    "ci_high": high,
                    "confidence_level": CONFIDENCE_LEVEL,
                    "bootstrap_replicates": replicates,
                    "valid_replicates": valid_replicates,
                    "event_cluster_count": int(event_mask.sum()),
                    "paired_finite_cluster_count": int(event_mask.sum()),
                    "clean_historical_tie_set": json.dumps(
                        list(clean_point["historical_tie_set"]), separators=(",", ":")
                    ),
                    "clean_current_tie_set": json.dumps(
                        list(clean_point["current_tie_set"]), separators=(",", ":")
                    ),
                    "condition_historical_tie_set": json.dumps(
                        list(condition_point["historical_tie_set"]),
                        separators=(",", ":"),
                    ),
                    "condition_current_tie_set": json.dumps(
                        list(condition_point["current_tie_set"]),
                        separators=(",", ":"),
                    ),
                    "seed_estimates_json": json.dumps(
                        seed_estimates, sort_keys=True, separators=(",", ":")
                    ),
                    "seed_min": min(finite_seed_values)
                    if finite_seed_values
                    else math.nan,
                    "seed_max": max(finite_seed_values)
                    if finite_seed_values
                    else math.nan,
                }
            )
    return rows


def _capability_constrained_regret_rows(
    clean: ConditionData,
    condition: ConditionData,
    weights: np.ndarray,
    *,
    replicates: int,
    floor: float = SELECTION_CAPABILITY_FLOOR,
) -> list[dict[str, Any]]:
    """Selection regret on a common Q_eval pool after a target-success floor."""

    rows: list[dict[str, Any]] = []
    query_scope = Q_EVAL
    for event_scope in EVENT_SCOPES:
        event_mask = _event_mask(clean, event_scope)
        clean_old = _weighted_scores(
            clean, "old_reference_retention", query_scope, weights, event_mask
        )
        clean_current = _weighted_scores(
            clean, "current_valid_accuracy", query_scope, weights, event_mask
        )
        clean_target = _weighted_scores(
            clean, "target_success", TARGET, weights, event_mask
        )
        condition_old = _weighted_scores(
            condition,
            "old_reference_retention",
            query_scope,
            weights,
            event_mask,
        )
        condition_current = _weighted_scores(
            condition,
            "current_valid_accuracy",
            query_scope,
            weights,
            event_mask,
        )
        condition_target = _weighted_scores(
            condition, "target_success", TARGET, weights, event_mask
        )
        clean_samples = _capability_constrained_regret_samples(
            clean_old, clean_current, clean_target, floor=floor
        )
        condition_samples = _capability_constrained_regret_samples(
            condition_old, condition_current, condition_target, floor=floor
        )
        delta_samples = condition_samples - clean_samples
        low, high, valid_replicates = percentile_interval(delta_samples)
        clean_point = _point_capability_ranking(
            clean, query_scope, event_mask, floor=floor
        )
        condition_point = _point_capability_ranking(
            condition, query_scope, event_mask, floor=floor
        )
        clean_regret = float(clean_point["tie_robust_regret"])
        condition_regret = float(condition_point["tie_robust_regret"])
        point = condition_regret - clean_regret

        seed_estimates: dict[str, float | None] = {}
        for seed in sorted(set(int(value) for value in clean.seeds)):
            seed_mask = event_mask & (clean.seeds == seed)
            clean_seed = _point_capability_ranking(
                clean, query_scope, seed_mask, floor=floor
            )
            condition_seed = _point_capability_ranking(
                condition, query_scope, seed_mask, floor=floor
            )
            clean_value = float(clean_seed["tie_robust_regret"])
            condition_value = float(condition_seed["tie_robust_regret"])
            seed_estimates[str(seed)] = (
                condition_value - clean_value
                if math.isfinite(clean_value) and math.isfinite(condition_value)
                else None
            )
        finite_seed_values = [
            value for value in seed_estimates.values() if value is not None
        ]
        rows.append(
            {
                "row_type": "capability_constrained_regret",
                "condition": condition.spec.label,
                "factor": condition.spec.factor,
                "rate": condition.spec.rate,
                "event_scope": event_scope,
                "query_scope": query_scope,
                "method": "__selection__",
                "metric": "tie_robust_method_selection_regret",
                "direction": "noise-clean",
                "estimand": (
                    "paired_bootstrap_recomputed_nonlinear_regret_delta_"
                    "after_target_success_floor"
                ),
                "status": "estimated"
                if math.isfinite(point) and valid_replicates > 0
                else "not_computable",
                "target_success_floor": floor,
                "clean_point": clean_regret,
                "condition_point": condition_regret,
                "point_estimate": point,
                "ci_low": low,
                "ci_high": high,
                "confidence_level": CONFIDENCE_LEVEL,
                "bootstrap_replicates": replicates,
                "valid_replicates": valid_replicates,
                "event_cluster_count": int(event_mask.sum()),
                "paired_finite_cluster_count": int(event_mask.sum()),
                "clean_feasible_methods": json.dumps(
                    list(clean_point["feasible_methods"]), separators=(",", ":")
                ),
                "condition_feasible_methods": json.dumps(
                    list(condition_point["feasible_methods"]),
                    separators=(",", ":"),
                ),
                "clean_historical_tie_set": json.dumps(
                    list(clean_point["historical_tie_set"]), separators=(",", ":")
                ),
                "clean_current_tie_set": json.dumps(
                    list(clean_point["current_tie_set"]), separators=(",", ":")
                ),
                "condition_historical_tie_set": json.dumps(
                    list(condition_point["historical_tie_set"]),
                    separators=(",", ":"),
                ),
                "condition_current_tie_set": json.dumps(
                    list(condition_point["current_tie_set"]),
                    separators=(",", ":"),
                ),
                "seed_estimates_json": json.dumps(
                    seed_estimates, sort_keys=True, separators=(",", ":")
                ),
                "seed_min": min(finite_seed_values)
                if finite_seed_values
                else math.nan,
                "seed_max": max(finite_seed_values)
                if finite_seed_values
                else math.nan,
            }
        )
    return rows


def _obsolete_reduction_rows(
    clean: ConditionData,
    condition: ConditionData,
    weights: np.ndarray,
    *,
    replicates: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    clean_values = clean.cluster_means("obsolete_answer_rate", OBSOLETE)
    condition_values = condition.cluster_means("obsolete_answer_rate", OBSOLETE)
    old_index = condition.methods.index(OLD_METHOD)
    post_index = condition.methods.index(POST_METHOD)
    clean_valid = np.isfinite(clean_values[:, old_index]) & np.isfinite(
        clean_values[:, post_index]
    )
    condition_valid = np.isfinite(condition_values[:, old_index]) & np.isfinite(
        condition_values[:, post_index]
    )
    clean_reduction = clean_values[:, old_index] - clean_values[:, post_index]
    condition_reduction = (
        condition_values[:, old_index] - condition_values[:, post_index]
    )

    for event_scope in EVENT_SCOPES:
        event_mask = _event_mask(clean, event_scope)
        samples = _bootstrap_cluster_mean(
            condition_reduction[:, None],
            condition_valid[:, None],
            weights,
            event_mask,
        )[:, 0]
        low, high, valid_replicates = percentile_interval(samples)
        valid = condition_valid & event_mask
        clean_scope_valid = clean_valid & event_mask
        seed_estimates = _seed_estimates(
            condition_reduction,
            condition_valid,
            condition,
            event_mask,
        )
        finite_seed_values = [
            value for value in seed_estimates.values() if value is not None
        ]
        rows.append(
            {
                "row_type": "postevent_vs_oldref",
                "condition": condition.spec.label,
                "factor": condition.spec.factor,
                "rate": condition.spec.rate,
                "event_scope": event_scope,
                "query_scope": OBSOLETE,
                "method": f"{OLD_METHOD}-{POST_METHOD}",
                "metric": "obsolete_answer_rate_reduction",
                "direction": "oldref-postevent",
                "estimand": "mean_of_paired_event_cluster_method_differences",
                "status": "estimated" if valid.any() else "not_applicable",
                "clean_point": float(clean_reduction[clean_scope_valid].mean())
                if clean_scope_valid.any()
                else math.nan,
                "condition_point": float(condition_reduction[valid].mean())
                if valid.any()
                else math.nan,
                "point_estimate": float(condition_reduction[valid].mean())
                if valid.any()
                else math.nan,
                "ci_low": low,
                "ci_high": high,
                "confidence_level": CONFIDENCE_LEVEL,
                "bootstrap_replicates": replicates,
                "valid_replicates": valid_replicates,
                "event_cluster_count": int(event_mask.sum()),
                "paired_finite_cluster_count": int(valid.sum()),
                "seed_estimates_json": json.dumps(
                    seed_estimates, sort_keys=True, separators=(",", ":")
                ),
                "seed_min": min(finite_seed_values)
                if finite_seed_values
                else math.nan,
                "seed_max": max(finite_seed_values)
                if finite_seed_values
                else math.nan,
            }
        )
    return rows


def analyze_condition(
    clean: ConditionData,
    condition: ConditionData,
    weights: np.ndarray,
    *,
    replicates: int,
) -> list[dict[str, Any]]:
    """Return one condition's clean-relative and paired method CI table."""

    return [
        *_metric_rows(clean, condition, weights, replicates=replicates),
        *_regret_rows(clean, condition, weights, replicates=replicates),
        *_capability_constrained_regret_rows(
            clean, condition, weights, replicates=replicates
        ),
        *_obsolete_reduction_rows(clean, condition, weights, replicates=replicates),
    ]


def _find_row(
    rows: Sequence[Mapping[str, Any]],
    *,
    row_type: str,
    metric: str,
    query_scope: str,
    method: str,
) -> Mapping[str, Any]:
    matches = [
        row
        for row in rows
        if row["row_type"] == row_type
        and row["metric"] == metric
        and row["query_scope"] == query_scope
        and row["method"] == method
        and row["event_scope"] == ALL_EVENT_TYPES
    ]
    if len(matches) != 1:
        raise NoiseInferenceError(
            f"expected one gate row for {row_type}/{metric}/{query_scope}/{method}; "
            f"observed={len(matches)}"
        )
    return matches[0]


def evaluate_checkpoint(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Evaluate the frozen 6.7 margins for one non-clean condition."""

    target = _find_row(
        rows,
        row_type="clean_relative_metric",
        metric="target_success",
        query_scope=TARGET,
        method=POST_METHOD,
    )
    historical = _find_row(
        rows,
        row_type="clean_relative_metric",
        metric="historical_accuracy",
        query_scope=HISTORICAL,
        method=POST_METHOD,
    )
    obsolete = _find_row(
        rows,
        row_type="clean_relative_metric",
        metric="obsolete_answer_rate",
        query_scope=OBSOLETE,
        method=POST_METHOD,
    )
    evidence = _find_row(
        rows,
        row_type="clean_relative_metric",
        metric="evidence_hit",
        query_scope=TARGET,
        method=POST_METHOD,
    )
    cvbd = _find_row(
        rows,
        row_type="clean_relative_metric",
        metric="cvbd",
        query_scope=STABLE,
        method=POST_METHOD,
    )
    obsolete_reduction = _find_row(
        rows,
        row_type="postevent_vs_oldref",
        metric="obsolete_answer_rate_reduction",
        query_scope=OBSOLETE,
        method=f"{OLD_METHOD}-{POST_METHOD}",
    )

    bounds = {
        "target_delta_ci_low": target["ci_low"],
        "historical_delta_ci_low": historical["ci_low"],
        "obsolete_increase_ci_high": obsolete["ci_high"],
        "obsolete_reduction_ci_low": obsolete_reduction["ci_low"],
        "evidence_hit_delta_ci_low": evidence["ci_low"],
        "cvbd_increase_ci_high": cvbd["ci_high"],
    }
    available = all(
        isinstance(value, (int, float)) and math.isfinite(float(value))
        for value in bounds.values()
    )
    conditions = {
        "target_clean_relative_noninferior": available
        and float(bounds["target_delta_ci_low"]) >= TARGET_DELTA_FLOOR,
        "historical_clean_relative_noninferior": available
        and float(bounds["historical_delta_ci_low"]) >= HISTORICAL_DELTA_FLOOR,
        "obsolete_increase_bounded": available
        and float(bounds["obsolete_increase_ci_high"])
        <= OBSOLETE_INCREASE_CEILING,
        "postevent_vs_oldref_obsolete_reduction_positive": available
        and float(bounds["obsolete_reduction_ci_low"]) > 0.0,
        "evidence_hit_noninferior": available
        and float(bounds["evidence_hit_delta_ci_low"]) >= EVIDENCE_DELTA_FLOOR,
        "cvbd_increase_bounded": available
        and float(bounds["cvbd_increase_ci_high"]) <= CVBD_INCREASE_CEILING,
    }
    no_secondary_regression = (
        conditions["evidence_hit_noninferior"]
        and conditions["cvbd_increase_bounded"]
        and conditions["historical_clean_relative_noninferior"]
    )
    return {
        "available": available,
        "bounds": bounds,
        "conditions": conditions,
        "no_evidence_cvbd_historical_regression": no_secondary_regression,
        "pass": available and all(conditions.values()),
    }


def classify_verdict(condition_gates: Sequence[Mapping[str, Any]]) -> str:
    """Apply the pre-registered 5/10/20 checkpoint interpretation."""

    five = [gate for gate in condition_gates if math.isclose(float(gate["rate"]), 0.05)]
    ten = [gate for gate in condition_gates if math.isclose(float(gate["rate"]), 0.10)]
    if len(five) != 3 or len(ten) != 3:
        raise NoiseInferenceError(
            f"verdict requires three 5% and three 10% gates; got {len(five)} and {len(ten)}"
        )
    # A failure already visible at 5% is the stronger boundary interpretation.
    if any(not bool(gate["pass"]) for gate in five):
        return METADATA_INTENT_SENSITIVE_BOUNDARY
    if all(bool(gate["pass"]) for gate in ten):
        return ROBUST_MODERATE
    return FACTOR_SPECIFIC_DEGRADATION


def _condition_inventory_rows(data_by_label: Mapping[str, ConditionData]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for spec in CONDITION_SPECS:
        data = data_by_label[spec.label]
        for event_type in (*EVENT_TYPES, ALL_EVENT_TYPES):
            if event_type == ALL_EVENT_TYPES:
                mask = np.ones(len(data.clusters), dtype=bool)
            else:
                mask = data.event_types == event_type
            rows.append(
                {
                    "condition": spec.label,
                    "factor": spec.factor,
                    "rate": spec.rate,
                    "event_type": event_type,
                    "event_cluster_count": int(mask.sum()),
                    "raw_row_count": data.row_count
                    if event_type == ALL_EVENT_TYPES
                    else "",
                    "method_count": len(data.methods),
                    "metrics_sha256": data.metrics_sha256,
                    "manifest_sha256": data.manifest_sha256,
                }
            )
    return rows


def run_analysis(args: argparse.Namespace) -> dict[str, Any]:
    if args.output_dir.exists():
        raise NoiseInferenceError(
            f"refusing to overwrite output directory: {args.output_dir}"
        )
    if args.bootstrap_replicates <= 0:
        raise NoiseInferenceError("bootstrap_replicates must be positive")
    if not args.run_id:
        raise NoiseInferenceError("run_id must be non-empty")

    started = utc_now()
    data_by_label = load_conditions(args.input_root)
    clean = data_by_label["clean"]
    weights = build_bootstrap_weights(
        clean,
        replicates=args.bootstrap_replicates,
        rng_seed=args.bootstrap_seed,
    )

    tables: dict[str, list[dict[str, Any]]] = {}
    gates: list[dict[str, Any]] = []
    for spec in CONDITION_SPECS:
        rows = analyze_condition(
            clean,
            data_by_label[spec.label],
            weights,
            replicates=args.bootstrap_replicates,
        )
        tables[spec.label] = rows
        if spec.label != "clean":
            gate = {
                "condition": spec.label,
                "factor": spec.factor,
                "rate": spec.rate,
                "checkpoint_role": "primary"
                if math.isclose(spec.rate, 0.10)
                else "stress_only"
                if math.isclose(spec.rate, 0.20)
                else "early_boundary",
                **evaluate_checkpoint(rows),
            }
            gates.append(gate)

    verdict_label = classify_verdict(gates)
    verdict = {
        "run_id": args.run_id,
        "verdict": verdict_label,
        "primary_checkpoint_rate": 0.10,
        "early_boundary_rate": 0.05,
        "stress_only_rate": 0.20,
        "primary_method": POST_METHOD,
        "paired_obsolete_reduction_contrast": f"{OLD_METHOD}-{POST_METHOD}",
        "thresholds": {
            "target_delta_ci_low_min": TARGET_DELTA_FLOOR,
            "historical_delta_ci_low_min": HISTORICAL_DELTA_FLOOR,
            "obsolete_increase_ci_high_max": OBSOLETE_INCREASE_CEILING,
            "obsolete_reduction_ci_low_strict_min": 0.0,
            "evidence_hit_delta_ci_low_min": EVIDENCE_DELTA_FLOOR,
            "cvbd_increase_ci_high_max": CVBD_INCREASE_CEILING,
        },
        "condition_gates": gates,
        "interpretation": {
            ROBUST_MODERATE: (
                "All three 10% noise families pass the pre-registered margins, "
                "and no family crosses them at 5%."
            ),
            FACTOR_SPECIFIC_DEGRADATION: (
                "All three 5% conditions pass, but at least one 10% family fails."
            ),
            METADATA_INTENT_SENSITIVE_BOUNDARY: (
                "At least one noise family fails a pre-registered gate at 5%."
            ),
        }[verdict_label],
        "twenty_percent_is_hard_gate": False,
    }

    args.output_dir.mkdir(parents=True)
    table_names: list[str] = []
    for spec in CONDITION_SPECS:
        name = f"condition_{spec.label}_cluster_ci.csv"
        table_names.append(name)
        write_csv(args.output_dir / name, tables[spec.label])
    write_csv(
        args.output_dir / "condition_inventory.csv",
        _condition_inventory_rows(data_by_label),
    )
    write_json(args.output_dir / "verdict.json", jsonable(verdict))

    analysis_git = git_metadata()
    source_clean = all(not data.source_git_dirty for data in data_by_label.values())
    paper_facing_eligible = source_clean and analysis_git.get("git_dirty") is False
    protocol = {
        "cluster_key": ["seed", "event_id"],
        "strata": ["seed", "event_type"],
        "paired_across": ["condition", "method", "query_type", "row"],
        "ordinary_metric_estimand": "mean_of_paired_event_cluster_mean_deltas",
        "clean_relative_direction": "noise-clean",
        "bootstrap_replicates": args.bootstrap_replicates,
        "bootstrap_rng_seed": args.bootstrap_seed,
        "bootstrap_batch_size": BOOTSTRAP_BATCH_SIZE,
        "rng_algorithm": RNG_ALGORITHM,
        "confidence_level": CONFIDENCE_LEVEL,
        "interval": "percentile",
        "quantile_method": QUANTILE_METHOD,
        "tie_epsilon": TIE_EPSILON,
        "regret_contract": (
            "full tie set and conservative best-current member of the historical "
            "tie set, recomputed inside every paired replicate; Q_eval additionally "
            f"reports the target-success floor {SELECTION_CAPABILITY_FLOOR:.2f}"
        ),
    }
    inputs = {
        spec.label: {
            "factor": spec.factor,
            "rate": spec.rate,
            "metrics_path": str(data_by_label[spec.label].metrics_path.resolve()),
            "metrics_sha256": data_by_label[spec.label].metrics_sha256,
            "manifest_path": str(data_by_label[spec.label].manifest_path.resolve()),
            "manifest_sha256": data_by_label[spec.label].manifest_sha256,
            "raw_rows": data_by_label[spec.label].row_count,
            "event_clusters": len(data_by_label[spec.label].clusters),
            "dataset_spec_hash": data_by_label[spec.label].dataset_spec_hash,
            "noise_assignment_hash": data_by_label[spec.label].assignment_hash,
            "source_git_sha": data_by_label[spec.label].source_git_sha,
            "source_git_dirty": data_by_label[spec.label].source_git_dirty,
        }
        for spec in CONDITION_SPECS
    }
    manifest = {
        "experiment": "block_n200_metadata_intent_noise_inference",
        "run_id": args.run_id,
        "status": "COMPLETE",
        "started_at_utc": started,
        "completed_at_utc": utc_now(),
        "input_root": str(args.input_root.resolve()),
        "inputs": inputs,
        "protocol": protocol,
        "protocol_hash": stable_json_hash(protocol),
        "verdict": verdict_label,
        "paper_facing_eligible": paper_facing_eligible,
        "paper_facing_requirement": (
            "All source manifests and this analysis must record git_dirty=false."
        ),
        "git": analysis_git,
        "source_code": {
            "noise_inference_path": str(Path(__file__).resolve()),
            "noise_inference_sha256": sha256_file(Path(__file__)),
        },
        "environment": {
            "command": " ".join([sys.executable, *sys.argv]),
            "hostname": platform.node(),
            "python": sys.version,
            "numpy": np.__version__,
            "platform": platform.platform(),
            "pid": os.getpid(),
        },
        "outputs": [
            *table_names,
            "condition_inventory.csv",
            "verdict.json",
            "analysis_manifest.json",
            "run.log",
        ],
        "claim_boundary": (
            "Pointwise controlled Block N intervals for the fixed seeds, event mix, "
            "methods, and nested noise assignments.  The 20% conditions are stress-only; "
            "these results do not establish production-RAG robustness."
        ),
    }
    write_json(args.output_dir / "analysis_manifest.json", jsonable(manifest))
    (args.output_dir / "run.log").write_text(
        "\n".join(
            [
                f"[start] {started}",
                f"[run_id] {args.run_id}",
                "[inventory] PASS: exact clean-relative row and cluster pairing",
                f"[bootstrap] B={args.bootstrap_replicates} seed={args.bootstrap_seed}",
                f"[verdict] {verdict_label}",
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
    parser.add_argument("--input-root", type=Path, required=True)
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
                "verdict": manifest["verdict"],
                "paper_facing_eligible": manifest["paper_facing_eligible"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
