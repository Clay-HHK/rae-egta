#!/usr/bin/env python3
"""Summarize controlled RAG post-event drift outputs."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


BASE_NUMERIC_FIELDS = [
    "target_success",
    "current_valid_accuracy",
    "historical_accuracy",
    "obsolete_answer_rate",
    "old_reference_retention",
    "postevent_retention",
    "cvbd",
    "total_drift",
    "store_response_drift",
    "adaptation_side_effect",
]

DOC_NUMERIC_FIELDS = [
    "evidence_hit",
    "obsolete_evidence_hit",
    "retrieval_recall_at_k",
    "chunk_rank",
]

TARGET = "target"
CURRENT_VALID_STABLE = "current_valid_stable"
OBSOLETE_UNQUALIFIED = "obsolete_unqualified"
HISTORICAL = "historical"
ADD = "ADD"
REVISE = "REVISE"
RETIRE = "RETIRE"

CURRENT_VALID_TYPES = {TARGET, CURRENT_VALID_STABLE, OBSOLETE_UNQUALIFIED}
SHIFTED_EVENT_TYPES = {REVISE, RETIRE}
R2_MATCH_TOLERANCE = 0.03
R2_OBSOLETE_REDUCTION = 0.10
R2_EVIDENCE_FLOOR = -0.01
R2_CVBD_CEILING = 0.01
R2_HISTORICAL_FLOOR = -0.02
R3_TARGET_HIGH = 0.95
R3_TARGET_LOW = 0.05
R3_OBSOLETE_HIGH = 0.95
R3_OBSOLETE_LOW = 0.05
R3_HISTORICAL_FLOOR = 0.98

R3_FIELDS = [
    "target_success",
    "current_valid_accuracy",
    "obsolete_answer_rate",
    "evidence_hit",
    "retrieval_recall_at_k",
    "historical_accuracy",
    "cvbd",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or path.stat().st_size == 0:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def as_float(row: dict[str, str], key: str, default: float = math.nan) -> float:
    value = row.get(key, "")
    if value == "" or str(value).lower() == "nan":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def finite(values: list[float]) -> list[float]:
    return [value for value in values if math.isfinite(value)]


def safe_mean(values: list[float]) -> float:
    vals = finite(values)
    return mean(vals) if vals else math.nan


def row_mean(rows: list[dict[str, str]], field: str) -> float:
    return safe_mean([as_float(row, field) for row in rows])


def numeric_fields_for_rows(rows: list[dict[str, str]]) -> list[str]:
    fields = list(BASE_NUMERIC_FIELDS)
    for field in DOC_NUMERIC_FIELDS:
        if any(field in row for row in rows):
            fields.append(field)
    return fields


def fmt(value: Any, digits: int = 4) -> str:
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return "nan"
        return f"{value:.{digits}f}"
    return str(value)


def ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    out = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i + 1
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        rank = (i + j + 1) / 2.0
        for k in range(i, j):
            out[indexed[k][0]] = rank
        i = j
    return out


def pearson(xs: list[float], ys: list[float]) -> float:
    paired = [(x, y) for x, y in zip(xs, ys) if math.isfinite(x) and math.isfinite(y)]
    if len(paired) < 2:
        return math.nan
    clean_x, clean_y = zip(*paired)
    x_mean = mean(clean_x)
    y_mean = mean(clean_y)
    x_centered = [x - x_mean for x in clean_x]
    y_centered = [y - y_mean for y in clean_y]
    denom_x = math.sqrt(sum(x * x for x in x_centered))
    denom_y = math.sqrt(sum(y * y for y in y_centered))
    if denom_x <= 0.0 or denom_y <= 0.0:
        return math.nan
    return sum(x * y for x, y in zip(x_centered, y_centered)) / (denom_x * denom_y)


def spearman(xs: list[float], ys: list[float]) -> float:
    paired = [(x, y) for x, y in zip(xs, ys) if math.isfinite(x) and math.isfinite(y)]
    if len(paired) < 2:
        return math.nan
    clean_x, clean_y = zip(*paired)
    return pearson(ranks(list(clean_x)), ranks(list(clean_y)))


def group_by(rows: list[dict[str, str]], keys: tuple[str, ...]) -> dict[tuple[str, ...], list[dict[str, str]]]:
    grouped: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[tuple(row.get(key, "") for key in keys)].append(row)
    return grouped


def summarize_group(key: tuple[str, ...], items: list[dict[str, str]], key_names: tuple[str, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {name: value for name, value in zip(key_names, key)}
    out["row_count"] = len(items)
    out["seed_count"] = len({row.get("seed", "") for row in items})
    out["event_count"] = len({row.get("event_id", "") for row in items})
    for field in numeric_fields_for_rows(items):
        out[field] = safe_mean([as_float(row, field) for row in items])
    return out


def method_summary(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        summarize_group(key, items, ("method",))
        for key, items in sorted(group_by(rows, ("method",)).items())
    ]


def method_family(method: str) -> str:
    if method.startswith("old_ref_rewrite_reg"):
        return "old_ref_rewrite_regularized"
    if method.startswith("postevent_rewrite_reg"):
        return "postevent_rewrite_regularized"
    if method.startswith("old_ref_reg"):
        return "old_ref_regularized"
    if method.startswith("postevent_ref_reg"):
        return "postevent_regularized"
    if method == "query_rewrite_update":
        return "query_rewrite_update"
    if method == "oldref_adapted_preserver":
        return "oldref_adapted_preserver"
    if method == "postevent_validity_filter":
        return "postevent_validity_filter"
    if method == "intent_aware_postevent_filter":
        return "intent_aware_postevent_filter"
    if method in {"no_update", "store_only", "stale_index", "naive_update", "replay_old"}:
        return method
    return "other"


def r2_method_summary(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for (method,), items in sorted(group_by(rows, ("method",)).items()):
        shifted = [row for row in items if row.get("event_type") in SHIFTED_EVENT_TYPES]
        shifted_target = [row for row in shifted if row.get("query_type") == TARGET]
        shifted_current_valid = [row for row in shifted if row.get("query_type") in CURRENT_VALID_TYPES]
        shifted_obsolete = [row for row in shifted if row.get("query_type") == OBSOLETE_UNQUALIFIED]
        shifted_stable = [row for row in shifted if row.get("query_type") == CURRENT_VALID_STABLE]
        shifted_historical = [row for row in shifted if row.get("query_type") == "historical"]
        out.append(
            {
                "method": method,
                "method_family": method_family(method),
                "shifted_target_row_count": len(shifted_target),
                "shifted_target_success": row_mean(shifted_target, "target_success"),
                "shifted_target_retrieval_recall_at_k": row_mean(shifted_target, "retrieval_recall_at_k"),
                "shifted_target_evidence_hit": row_mean(shifted_target, "evidence_hit"),
                "shifted_current_valid_row_count": len(shifted_current_valid),
                "shifted_current_valid_accuracy": row_mean(shifted_current_valid, "current_valid_accuracy"),
                "obsolete_row_count": len(shifted_obsolete),
                "obsolete_answer_rate": row_mean(shifted_obsolete, "obsolete_answer_rate"),
                "obsolete_evidence_hit": row_mean(shifted_obsolete, "obsolete_evidence_hit"),
                "cvbd_row_count": len(shifted_stable),
                "cvbd": row_mean(shifted_stable, "cvbd"),
                "historical_row_count": len(shifted_historical),
                "historical_accuracy": row_mean(shifted_historical, "historical_accuracy"),
            }
        )
    return out


def _r2_by_method(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["method"]): row for row in rows}


def legacy_scalar_grid_pair_grid(
    method_rows: list[dict[str, Any]],
    *,
    tolerance: float = R2_MATCH_TOLERANCE,
) -> list[dict[str, Any]]:
    by_method = _r2_by_method(method_rows)
    old_methods = sorted(method for method in by_method if method.startswith("old_ref_rewrite_reg"))
    postevent_methods = sorted(method for method in by_method if method.startswith("postevent_rewrite_reg"))
    out: list[dict[str, Any]] = []
    for old_method in old_methods:
        old = by_method[old_method]
        old_target = float(old["shifted_target_success"])
        old_obsolete = float(old["obsolete_answer_rate"])
        old_cvbd = float(old["cvbd"])
        old_evidence = float(old["shifted_target_evidence_hit"])
        for postevent_method in postevent_methods:
            postevent = by_method[postevent_method]
            postevent_target = float(postevent["shifted_target_success"])
            postevent_obsolete = float(postevent["obsolete_answer_rate"])
            postevent_cvbd = float(postevent["cvbd"])
            postevent_evidence = float(postevent["shifted_target_evidence_hit"])
            target_delta = postevent_target - old_target
            abs_target_delta = abs(target_delta)
            obsolete_reduction = old_obsolete - postevent_obsolete
            cvbd_change = postevent_cvbd - old_cvbd
            evidence_delta = postevent_evidence - old_evidence
            matched = math.isfinite(abs_target_delta) and abs_target_delta <= tolerance
            legacy_scalar_grid_positive = (
                matched
                and math.isfinite(obsolete_reduction)
                and obsolete_reduction >= 0.10
                and math.isfinite(evidence_delta)
                and evidence_delta >= -1e-12
                and math.isfinite(cvbd_change)
                and cvbd_change <= 1e-12
            )
            out.append(
                {
                    "old_method": old_method,
                    "postevent_method": postevent_method,
                    "old_target_success": old_target,
                    "postevent_target_success": postevent_target,
                    "target_success_delta": target_delta,
                    "abs_target_success_delta": abs_target_delta,
                    "old_obsolete_answer_rate": old_obsolete,
                    "postevent_obsolete_answer_rate": postevent_obsolete,
                    "obsolete_answer_rate_reduction": obsolete_reduction,
                    "old_cvbd": old_cvbd,
                    "postevent_cvbd": postevent_cvbd,
                    "cvbd_change": cvbd_change,
                    "old_evidence_hit": old_evidence,
                    "postevent_evidence_hit": postevent_evidence,
                    "evidence_hit_delta": evidence_delta,
                    "matched_target_success": matched,
                    "legacy_scalar_grid_positive": legacy_scalar_grid_positive,
                }
            )
    return sorted(out, key=lambda row: (float(row["abs_target_success_delta"]), -float(row["obsolete_answer_rate_reduction"])))


def legacy_scalar_grid_matched_pairs(
    pair_rows: list[dict[str, Any]],
    *,
    tolerance: float = R2_MATCH_TOLERANCE,
) -> list[dict[str, Any]]:
    return [
        row
        for row in pair_rows
        if math.isfinite(float(row["abs_target_success_delta"]))
        and float(row["abs_target_success_delta"]) <= tolerance
    ]


def r2_gate_rows(method_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_method = _r2_by_method(method_rows)
    old = by_method.get("oldref_adapted_preserver")
    if old is None:
        return []
    out: list[dict[str, Any]] = []
    for postevent_method in ["postevent_validity_filter", "intent_aware_postevent_filter"]:
        postevent = by_method.get(postevent_method)
        if postevent is None:
            continue
        old_target = float(old["shifted_target_success"])
        postevent_target = float(postevent["shifted_target_success"])
        target_delta = postevent_target - old_target
        abs_target_delta = abs(target_delta)
        obsolete_reduction = float(old["obsolete_answer_rate"]) - float(postevent["obsolete_answer_rate"])
        evidence_delta = float(postevent["shifted_target_evidence_hit"]) - float(old["shifted_target_evidence_hit"])
        cvbd_delta = float(postevent["cvbd"]) - float(old["cvbd"])
        historical_delta = float(postevent["historical_accuracy"]) - float(old["historical_accuracy"])
        matched = math.isfinite(abs_target_delta) and abs_target_delta <= R2_MATCH_TOLERANCE
        positive = (
            matched
            and math.isfinite(obsolete_reduction)
            and obsolete_reduction >= R2_OBSOLETE_REDUCTION
            and math.isfinite(evidence_delta)
            and evidence_delta >= R2_EVIDENCE_FLOOR
            and math.isfinite(cvbd_delta)
            and cvbd_delta <= R2_CVBD_CEILING
            and math.isfinite(historical_delta)
            and historical_delta >= R2_HISTORICAL_FLOOR
        )
        out.append(
            {
                "old_method": "oldref_adapted_preserver",
                "postevent_method": postevent_method,
                "old_target_success": old_target,
                "postevent_target_success": postevent_target,
                "target_success_delta": target_delta,
                "abs_target_success_delta": abs_target_delta,
                "old_obsolete_answer_rate": float(old["obsolete_answer_rate"]),
                "postevent_obsolete_answer_rate": float(postevent["obsolete_answer_rate"]),
                "obsolete_answer_rate_reduction": obsolete_reduction,
                "old_evidence_hit": float(old["shifted_target_evidence_hit"]),
                "postevent_evidence_hit": float(postevent["shifted_target_evidence_hit"]),
                "evidence_hit_delta": evidence_delta,
                "old_cvbd": float(old["cvbd"]),
                "postevent_cvbd": float(postevent["cvbd"]),
                "cvbd_delta": cvbd_delta,
                "old_historical_accuracy": float(old["historical_accuracy"]),
                "postevent_historical_accuracy": float(postevent["historical_accuracy"]),
                "historical_accuracy_delta": historical_delta,
                "matched_target_success": matched,
                "r2_positive": positive,
            }
        )
    return out


def r3_event_type_summary(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, items in sorted(group_by(rows, ("event_type", "method", "query_type")).items()):
        event_type, method, query_type = key
        row: dict[str, Any] = {
            "event_type": event_type,
            "method": method,
            "method_family": method_family(method),
            "query_type": query_type,
            "row_count": len(items),
            "seed_count": len({item.get("seed", "") for item in items}),
            "event_count": len({item.get("event_id", "") for item in items}),
        }
        for field in R3_FIELDS:
            row[field] = row_mean(items, field)
        out.append(row)
    return out


def _r3_method_rows_for_event(rows: list[dict[str, str]], event_type: str) -> list[dict[str, Any]]:
    return [
        summarize_group(key, items, ("method",))
        for key, items in sorted(group_by([row for row in rows if row.get("event_type") == event_type], ("method",)).items())
    ]


def r3_event_regret(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event_type in sorted({row.get("event_type", "") for row in rows if row.get("event_type", "")}):
        event_rows = [row for row in rows if row.get("event_type") == event_type]
        ranking = ranking_summary(_r3_method_rows_for_event(rows, event_type))
        out.append(
            {
                "event_type": event_type,
                "event_boundary_role": "negative_control" if event_type == ADD else "positive_conflict",
                "row_count": len(event_rows),
                "seed_count": len({row.get("seed", "") for row in event_rows}),
                "event_count": len({row.get("event_id", "") for row in event_rows}),
                "obsolete_unqualified_row_count": len(
                    [row for row in event_rows if row.get("query_type") == OBSOLETE_UNQUALIFIED]
                ),
                **ranking,
            }
        )
    return out


def _r3_event_method_metrics(rows: list[dict[str, str]], event_type: str, method: str) -> dict[str, Any]:
    items = [row for row in rows if row.get("event_type") == event_type and row.get("method") == method]
    target_rows = [row for row in items if row.get("query_type") == TARGET]
    obsolete_rows = [row for row in items if row.get("query_type") == OBSOLETE_UNQUALIFIED]
    stable_rows = [row for row in items if row.get("query_type") == CURRENT_VALID_STABLE]
    historical_rows = [row for row in items if row.get("query_type") == HISTORICAL]
    return {
        "target_row_count": len(target_rows),
        "target_success": row_mean(target_rows, "target_success"),
        "target_evidence_hit": row_mean(target_rows, "evidence_hit"),
        "target_retrieval_recall_at_k": row_mean(target_rows, "retrieval_recall_at_k"),
        "obsolete_row_count": len(obsolete_rows),
        "obsolete_answer_rate": row_mean(obsolete_rows, "obsolete_answer_rate"),
        "obsolete_evidence_hit": row_mean(obsolete_rows, "obsolete_evidence_hit"),
        "cvbd_row_count": len(stable_rows),
        "cvbd": row_mean(stable_rows, "cvbd"),
        "historical_row_count": len(historical_rows),
        "historical_accuracy": row_mean(historical_rows, "historical_accuracy"),
    }


def _nan_delta(left: float, right: float) -> float:
    if math.isfinite(left) and math.isfinite(right):
        return left - right
    return math.nan


def r3_r2_by_event(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    event_types = sorted({row.get("event_type", "") for row in rows if row.get("event_type", "")})
    for event_type in event_types:
        old = _r3_event_method_metrics(rows, event_type, "oldref_adapted_preserver")
        store = _r3_event_method_metrics(rows, event_type, "store_only")
        for postevent_method in ["postevent_validity_filter", "intent_aware_postevent_filter"]:
            postevent = _r3_event_method_metrics(rows, event_type, postevent_method)
            if old["target_row_count"] == 0 or postevent["target_row_count"] == 0:
                continue
            target_delta = _nan_delta(float(postevent["target_success"]), float(old["target_success"]))
            abs_target_delta = abs(target_delta) if math.isfinite(target_delta) else math.nan
            obsolete_reduction = _nan_delta(
                float(old["obsolete_answer_rate"]),
                float(postevent["obsolete_answer_rate"]),
            )
            evidence_delta = _nan_delta(
                float(postevent["target_evidence_hit"]),
                float(old["target_evidence_hit"]),
            )
            cvbd_delta = _nan_delta(float(postevent["cvbd"]), float(old["cvbd"]))
            historical_delta = _nan_delta(
                float(postevent["historical_accuracy"]),
                float(old["historical_accuracy"]),
            )
            role = "negative_control" if event_type == ADD else "positive_conflict"
            negative_control_pass = (
                event_type == ADD
                and int(old["obsolete_row_count"]) == 0
                and int(postevent["obsolete_row_count"]) == 0
                and int(store["obsolete_row_count"]) == 0
                and math.isfinite(float(store["target_success"]))
                and float(store["target_success"]) >= R3_TARGET_HIGH
                and math.isfinite(float(postevent["target_success"]))
                and float(postevent["target_success"]) >= R3_TARGET_HIGH
            )
            positive = (
                event_type in SHIFTED_EVENT_TYPES
                and math.isfinite(float(store["target_success"]))
                and float(store["target_success"]) <= R3_TARGET_LOW
                and math.isfinite(float(old["target_success"]))
                and float(old["target_success"]) >= R3_TARGET_HIGH
                and math.isfinite(float(old["obsolete_answer_rate"]))
                and float(old["obsolete_answer_rate"]) >= R3_OBSOLETE_HIGH
                and math.isfinite(float(postevent["obsolete_answer_rate"]))
                and float(postevent["obsolete_answer_rate"]) <= R3_OBSOLETE_LOW
                and math.isfinite(abs_target_delta)
                and abs_target_delta <= R2_MATCH_TOLERANCE
                and math.isfinite(evidence_delta)
                and evidence_delta >= R2_EVIDENCE_FLOOR
                and math.isfinite(cvbd_delta)
                and cvbd_delta <= R2_CVBD_CEILING
                and math.isfinite(float(postevent["historical_accuracy"]))
                and float(postevent["historical_accuracy"]) >= R3_HISTORICAL_FLOOR
                and math.isfinite(historical_delta)
                and historical_delta >= R2_HISTORICAL_FLOOR
            )
            out.append(
                {
                    "event_type": event_type,
                    "event_boundary_role": role,
                    "old_method": "oldref_adapted_preserver",
                    "postevent_method": postevent_method,
                    "store_only_target_success": store["target_success"],
                    "old_target_success": old["target_success"],
                    "postevent_target_success": postevent["target_success"],
                    "target_success_delta": target_delta,
                    "abs_target_success_delta": abs_target_delta,
                    "old_obsolete_answer_rate": old["obsolete_answer_rate"],
                    "postevent_obsolete_answer_rate": postevent["obsolete_answer_rate"],
                    "obsolete_answer_rate_reduction": obsolete_reduction,
                    "old_evidence_hit": old["target_evidence_hit"],
                    "postevent_evidence_hit": postevent["target_evidence_hit"],
                    "evidence_hit_delta": evidence_delta,
                    "old_cvbd": old["cvbd"],
                    "postevent_cvbd": postevent["cvbd"],
                    "cvbd_delta": cvbd_delta,
                    "old_historical_accuracy": old["historical_accuracy"],
                    "postevent_historical_accuracy": postevent["historical_accuracy"],
                    "historical_accuracy_delta": historical_delta,
                    "old_obsolete_row_count": old["obsolete_row_count"],
                    "postevent_obsolete_row_count": postevent["obsolete_row_count"],
                    "store_only_obsolete_row_count": store["obsolete_row_count"],
                    "negative_control_pass": negative_control_pass if event_type == ADD else math.nan,
                    "r3_event_positive": "not_applicable" if event_type == ADD else positive,
                    "r3_event_gate_pass": negative_control_pass if event_type == ADD else positive,
                }
            )
    return out


def query_summary(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    return [
        summarize_group(key, items, ("method", "query_type"))
        for key, items in sorted(group_by(rows, ("method", "query_type")).items())
    ]


def decomposition_summary(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    out = []
    for key, items in sorted(group_by(rows, ("method", "event_type")).items()):
        row = summarize_group(key, items, ("method", "event_type"))
        store = row["store_response_drift"]
        side = row["adaptation_side_effect"]
        total = row["total_drift"]
        row["decomposition_residual"] = total - store - side if all(
            math.isfinite(value) for value in [store, side, total]
        ) else math.nan
        out.append(row)
    return out


def ranking_summary(method_rows: list[dict[str, Any]]) -> dict[str, Any]:
    clean = [
        row
        for row in method_rows
        if math.isfinite(float(row["old_reference_retention"]))
        and math.isfinite(float(row["current_valid_accuracy"]))
    ]
    if len(clean) < 2:
        return {
            "method_count": len(clean),
            "spearman_old_retention_vs_current_valid_accuracy": math.nan,
            "top_old_reference_method": "NA",
            "best_current_valid_method": "NA",
            "metric_inversion_detected": False,
        }
    top_old = max(clean, key=lambda row: float(row["old_reference_retention"]))
    best_current = max(clean, key=lambda row: float(row["current_valid_accuracy"]))
    inversion = (
        top_old["method"] != best_current["method"]
        and float(top_old["current_valid_accuracy"]) < float(best_current["current_valid_accuracy"])
    )
    return {
        "method_count": len(clean),
        "spearman_old_retention_vs_current_valid_accuracy": spearman(
            [float(row["old_reference_retention"]) for row in clean],
            [float(row["current_valid_accuracy"]) for row in clean],
        ),
        "top_old_reference_method": top_old["method"],
        "top_old_reference_retention": top_old["old_reference_retention"],
        "top_old_current_valid_accuracy": top_old["current_valid_accuracy"],
        "best_current_valid_method": best_current["method"],
        "best_current_valid_accuracy": best_current["current_valid_accuracy"],
        "best_current_old_reference_retention": best_current["old_reference_retention"],
        "current_valid_regret_of_top_old": (
            float(best_current["current_valid_accuracy"]) - float(top_old["current_valid_accuracy"])
        ),
        "metric_inversion_detected": inversion,
    }


def report_text(
    *,
    input_dir: Path,
    manifest: dict[str, Any],
    methods: list[dict[str, Any]],
    ranking: dict[str, Any],
    legacy_scalar_grid_pairs: list[dict[str, Any]] | None = None,
    legacy_scalar_grid_matched: list[dict[str, Any]] | None = None,
    r2_gate: list[dict[str, Any]] | None = None,
    r3_event_regret: list[dict[str, Any]] | None = None,
    r3_r2_by_event: list[dict[str, Any]] | None = None,
) -> str:
    has_doc_metrics = any(
        math.isfinite(float(row.get("retrieval_recall_at_k", math.nan)))
        for row in methods
    )
    lines = [
        "# RAG Post-Event Drift Report",
        "",
        "Scope: controlled structured QA substrate for evolving knowledge-store RAG semantics.",
        "Formal claims should cite T4-generated outputs only.",
        "",
        "## Provenance",
        "",
        f"- Input directory: `{input_dir}`",
        f"- Command: `{manifest.get('environment', {}).get('command', 'NA')}`",
        f"- Device: `{manifest.get('environment', {}).get('device', 'NA')}`",
        f"- CUDA device: `{manifest.get('environment', {}).get('cuda_device', 'NA')}`",
        f"- Git SHA: `{manifest.get('git', {}).get('git_sha', 'NA')}`",
        f"- Git dirty: `{manifest.get('git', {}).get('git_dirty', 'NA')}`",
        "",
        "## Metric Inversion Gate",
        "",
        f"- Spearman(old-reference retention, current-valid accuracy): {fmt(ranking.get('spearman_old_retention_vs_current_valid_accuracy'))}",
        f"- Top old-reference method: `{ranking.get('top_old_reference_method', 'NA')}`",
        f"- Best current-valid method: `{ranking.get('best_current_valid_method', 'NA')}`",
        f"- Current-valid regret of top old-reference method: {fmt(ranking.get('current_valid_regret_of_top_old', math.nan))}",
        f"- Metric inversion detected: `{ranking.get('metric_inversion_detected', False)}`",
        "",
        "## Method Summary",
        "",
        "| Method | Current-valid acc | Old-ref retention | Post-event retention | Obsolete answer rate | CVBD |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(methods, key=lambda item: str(item["method"])):
        lines.append(
            "| {method} | {cva} | {old} | {post} | {obsolete} | {cvbd} |".format(
                method=row["method"],
                cva=fmt(row["current_valid_accuracy"]),
                old=fmt(row["old_reference_retention"]),
                post=fmt(row["postevent_retention"]),
                obsolete=fmt(row["obsolete_answer_rate"]),
                cvbd=fmt(row["cvbd"]),
            )
        )
    if legacy_scalar_grid_pairs:
        matched = legacy_scalar_grid_matched or []
        positives = [
            row
            for row in matched
            if str(row.get("legacy_scalar_grid_positive", "")).lower() == "true"
            or row.get("legacy_scalar_grid_positive") is True
        ]
        best = None
        if matched:
            best = max(
                matched,
                key=lambda row: (
                    float(row["obsolete_answer_rate_reduction"])
                    if math.isfinite(float(row["obsolete_answer_rate_reduction"]))
                    else -math.inf,
                    -float(row["abs_target_success_delta"]),
                ),
            )
        lines.extend(
            [
                "",
                "## Legacy Scalar-Grid Diagnostic",
                "",
                f"- Pair grid rows: {len(legacy_scalar_grid_pairs)}",
                f"- Matched pairs at tolerance {R2_MATCH_TOLERANCE:.2f}: {len(matched)}",
                f"- Paper-ready positive pair exists: `{bool(positives)}`",
            ]
        )
        if best is None:
            lines.append("- Best matched pair: `NA`")
        else:
            lines.append(
                "- Best matched pair: `{old}` vs `{post}`; target gap {gap}; obsolete reduction {reduction}; evidence delta {evidence}; CVBD change {cvbd}.".format(
                    old=best["old_method"],
                    post=best["postevent_method"],
                    gap=fmt(float(best["abs_target_success_delta"])),
                    reduction=fmt(float(best["obsolete_answer_rate_reduction"])),
                    evidence=fmt(float(best["evidence_hit_delta"])),
                    cvbd=fmt(float(best["cvbd_change"])),
                )
            )
    if r2_gate:
        positives = [
            row
            for row in r2_gate
            if str(row.get("r2_positive", "")).lower() == "true" or row.get("r2_positive") is True
        ]
        lines.extend(
            [
                "",
                "## R2 Validity-Control Gate",
                "",
                f"- Gate rows: {len(r2_gate)}",
                f"- Positive post-event validity-control pair exists: `{bool(positives)}`",
                "| Old method | Post-event method | Target gap | Obsolete reduction | Evidence delta | CVBD delta | Historical delta | Positive |",
                "|---|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in r2_gate:
            lines.append(
                "| {old} | {post} | {gap} | {obs} | {ev} | {cvbd} | {hist} | {positive} |".format(
                    old=row["old_method"],
                    post=row["postevent_method"],
                    gap=fmt(float(row["abs_target_success_delta"])),
                    obs=fmt(float(row["obsolete_answer_rate_reduction"])),
                    ev=fmt(float(row["evidence_hit_delta"])),
                    cvbd=fmt(float(row["cvbd_delta"])),
                    hist=fmt(float(row["historical_accuracy_delta"])),
                    positive=row["r2_positive"],
                )
            )
    if r3_r2_by_event:
        add_rows = [row for row in r3_r2_by_event if row.get("event_type") == ADD]
        shifted_rows = [row for row in r3_r2_by_event if row.get("event_type") in SHIFTED_EVENT_TYPES]
        add_pass = bool(add_rows) and all(row.get("r3_event_gate_pass") is True for row in add_rows)
        shifted_pass = bool(shifted_rows) and all(row.get("r3_event_gate_pass") is True for row in shifted_rows)
        lines.extend(
            [
                "",
                "## R3 Event-Type Boundary",
                "",
                f"- ADD negative-control gate passes: `{add_pass}`",
                f"- REVISE/RETIRE positive-conflict gates pass separately: `{shifted_pass}`",
                "- ADD is not counted as an obsolete-conflict positive case.",
                "| Event type | Role | Post-event method | Store-only target | Oldref target | Postevent target | Obsolete reduction | Historical delta | Gate |",
                "|---|---|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for row in r3_r2_by_event:
            lines.append(
                "| {event} | {role} | {post} | {store_target} | {old_target} | {post_target} | {obs} | {hist} | {gate} |".format(
                    event=row["event_type"],
                    role=row["event_boundary_role"],
                    post=row["postevent_method"],
                    store_target=fmt(float(row["store_only_target_success"])),
                    old_target=fmt(float(row["old_target_success"])),
                    post_target=fmt(float(row["postevent_target_success"])),
                    obs=fmt(float(row["obsolete_answer_rate_reduction"])),
                    hist=fmt(float(row["historical_accuracy_delta"])),
                    gate=row["r3_event_gate_pass"],
                )
            )
    if r3_event_regret:
        lines.extend(
            [
                "",
                "### R3 Event-Type Regret",
                "",
                "| Event type | Role | Top old-reference method | Best current-valid method | Regret | Spearman |",
                "|---|---|---|---|---:|---:|",
            ]
        )
        for row in r3_event_regret:
            lines.append(
                "| {event} | {role} | {top_old} | {best} | {regret} | {spearman} |".format(
                    event=row["event_type"],
                    role=row["event_boundary_role"],
                    top_old=row.get("top_old_reference_method", "NA"),
                    best=row.get("best_current_valid_method", "NA"),
                    regret=fmt(row.get("current_valid_regret_of_top_old", math.nan)),
                    spearman=fmt(row.get("spearman_old_retention_vs_current_valid_accuracy", math.nan)),
                )
            )
    if has_doc_metrics:
        lines.extend(
            [
                "",
                "## Document Evidence Summary",
                "",
                "| Method | Retrieval recall@k | Evidence hit | Obsolete evidence hit | Mean chunk rank |",
                "|---|---:|---:|---:|---:|",
            ]
        )
        for row in sorted(methods, key=lambda item: str(item["method"])):
            lines.append(
                "| {method} | {recall} | {hit} | {obsolete_hit} | {rank} |".format(
                    method=row["method"],
                    recall=fmt(row.get("retrieval_recall_at_k", math.nan)),
                    hit=fmt(row.get("evidence_hit", math.nan)),
                    obsolete_hit=fmt(row.get("obsolete_evidence_hit", math.nan)),
                    rank=fmt(row.get("chunk_rank", math.nan)),
                )
            )
    lines.extend(
        [
            "",
            "## Machine-Readable Outputs",
            "",
            "- `rag_postevent_method_summary.csv`",
            "- `rag_postevent_query_summary.csv`",
            "- `rag_postevent_decomposition_summary.csv`",
            "- `rag_postevent_ranking_summary.csv`",
            "- `rag_postevent_r2_method_summary.csv`",
            "- `rag_postevent_legacy_scalar_grid_pair_grid.csv`",
            "- `rag_postevent_legacy_scalar_grid_matched_pairs.csv`",
            "- `rag_postevent_r2_gate.csv`",
            "- `rag_postevent_r3_event_type_summary.csv`",
            "- `rag_postevent_r3_event_regret.csv`",
            "- `rag_postevent_r3_r2_by_event.csv`",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    metrics = read_csv(args.input_dir / "rag_postevent_metrics.csv")
    manifest = read_json(args.input_dir / "run_manifest.json")
    if not metrics:
        raise SystemExit(f"No metrics found in {args.input_dir}")

    method_rows = method_summary(metrics)
    query_rows = query_summary(metrics)
    decomposition_rows = decomposition_summary(metrics)
    ranking = ranking_summary(method_rows)
    r2_method_rows = r2_method_summary(metrics)
    legacy_scalar_grid_pair_rows = legacy_scalar_grid_pair_grid(r2_method_rows)
    legacy_scalar_grid_matched_rows = legacy_scalar_grid_matched_pairs(legacy_scalar_grid_pair_rows)
    r2_rows = r2_gate_rows(r2_method_rows)
    r3_event_type_rows = r3_event_type_summary(metrics)
    r3_event_regret_rows = r3_event_regret(metrics)
    r3_r2_by_event_rows = r3_r2_by_event(metrics)

    write_csv(args.input_dir / "rag_postevent_method_summary.csv", method_rows)
    write_csv(args.input_dir / "rag_postevent_query_summary.csv", query_rows)
    write_csv(args.input_dir / "rag_postevent_decomposition_summary.csv", decomposition_rows)
    write_csv(args.input_dir / "rag_postevent_ranking_summary.csv", [ranking])
    write_csv(args.input_dir / "rag_postevent_r2_method_summary.csv", r2_method_rows)
    write_csv(args.input_dir / "rag_postevent_legacy_scalar_grid_pair_grid.csv", legacy_scalar_grid_pair_rows)
    write_csv(args.input_dir / "rag_postevent_legacy_scalar_grid_matched_pairs.csv", legacy_scalar_grid_matched_rows)
    write_csv(args.input_dir / "rag_postevent_r2_gate.csv", r2_rows)
    write_csv(args.input_dir / "rag_postevent_r3_event_type_summary.csv", r3_event_type_rows)
    write_csv(args.input_dir / "rag_postevent_r3_event_regret.csv", r3_event_regret_rows)
    write_csv(args.input_dir / "rag_postevent_r3_r2_by_event.csv", r3_r2_by_event_rows)

    output = args.output or (args.input_dir / "rag_postevent_report.md")
    output.write_text(
        report_text(
            input_dir=args.input_dir,
            manifest=manifest,
            methods=method_rows,
            ranking=ranking,
            legacy_scalar_grid_pairs=legacy_scalar_grid_pair_rows,
            legacy_scalar_grid_matched=legacy_scalar_grid_matched_rows,
            r2_gate=r2_rows,
            r3_event_regret=r3_event_regret_rows,
            r3_r2_by_event=r3_r2_by_event_rows,
        ),
        encoding="utf-8",
    )
    print(f"[done] RAG post-event report: {output}")


if __name__ == "__main__":
    main()
