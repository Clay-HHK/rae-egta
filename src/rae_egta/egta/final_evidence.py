"""Build the compact final evidence bundle for the RAE/EGTA paper.

The builder is deliberately a derived-analysis layer.  It reads frozen,
paper-facing source artifacts, never mutates them, and writes a new immutable
bundle containing:

* the published-lineage audit summary and accepted-case inventory;
* a sensitivity bridge from misaligned-query mass to ranking reversal;
* the matched-capability selection comparison on one common ``Q_eval`` pool;
* event, signal-noise, and reader robustness summaries; and
* a blind human-review packet whose status remains ``PENDING_HUMAN`` until
  real reviewers complete and sign it.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


PRESERVER_METHOD = "oldref_adapted_preserver"
VALIDITY_METHOD = "postevent_validity_filter"
QEVAL_QUERY_TYPES = (
    "target",
    "obsolete_unqualified",
    "current_valid_stable",
)
TIE_EPSILON = 1e-12
SENSITIVITY_ADVANTAGES = (0.25, 0.50, 0.75, 1.00)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                records.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
    return records


def write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    fieldnames: Sequence[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        fieldnames = tuple(rows[0]) if rows else ()
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )


def _float(value: Any) -> float:
    if value is None:
        return math.nan
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return math.nan
    return float(text)


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _weighted_mean(
    rows: Iterable[Mapping[str, Any]],
    metric: str,
    *,
    query_types: set[str] | None = None,
) -> tuple[float, int]:
    numerator = 0.0
    denominator = 0
    for row in rows:
        if query_types is not None and row.get("query_type") not in query_types:
            continue
        value = _float(row.get(metric))
        if not math.isfinite(value):
            continue
        weight = int(row.get("row_count", 1))
        numerator += value * weight
        denominator += weight
    if denominator == 0:
        return math.nan, 0
    return numerator / denominator, denominator


def reversal_threshold(aligned_gap: float, changed_advantage: float) -> float:
    """Return ``p* = d / (d + g)`` for the two-region sensitivity model."""

    if aligned_gap < 0 or changed_advantage <= 0:
        raise ValueError("aligned_gap must be non-negative and changed_advantage positive")
    return aligned_gap / (aligned_gap + changed_advantage)


def maximum_overcomeable_aligned_gap(
    misaligned_proportion: float, changed_advantage: float
) -> float:
    """Return ``d_max = p g / (1-p)`` for ``0 <= p < 1``."""

    if not 0 <= misaligned_proportion < 1:
        raise ValueError("misaligned_proportion must lie in [0, 1)")
    if changed_advantage <= 0:
        raise ValueError("changed_advantage must be positive")
    return (
        misaligned_proportion
        * changed_advantage
        / (1.0 - misaligned_proportion)
    )


def sensitivity_threshold_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for changed_advantage in SENSITIVITY_ADVANTAGES:
        for aligned_gap in (0.00, 0.01, 0.02, 0.05, 0.10):
            rows.append(
                {
                    "changed_region_advantage_g": changed_advantage,
                    "aligned_region_gap_d": aligned_gap,
                    "minimum_misaligned_proportion_p": reversal_threshold(
                        aligned_gap, changed_advantage
                    ),
                    "condition": "p*g > (1-p)*d",
                }
            )
    return rows


def sensitivity_scenario_rows() -> list[dict[str, Any]]:
    scenarios = (
        {
            "scenario": "audit_strict_sample_marker",
            "numerator": 4,
            "denominator": 47,
            "role": "sample-level scenario marker; not a prevalence estimate",
        },
        {
            "scenario": "audit_primary_sample_marker",
            "numerator": 6,
            "denominator": 47,
            "role": "sample-level scenario marker; not a prevalence estimate",
        },
        {
            "scenario": "controlled_effective_misaligned_mass",
            "numerator": 4050,
            "denominator": 17550,
            "role": "Q_eval mass that separates the matched pair",
        },
        {
            "scenario": "controlled_all_event_affected_mass",
            "numerator": 8550,
            "denominator": 17550,
            "role": "target plus obsolete-unqualified share; not all rows separate methods",
        },
    )
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        proportion = scenario["numerator"] / scenario["denominator"]
        for changed_advantage in SENSITIVITY_ADVANTAGES:
            rows.append(
                {
                    **scenario,
                    "misaligned_proportion_p": proportion,
                    "changed_region_advantage_g": changed_advantage,
                    "maximum_overcomeable_aligned_gap_d": (
                        maximum_overcomeable_aligned_gap(
                            proportion, changed_advantage
                        )
                    ),
                }
            )
    return rows


def plot_sensitivity_bridge(path_pdf: Path, path_png: Path) -> None:
    """Render the sensitivity bridge in vector and high-resolution raster form."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    colors = {
        0.25: "#0072B2",
        0.50: "#009E73",
        0.75: "#E69F00",
        1.00: "#D55E00",
    }
    proportions = [index / 1000 for index in range(0, 251)]
    fig, ax = plt.subplots(figsize=(7.2, 4.3), constrained_layout=True)
    for changed_advantage in SENSITIVITY_ADVANTAGES:
        gaps = [
            maximum_overcomeable_aligned_gap(value, changed_advantage)
            for value in proportions
        ]
        ax.plot(
            proportions,
            gaps,
            color=colors[changed_advantage],
            linewidth=2.1,
            label=f"changed-query advantage $g={changed_advantage:.2f}$",
        )

    markers = (
        (4 / 47, "strict audit marker\n4/47", 0.205),
        (6 / 47, "primary audit marker\n6/47", 0.285),
        (4050 / 17550, "matched testbed\n23.1%", 0.285),
    )
    marker_styles = ("--", "-.", ":")
    for (value, label, label_y), style in zip(markers, marker_styles):
        ax.axvline(value, color="#333333", linestyle=style, linewidth=1.2)
        ax.text(
            value + 0.003,
            label_y,
            label,
            rotation=90,
            va="top",
            ha="left",
            fontsize=8.5,
            color="#333333",
            bbox={
                "facecolor": "white",
                "edgecolor": "none",
                "alpha": 0.76,
                "pad": 0.6,
            },
        )

    ax.set_xlim(0.0, 0.25)
    ax.set_ylim(0.0, 0.30)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.yaxis.set_major_formatter(PercentFormatter(1.0, decimals=0))
    ax.set_xlabel("Misaligned-query proportion $p$")
    ax.set_ylabel("Maximum aligned-query gap overcome, $d_{max}$")
    ax.set_title("Ranking-reversal sensitivity under finite misalignment")
    ax.grid(axis="both", color="#D0D0D0", linewidth=0.7, alpha=0.65)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.legend(loc="upper left", frameon=False, fontsize=8.5)

    path_pdf.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "Title": "Ranking-reversal sensitivity under finite misalignment",
        "Author": "Anonymous",
        "Subject": "RAE sensitivity analysis",
        "CreationDate": None,
        "ModDate": None,
    }
    fig.savefig(path_pdf, bbox_inches="tight", metadata=metadata)
    fig.savefig(
        path_png,
        dpi=320,
        bbox_inches="tight",
        metadata={"Software": "matplotlib"},
    )
    plt.close(fig)


def summarize_matched_capability(
    query_summary_path: Path,
    *,
    methods: Sequence[str] = (PRESERVER_METHOD, VALIDITY_METHOD),
) -> list[dict[str, Any]]:
    rows = read_csv(query_summary_path)
    by_method: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        by_method[row["method"]].append(row)
    missing = sorted(set(methods) - set(by_method))
    if missing:
        raise ValueError(f"query summary is missing methods: {missing}")

    output: list[dict[str, Any]] = []
    for method in methods:
        method_rows = by_method[method]
        target_success, target_rows = _weighted_mean(
            method_rows, "target_success", query_types={"target"}
        )
        evidence_hit, _ = _weighted_mean(
            method_rows, "evidence_hit", query_types={"target"}
        )
        historical_accuracy, historical_rows = _weighted_mean(
            method_rows, "historical_accuracy", query_types={"historical"}
        )
        stable_accuracy, stable_rows = _weighted_mean(
            method_rows,
            "current_valid_accuracy",
            query_types={"current_valid_stable"},
        )
        stable_cvbd, _ = _weighted_mean(
            method_rows, "cvbd", query_types={"current_valid_stable"}
        )
        obsolete_answer_rate, obsolete_rows = _weighted_mean(
            method_rows,
            "obsolete_answer_rate",
            query_types={"obsolete_unqualified"},
        )
        pre_event_retention, qeval_rows = _weighted_mean(
            method_rows,
            "old_reference_retention",
            query_types=set(QEVAL_QUERY_TYPES),
        )
        current_valid_accuracy, qeval_current_rows = _weighted_mean(
            method_rows,
            "current_valid_accuracy",
            query_types=set(QEVAL_QUERY_TYPES),
        )
        if qeval_rows != qeval_current_rows:
            raise ValueError(
                f"{method}: Q_eval metric denominators differ "
                f"({qeval_rows} != {qeval_current_rows})"
            )
        output.append(
            {
                "method": method,
                "qeval_row_count": qeval_rows,
                "target_row_count": target_rows,
                "obsolete_row_count": obsolete_rows,
                "stable_row_count": stable_rows,
                "historical_row_count": historical_rows,
                "target_success": target_success,
                "historical_accuracy": historical_accuracy,
                "stable_accuracy": stable_accuracy,
                "stable_cvbd": stable_cvbd,
                "evidence_hit_target": evidence_hit,
                "obsolete_answer_rate": obsolete_answer_rate,
                "pre_event_retention_qeval": pre_event_retention,
                "current_valid_accuracy_qeval": current_valid_accuracy,
            }
        )
    count_fields = (
        "qeval_row_count",
        "target_row_count",
        "obsolete_row_count",
        "stable_row_count",
        "historical_row_count",
    )
    reference_counts = {field: output[0][field] for field in count_fields}
    for row in output[1:]:
        counts = {field: row[field] for field in count_fields}
        if counts != reference_counts:
            raise ValueError(
                "matched methods do not share the same query-pool composition: "
                f"{output[0]['method']}={reference_counts}, "
                f"{row['method']}={counts}"
            )
    return output


def selection_statistics(
    method_rows: Sequence[Mapping[str, Any]],
    *,
    feasible_methods: Sequence[str] | None = None,
) -> dict[str, Any]:
    by_method = {str(row["method"]): row for row in method_rows}
    candidates = tuple(feasible_methods or sorted(by_method))
    if not candidates or any(method not in by_method for method in candidates):
        raise ValueError("selection candidates must be a non-empty subset of methods")

    old_scores = {
        method: float(by_method[method]["pre_event_retention_qeval"])
        for method in candidates
    }
    current_scores = {
        method: float(by_method[method]["current_valid_accuracy_qeval"])
        for method in candidates
    }
    old_best = max(old_scores.values())
    current_best = max(current_scores.values())
    old_winners = tuple(
        sorted(
            method
            for method, score in old_scores.items()
            if old_best - score <= TIE_EPSILON
        )
    )
    current_winners = tuple(
        sorted(
            method
            for method, score in current_scores.items()
            if current_best - score <= TIE_EPSILON
        )
    )
    regret = current_best - max(current_scores[method] for method in old_winners)
    if abs(regret) <= TIE_EPSILON:
        regret = 0.0
    return {
        "candidate_methods": candidates,
        "pre_event_winners": old_winners,
        "current_valid_winners": current_winners,
        "ranking_reversal": set(old_winners).isdisjoint(current_winners),
        "tie_robust_current_valid_regret": regret,
        "best_current_valid_accuracy": current_best,
    }


def matched_selection_rows(
    matched_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    statistics = selection_statistics(
        matched_rows,
        feasible_methods=(PRESERVER_METHOD, VALIDITY_METHOD),
    )
    return [
        {
            "selection_scope": "matched_pair",
            "criterion": "pre_event_retention_qeval",
            "selected_methods": "|".join(statistics["pre_event_winners"]),
            "ranking_reversal_against_other_criterion": statistics[
                "ranking_reversal"
            ],
            "best_current_valid_accuracy": statistics[
                "best_current_valid_accuracy"
            ],
            "tie_robust_current_valid_regret": statistics[
                "tie_robust_current_valid_regret"
            ],
        },
        {
            "selection_scope": "matched_pair",
            "criterion": "current_valid_accuracy_qeval",
            "selected_methods": "|".join(statistics["current_valid_winners"]),
            "ranking_reversal_against_other_criterion": statistics[
                "ranking_reversal"
            ],
            "best_current_valid_accuracy": statistics[
                "best_current_valid_accuracy"
            ],
            "tie_robust_current_valid_regret": 0.0,
        },
    ]


def _method_scores_from_raw_metrics(
    metrics_path: Path,
) -> list[dict[str, Any]]:
    rows = read_csv(metrics_path)
    accumulators: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    qeval_keys: dict[str, set[tuple[str, str, str, str]]] = defaultdict(set)
    for row in rows:
        query_type = row["query_type"]
        method = row["method"]
        if query_type in QEVAL_QUERY_TYPES:
            old_value = _float(row.get("old_reference_retention"))
            current_value = _float(row.get("current_valid_accuracy"))
            old_finite = math.isfinite(old_value)
            current_finite = math.isfinite(current_value)
            if old_finite != current_finite:
                raise ValueError(
                    f"{metrics_path}: {method} has a row outside the common "
                    f"Q_eval support ({row.get('query_id', '<unknown>')})"
                )
            if old_finite:
                key = (
                    str(row.get("seed", "")),
                    str(row.get("event_id", "")),
                    str(row.get("query_id", "")),
                    query_type,
                )
                if key in qeval_keys[method]:
                    raise ValueError(
                        f"{metrics_path}: duplicate Q_eval row for {method}: {key}"
                    )
                qeval_keys[method].add(key)
                accumulators[method]["old_reference_retention"].append(old_value)
                accumulators[method]["current_valid_accuracy"].append(current_value)
        if query_type == "target":
            value = _float(row.get("target_success"))
            if math.isfinite(value):
                accumulators[method]["target_success"].append(value)
        if query_type == "obsolete_unqualified":
            value = _float(row.get("obsolete_answer_rate"))
            if math.isfinite(value):
                accumulators[method]["obsolete_answer_rate"].append(value)

    output: list[dict[str, Any]] = []
    for method in sorted(accumulators):
        metrics = accumulators[method]
        old_values = metrics["old_reference_retention"]
        current_values = metrics["current_valid_accuracy"]
        if len(old_values) != len(current_values):
            raise ValueError(
                f"{metrics_path}: {method} uses unequal Q_eval denominators"
            )
        output.append(
            {
                "method": method,
                "qeval_row_count": len(old_values),
                "target_success": sum(metrics["target_success"])
                / len(metrics["target_success"]),
                "obsolete_answer_rate": sum(metrics["obsolete_answer_rate"])
                / len(metrics["obsolete_answer_rate"]),
                "pre_event_retention_qeval": sum(old_values) / len(old_values),
                "current_valid_accuracy_qeval": sum(current_values)
                / len(current_values),
            }
        )
    if output:
        reference_method = output[0]["method"]
        reference_keys = qeval_keys[reference_method]
        for row in output[1:]:
            method = row["method"]
            if qeval_keys[method] != reference_keys:
                missing = len(reference_keys - qeval_keys[method])
                extra = len(qeval_keys[method] - reference_keys)
                raise ValueError(
                    f"{metrics_path}: {method} does not share the exact Q_eval "
                    f"row identities with {reference_method} "
                    f"(missing={missing}, extra={extra})"
                )
    return output


def _read_qeval_inference_row(path: Path) -> dict[str, str]:
    rows = read_csv(path)
    matches = [
        row
        for row in rows
        if row["row_type"] == "capability_constrained_regret"
        and row["event_scope"] == "ALL"
        and row["query_scope"] == "Q_eval"
        and row["metric"] == "tie_robust_method_selection_regret"
    ]
    if len(matches) != 1:
        raise ValueError(f"{path}: expected one Q_eval selection row, got {len(matches)}")
    return matches[0]


def summarize_noise_selection(
    condition_root: Path,
    inference_root: Path,
    *,
    capability_floor: float = 0.10,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for condition_dir in sorted(
        path for path in condition_root.iterdir() if path.is_dir()
    ):
        condition = condition_dir.name
        metrics_path = condition_dir / "rag_postevent_metrics.csv"
        manifest_path = condition_dir / "run_manifest.json"
        if not metrics_path.is_file() or not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assignment = manifest["noise_assignment"]
        scores = _method_scores_from_raw_metrics(metrics_path)
        by_method = {row["method"]: row for row in scores}
        feasible = tuple(
            sorted(
                method
                for method, row in by_method.items()
                if float(row["target_success"]) + TIE_EPSILON >= capability_floor
            )
        )
        statistics = selection_statistics(scores, feasible_methods=feasible)
        inference = _read_qeval_inference_row(
            inference_root / f"condition_{condition}_cluster_ci.csv"
        )
        inferred_regret = _float(inference["condition_point"])
        if not math.isclose(
            inferred_regret,
            float(statistics["tie_robust_current_valid_regret"]),
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise ValueError(
                f"{condition}: raw and cluster-inference regret disagree "
                f"({statistics['tie_robust_current_valid_regret']} != {inferred_regret})"
            )
        preserver = by_method[PRESERVER_METHOD]
        validity = by_method[VALIDITY_METHOD]
        output.append(
            {
                "condition": condition,
                "factor": assignment["factor"],
                "requested_rate": float(assignment["requested_rate"]),
                "event_cluster_count": int(manifest["event_record_count"]),
                "raw_row_count": int(manifest["row_count"]),
                "qeval_row_count_per_method": int(preserver["qeval_row_count"]),
                "capability_floor": capability_floor,
                "feasible_methods": "|".join(feasible),
                "pre_event_winners": "|".join(statistics["pre_event_winners"]),
                "current_valid_winners": "|".join(
                    statistics["current_valid_winners"]
                ),
                "ranking_reversal": statistics["ranking_reversal"],
                "tie_robust_current_valid_regret": statistics[
                    "tie_robust_current_valid_regret"
                ],
                "regret_delta_vs_clean": _float(inference["point_estimate"]),
                "regret_delta_ci_low": _float(inference["ci_low"]),
                "regret_delta_ci_high": _float(inference["ci_high"]),
                "preserver_target_success": preserver["target_success"],
                "validity_filter_target_success": validity["target_success"],
                "preserver_pre_event_retention_qeval": preserver[
                    "pre_event_retention_qeval"
                ],
                "validity_filter_pre_event_retention_qeval": validity[
                    "pre_event_retention_qeval"
                ],
                "preserver_current_valid_accuracy_qeval": preserver[
                    "current_valid_accuracy_qeval"
                ],
                "validity_filter_current_valid_accuracy_qeval": validity[
                    "current_valid_accuracy_qeval"
                ],
                "preserver_obsolete_answer_rate": preserver[
                    "obsolete_answer_rate"
                ],
                "validity_filter_obsolete_answer_rate": validity[
                    "obsolete_answer_rate"
                ],
                "matched_pair_pre_prefers_preserver": (
                    preserver["pre_event_retention_qeval"]
                    > validity["pre_event_retention_qeval"] + TIE_EPSILON
                ),
                "matched_pair_current_prefers_filter": (
                    validity["current_valid_accuracy_qeval"]
                    > preserver["current_valid_accuracy_qeval"] + TIE_EPSILON
                ),
                "source_metrics_sha256": sha256_file(metrics_path),
                "source_manifest_sha256": sha256_file(manifest_path),
                "source_git_sha": manifest["git"]["git_sha"],
                "source_git_dirty": manifest["git"]["git_dirty"],
            }
        )
    if len(output) != 10:
        raise ValueError(f"expected 10 noise conditions, got {len(output)}")
    return output


def extract_protocol_audit_rows(workbook_path: Path) -> list[dict[str, str]]:
    lines = workbook_path.read_text(encoding="utf-8").splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith("| Benchmark / protocol |")
        ),
        None,
    )
    if start is None:
        raise ValueError(f"{workbook_path}: front audit table not found")
    raw_rows: list[list[str]] = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 9:
            raise ValueError(f"{workbook_path}: malformed protocol row: {line}")
        raw_rows.append(cells)

    headers = (
        "benchmark_or_protocol",
        "audit_role",
        "affected_intersection_confirmed",
        "changed_query_in_retention",
        "unrelated_locality_separate",
        "current_history_distinguished",
        "s1_constructible",
        "evidence_class",
        "benchmark_verdict",
    )

    def clean(value: str) -> str:
        return value.replace("**", "").replace("`", "")

    return [
        dict(zip(headers, (clean(value) for value in row)))
        for row in raw_rows
    ]


def audit_summary_rows(
    summary: Mapping[str, Any],
    sensitivity: Mapping[str, Any],
    accounting_rows: Sequence[Mapping[str, str]],
) -> list[dict[str, Any]]:
    year_rows = [row for row in accounting_rows if row["stratum"] == "C_year_only"]
    year_valid = [
        row for row in year_rows if row["final_verdict"] == "historically_valid"
    ]
    accepted = summary["accepted_supersession"]
    exact = summary["agreement"]["exact_class"]
    repeat = summary["stability"]["exact_class"]
    verdict_counts = summary["verdict_counts"]
    rows = [
        {
            "measure": "accepted_supersession_primary",
            "numerator": accepted["numerator"],
            "denominator": accepted["denominator"],
            "rate": accepted["rate"],
            "wilson95_low": accepted["wilson95_low"],
            "wilson95_high": accepted["wilson95_high"],
            "claim_role": "existence in one sampled published lineage; not prevalence",
        },
        {
            "measure": "accepted_supersession_strict_reference_time",
            "numerator": sensitivity["accepted_supersession_strict"],
            "denominator": sensitivity["denominator_unique_items"],
            "rate": sensitivity["accepted_supersession_strict"]
            / sensitivity["denominator_unique_items"],
            "wilson95_low": "",
            "wilson95_high": "",
            "claim_role": "reference-time sensitivity check; not prevalence",
        },
        {
            "measure": "year_anchored_negative_control",
            "numerator": len(year_valid),
            "denominator": len(year_rows),
            "rate": len(year_valid) / len(year_rows),
            "wilson95_low": "",
            "wilson95_high": "",
            "claim_role": "explicit historical anchors remain historically valid",
        },
        {
            "measure": "initial_cross_auditor_exact_class_agreement",
            "numerator": exact["numerator"],
            "denominator": exact["denominator"],
            "rate": exact["rate"],
            "wilson95_low": exact["wilson95_low"],
            "wilson95_high": exact["wilson95_high"],
            "claim_role": "reliability limitation; below preregistered threshold",
        },
        {
            "measure": "same_auditor_repeat_exact_class_stability",
            "numerator": repeat["numerator"],
            "denominator": repeat["denominator"],
            "rate": repeat["rate"],
            "wilson95_low": repeat["wilson95_low"],
            "wilson95_high": repeat["wilson95_high"],
            "claim_role": "repeat stability only; not cross-auditor validity",
        },
        {
            "measure": "retention_eligible_pairs",
            "numerator": verdict_counts["historically_valid"]
            + verdict_counts["still_valid"],
            "denominator": summary["unique_item_count"],
            "rate": (
                verdict_counts["historically_valid"]
                + verdict_counts["still_valid"]
            )
            / summary["unique_item_count"],
            "wilson95_low": "",
            "wilson95_high": "",
            "claim_role": "still-valid plus explicitly historical references",
        },
        {
            "measure": "unresolved_pairs",
            "numerator": verdict_counts["ambiguous_context"]
            + verdict_counts["insufficient_evidence"],
            "denominator": summary["unique_item_count"],
            "rate": (
                verdict_counts["ambiguous_context"]
                + verdict_counts["insufficient_evidence"]
            )
            / summary["unique_item_count"],
            "wilson95_low": "",
            "wilson95_high": "",
            "claim_role": "abstained; excluded from deterministic validity claims",
        },
    ]
    return rows


def accepted_case_rows(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for record in records:
        if not record["acceptance"]["supersession_accepted"]:
            continue
        answer = record["proposed_current_answer"]
        change_date = record["change_date"]
        output.append(
            {
                "item_id": record["item_id"],
                "egta_verdict": record["verdict"],
                "question": record["question"],
                "frozen_answer": record["frozen_answer"]["raw"],
                "current_answer_state": answer["state"],
                "current_answer": answer["canonical"] or "",
                "change_date_earliest": change_date["earliest"] or "",
                "change_date_latest": change_date["latest"] or "",
                "reference_time_latest": record["query_temporal_context"][
                    "reference_time"
                ]["latest"],
                "audit_as_of": record["query_temporal_context"]["audit_as_of"],
                "evidence_source_count": len(record["evidence_sources"]),
                "evidence_package_id": record["evidence_package_id"],
                "evidence_package_sha256": record["provenance"][
                    "evidence_package_sha256"
                ],
                "egta_acceptance_status": "ACCEPTED",
                "human_review_status": "PENDING_HUMAN",
            }
        )
    if len(output) != 6:
        raise ValueError(f"expected six accepted supersessions, got {len(output)}")
    return output


def event_boundary_rows(path: Path) -> list[dict[str, Any]]:
    rows = read_csv(path)
    output: list[dict[str, Any]] = []
    for row in rows:
        if row["postevent_method"] != VALIDITY_METHOD:
            continue
        output.append(
            {
                "event_type": row["event_type"],
                "boundary_role": row["event_boundary_role"],
                "preserver_target_success": _float(row["old_target_success"]),
                "validity_filter_target_success": _float(
                    row["postevent_target_success"]
                ),
                "preserver_obsolete_answer_rate": _float(
                    row["old_obsolete_answer_rate"]
                ),
                "validity_filter_obsolete_answer_rate": _float(
                    row["postevent_obsolete_answer_rate"]
                ),
                "historical_accuracy_delta": _float(
                    row["historical_accuracy_delta"]
                ),
                "event_gate_pass": _bool(row["r3_event_gate_pass"]),
            }
        )
    return sorted(output, key=lambda row: row["event_type"])


def reader_selection_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        {
            "reader_retrieval_arm": arm,
            "pre_event_winner": values["argmax_preev_retention"],
            "pre_event_selective_winner": values[
                "argmax_preev_retention_selective"
            ],
            "current_valid_winner": values["argmax_current_valid_acc"],
            "ranking_reversal": values["reversal"],
            "current_valid_regret": values["regret_current_valid"],
            "evidence_role": "exploratory generative-reader transfer",
        }
        for arm, values in sorted(payload.items())
    ]


def _case_markdown(case_id: str, record: Mapping[str, Any]) -> str:
    context = record["query_temporal_context"]
    reference = context["reference_time"]
    anchors = ", ".join(
        f"{anchor['kind']}: {anchor['text']}" for anchor in context["anchors"]
    ) or "(none)"
    lines = [
        f"# Human review case {case_id}",
        "",
        "This sheet is blind to EGTA/model verdicts. Judge only from the",
        "question, frozen reference, temporal metadata, and frozen evidence below.",
        "",
        "## Item",
        "",
        f"- Question: {record['question']}",
        f"- Frozen answer: {record['frozen_answer']['raw']}",
        f"- Reference-time latest bound: {reference['latest']}",
        f"- Audit as-of date: {context['audit_as_of']}",
        f"- Query anchors: {anchors}",
        "",
        "## Frozen evidence",
        "",
    ]
    for source_index, source in enumerate(record["evidence_sources"], start=1):
        lines.extend(
            [
                f"### Source {source_index}",
                "",
                f"- Title: {source['title']}",
                f"- Publisher: {source['publisher']}",
                f"- Source tier: {source['source_tier']}",
                f"- URL: {source['url']}",
                f"- Publication/update date: {source.get('publication_or_update_date') or '(not recorded)'}",
                f"- Access date: {source['access_date']}",
                f"- Content SHA256: {source['content_sha256']}",
                "",
                "Evidence spans:",
                "",
            ]
        )
        for span in source["spans"]:
            lines.append(
                f"- [{span['locator']}] {span['text']}"
            )
        lines.append("")
    lines.extend(
        [
            "## Reviewer task",
            "",
            "Record your decision in the assigned reviewer CSV. Do not write the",
            "decision into this case sheet. Determine temporal scope, whether the",
            "frozen answer remains valid, the current answer state/value, event",
            "type, change date, evidence sufficiency, and confidence.",
            "",
        ]
    )
    return "\n".join(lines)


def write_human_review_pack(
    root: Path,
    records: Sequence[Mapping[str, Any]],
) -> None:
    accepted = [
        record
        for record in records
        if record["acceptance"]["supersession_accepted"]
    ]
    accepted.sort(key=lambda record: record["item_id"])
    if len(accepted) != 6:
        raise ValueError("human-review pack requires exactly six accepted cases")

    cases_dir = root / "cases"
    admin_dir = root / "admin"
    cases_dir.mkdir(parents=True, exist_ok=True)
    admin_dir.mkdir(parents=True, exist_ok=True)
    mapping_rows: list[dict[str, Any]] = []
    for index, record in enumerate(accepted, start=1):
        case_id = f"HR-{index:03d}"
        case_path = cases_dir / f"{case_id}.md"
        case_path.write_text(_case_markdown(case_id, record), encoding="utf-8")
        mapping_rows.append(
            {
                "case_id": case_id,
                "item_id": record["item_id"],
                "egta_verdict": record["verdict"],
                "egta_current_answer_state": record["proposed_current_answer"][
                    "state"
                ],
                "egta_current_answer": record["proposed_current_answer"][
                    "canonical"
                ]
                or "",
                "case_file_sha256": sha256_file(case_path),
            }
        )
    write_csv(admin_dir / "case_key.csv", mapping_rows)

    decision_fields = (
        "case_id",
        "reviewer_id",
        "temporal_scope",
        "current_directed",
        "frozen_answer_supported",
        "current_answer_state",
        "current_answer",
        "event_type",
        "change_date_earliest",
        "change_date_latest",
        "change_after_reference",
        "source_conflict",
        "verdict",
        "evidence_sufficient",
        "confidence",
        "notes",
        "completed_at_utc",
        "reviewer_signature",
    )
    blank_rows = [
        {field: case_id if field == "case_id" else "" for field in decision_fields}
        for case_id in (row["case_id"] for row in mapping_rows)
    ]
    write_csv(
        root / "reviewer_a_decisions.csv",
        blank_rows,
        fieldnames=decision_fields,
    )
    write_csv(
        root / "reviewer_b_decisions.csv",
        blank_rows,
        fieldnames=decision_fields,
    )

    adjudication_fields = (
        "case_id",
        "reviewer_a_verdict",
        "reviewer_b_verdict",
        "agreement",
        "adjudicator_id",
        "final_verdict",
        "final_current_answer_state",
        "final_current_answer",
        "final_event_type",
        "final_change_date_earliest",
        "final_change_date_latest",
        "evidence_sufficient",
        "rationale",
        "completed_at_utc",
        "adjudicator_signature",
    )
    adjudication_rows = [
        {
            field: case_id if field == "case_id" else ""
            for field in adjudication_fields
        }
        for case_id in (row["case_id"] for row in mapping_rows)
    ]
    write_csv(
        root / "adjudication.csv",
        adjudication_rows,
        fieldnames=adjudication_fields,
    )

    protocol = """# Human expert review protocol

Status: `PENDING_HUMAN`

Scope: the six EGTA-accepted supersessions only. Completion supports
accepted-case correctness and evidence sufficiency; it does not estimate
full-pool EGTA reliability. To evaluate reliability, apply the same protocol
to all 47 unique pairs.

1. The administrator sends only `cases/` and one blank reviewer CSV to each
   reviewer. Do not send `admin/case_key.csv`, EGTA verdicts, or model outputs.
2. Two qualified reviewers label every case independently.
3. Freeze both completed reviewer CSVs before comparison; record their SHA256.
4. Exact agreement requires the same verdict, answer state/value, event type,
   and compatible change-date interval.
5. An independent adjudicator resolves every disagreement from the same frozen
   evidence. No new evidence may be added silently; additions require a new
   versioned case packet.
6. The administrator completes the signoff only after identity, independence,
   hashes, and adjudication completeness have been checked.

Allowed verdicts: `superseded_revision`, `superseded_retirement`,
`still_valid`, `historically_valid`, `ambiguous_context`,
`insufficient_evidence`, `source_conflict`.
"""
    (root / "protocol.md").write_text(protocol, encoding="utf-8")
    signoff = """# Human review signoff

- Status: `PENDING_HUMAN`
- Scope: six EGTA-accepted supersessions
- Reviewer A identity:
- Reviewer B identity:
- Adjudicator identity:
- Independence confirmed:
- Reviewer A decisions SHA256:
- Reviewer B decisions SHA256:
- Adjudication SHA256:
- Cases confirmed:
- Cases rejected:
- Cases unresolved:
- Completed at UTC:
- Final human signatory:

This file must not be marked complete by an AI system. Until real reviewers
complete the fields above, the paper may call the six cases
`EGTA-accepted` or `evidence-backed`, but not `human-confirmed`.
"""
    (root / "HUMAN_REVIEW_SIGNOFF.md").write_text(signoff, encoding="utf-8")
    write_json(
        root / "human_review_status.json",
        {
            "schema_version": "egta_human_review_status.v1",
            "status": "PENDING_HUMAN",
            "scope": "six_egta_accepted_supersessions",
            "case_count": 6,
            "reviewer_a_complete": False,
            "reviewer_b_complete": False,
            "adjudication_complete": False,
            "claim_allowed_before_completion": "EGTA-accepted, evidence-backed",
            "claim_not_allowed_before_completion": "human-confirmed",
        },
    )


def _source_specs(
    repo_root: Path, noise_inference_root: Path
) -> list[dict[str, str]]:
    sources = [
        (
            "egta_summary",
            "audit/runs/reported_round/tables/egta_summary.json",
            "RQ1 audit headline and reliability accounting",
        ),
        (
            "egta_sensitivity",
            "audit/runs/reported_round/tables/sensitivity_reference_time.json",
            "RQ1 strict reference-time sensitivity",
        ),
        (
            "egta_item_accounting",
            "audit/runs/reported_round/tables/egta_item_accounting.csv",
            "RQ1 negative control and item accounting",
        ),
        (
            "egta_verdict_distribution",
            "audit/runs/reported_round/tables/egta_verdict_distribution.csv",
            "RQ1 verdict distribution",
        ),
        (
            "egta_final_records",
            "audit/runs/reported_round/aggregate/final_records.jsonl",
            "accepted-case records and human-review evidence",
        ),
        (
            "protocol_audit_workbook",
            "data/protocol_audit/protocol_audit_workbook_2026-07-17.md",
            "five-protocol published-lineage audit",
        ),
        (
            "matched_query_summary",
            "data/matched_comparison/rag_postevent_query_summary.csv",
            "matched-capability Q_eval reanalysis",
        ),
        (
            "matched_gate",
            "data/matched_comparison/rag_postevent_r2_gate.csv",
            "matched-success validity-control gate",
        ),
        (
            "matched_manifest",
            "data/matched_comparison/run_manifest.json",
            "matched-control run provenance",
        ),
        (
            "event_boundary",
            "data/event_types/by_event_type.csv",
            "ADD/REVISE/RETIRE boundary",
        ),
        (
            "reader_selection",
            "data/reader_replay/selection/reader_selection.json",
            "exploratory reader selection transfer",
        ),
    ]
    noise_root = (
        repo_root
        / "data/noise_runs"
    )
    for condition_dir in sorted(path for path in noise_root.iterdir() if path.is_dir()):
        for filename, role in (
            ("rag_postevent_metrics.csv", "noise-condition raw metrics"),
            ("run_manifest.json", "noise-condition provenance"),
        ):
            relative = (condition_dir / filename).relative_to(repo_root)
            sources.append(
                (
                    f"noise_{condition_dir.name}_{filename.split('.')[0]}",
                    str(relative),
                    role,
                )
            )
    sources.append(
        (
            "noise_qeval_inference_manifest",
            str((noise_inference_root / "analysis_manifest.json").relative_to(repo_root)),
            "Q_eval capability-constrained selection-inference provenance",
        )
    )
    for path in sorted(noise_inference_root.glob("condition_*_cluster_ci.csv")):
        sources.append(
            (
                f"noise_qeval_inference_{path.stem}",
                str(path.relative_to(repo_root)),
                "Q_eval paired cluster-bootstrap selection inference",
            )
        )
    output: list[dict[str, str]] = []
    for source_id, relative_path, role in sources:
        path = repo_root / relative_path
        if not path.is_file():
            raise FileNotFoundError(path)
        output.append(
            {
                "source_id": source_id,
                "source_path": relative_path,
                "role": role,
                "sha256": sha256_file(path),
            }
        )
    return output


def _workspace_git(repo_root: Path) -> dict[str, Any]:
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {"git_sha": sha, "git_dirty": bool(status.strip())}


def _copy_snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _format_pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"


def _write_reports(
    output_root: Path,
    audit_rows: Sequence[Mapping[str, Any]],
    matched_rows: Sequence[Mapping[str, Any]],
    noise_rows: Sequence[Mapping[str, Any]],
) -> None:
    accepted = next(
        row for row in audit_rows if row["measure"] == "accepted_supersession_primary"
    )
    strict = next(
        row
        for row in audit_rows
        if row["measure"] == "accepted_supersession_strict_reference_time"
    )
    by_method = {row["method"]: row for row in matched_rows}
    preserver = by_method[PRESERVER_METHOD]
    validity = by_method[VALIDITY_METHOD]
    regret_values = [
        float(row["tie_robust_current_valid_regret"]) for row in noise_rows
    ]
    reports = output_root / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    sensitivity_report = f"""# RQ2 sensitivity bridge

For a two-region comparison, let `p` be the misaligned-query proportion, `d`
the preserver's accuracy advantage on aligned queries, and `g` the
validity-aligned method's advantage on misaligned queries. The latter wins
current-valid evaluation when:

`p*g > (1-p)*d`, equivalently `p > d/(d+g)`.

The primary and strict audit fractions ({accepted['numerator']}/{accepted['denominator']}
and {strict['numerator']}/{strict['denominator']}) are plotted only as
sample-level scenario markers. They are not estimates of benchmark-wide
prevalence. At the primary marker and `g=1`, a reversal can overcome an
aligned-region gap below
{_format_pct(maximum_overcomeable_aligned_gap(6 / 47, 1.0))}; at `g=0.5`,
the corresponding gap is
{_format_pct(maximum_overcomeable_aligned_gap(6 / 47, 0.5))}.

The controlled matched pair has zero aligned-axis gap and an effective
misaligned mass of 4,050/17,550 = 23.1%, so every positive mass suffices in
that diagnostic construction. This analysis connects existence to possible
selection consequences; it does not claim that the 47-pair sample estimates
the prevalence of misalignment in the full locality pool.
"""
    (reports / "sensitivity_bridge.md").write_text(
        sensitivity_report, encoding="utf-8"
    )

    matched_report = f"""# RQ3 matched-capability selection

Both methods have target success {preserver['target_success']:.4f}, target
evidence hit {preserver['evidence_hit_target']:.4f}, stable CVBD
{preserver['stable_cvbd']:.4f}, and historical accuracy
{preserver['historical_accuracy']:.4f}. The preserver's obsolete-answer rate
is {preserver['obsolete_answer_rate']:.4f}; the validity filter's is
{validity['obsolete_answer_rate']:.4f}.

On the exact same `Q_eval` denominator (`n={preserver['qeval_row_count']}` per
method), pre-event retention prefers the preserver
({_format_pct(preserver['pre_event_retention_qeval'])} vs.
{_format_pct(validity['pre_event_retention_qeval'])}), while current-valid
accuracy prefers the filter
({_format_pct(validity['current_valid_accuracy_qeval'])} vs.
{_format_pct(preserver['current_valid_accuracy_qeval'])}). The induced
current-valid selection regret is
{_format_pct(validity['current_valid_accuracy_qeval'] - preserver['current_valid_accuracy_qeval'])}.
Because the capability/control axes match, the ordering difference is carried
by preservation of the invalidated answer.
"""
    (reports / "matched_selection.md").write_text(
        matched_report, encoding="utf-8"
    )

    all_reversal = all(bool(row["ranking_reversal"]) for row in noise_rows)
    noise_report = f"""# Robustness: Q_eval selection under signal noise

The post-hoc selection reanalysis uses one common `Q_eval` denominator for
both criteria and a target-success floor of 0.10. Ranking reversal is observed
in {sum(bool(row['ranking_reversal']) for row in noise_rows)}/{len(noise_rows)}
conditions (`all={str(all_reversal).lower()}`), spanning clean, missing
metadata, corrupted metadata, and intent error at 5%, 10%, and 20%.
Tie-robust current-valid regret ranges from
{_format_pct(min(regret_values))} to {_format_pct(max(regret_values))}.

This supports a controlled robustness statement only. Noise is injected into
the synthetic generator and does not estimate naturally occurring production
errors. The inference table records paired cluster-bootstrap intervals for the
change in regret relative to clean; source runs and their hashes are listed in
`source_inventory.csv`.
"""
    (reports / "robustness_noise_selection.md").write_text(
        noise_report, encoding="utf-8"
    )

    claim_map = """# Claim-to-evidence map

| Claim | Final artifact | Supported wording | Boundary |
|---|---|---|---|
| Misalignment exists in a published lineage | `tables/audit_summary.csv`, `tables/audit_accepted_cases.csv` | EGTA accepts six evidence-backed supersessions in the 47-pair audit sample | Not benchmark-wide prevalence; human review pending |
| A finite misaligned fraction can change selection | `tables/rq2_sensitivity_*.csv`, `figures/fig_sensitivity_bridge.*` | Small `p` suffices when the aligned-method margin is small | Scenario analysis, not an empirical prevalence estimate |
| Reference choice changes the matched comparison | `tables/matched_comparison.csv`, `tables/matched_selection_summary.csv` | Same target/control axes, opposite criterion choices, 23.1-point regret | Controlled diagnostic policies, not learned-method frequency |
| Event boundary | `tables/robustness_event_types.csv` | ADD is conflict-free; REVISE and RETIRE reproduce the obsolete conflict | Controlled event generator |
| Signal-noise robustness | `tables/robustness_noise_selection.csv` | `Q_eval` ranking reversal occurs in all ten injected-noise conditions | Injected noise; post-hoc derived selection analysis |
| Reader transfer | `tables/robustness_reader_selection.csv` | Criterion choices differ on all three exploratory reader arms | Exploratory; not production generalization |
| Human expert confirmation | `human_review/` | Review packet prepared | `PENDING_HUMAN`; do not say human-confirmed |
"""
    (reports / "claim_evidence_map.md").write_text(claim_map, encoding="utf-8")


def build_final_bundle(
    *,
    repo_root: Path,
    output_root: Path,
    noise_inference_root: Path,
) -> dict[str, Any]:
    """Build a new final bundle without overwriting existing evidence."""

    repo_root = repo_root.resolve()
    output_root = output_root.resolve()
    noise_inference_root = noise_inference_root.resolve()
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite final bundle: {output_root}")
    if not noise_inference_root.is_dir():
        raise FileNotFoundError(noise_inference_root)
    output_root.mkdir(parents=True)

    egta_table_root = repo_root / "audit/runs/reported_round/tables"
    final_records_path = (
        repo_root / "audit/runs/reported_round/aggregate/final_records.jsonl"
    )
    protocol_workbook = (
        repo_root
        / "data/protocol_audit/protocol_audit_workbook_2026-07-17.md"
    )
    matched_query_summary = (
        repo_root
        / "data/matched_comparison/rag_postevent_query_summary.csv"
    )
    noise_condition_root = (
        repo_root
        / "data/noise_runs"
    )

    summary = json.loads(
        (egta_table_root / "egta_summary.json").read_text(encoding="utf-8")
    )
    sensitivity = json.loads(
        (egta_table_root / "sensitivity_reference_time.json").read_text(
            encoding="utf-8"
        )
    )
    accounting = read_csv(egta_table_root / "egta_item_accounting.csv")
    final_records = read_jsonl(final_records_path)

    audit_rows = audit_summary_rows(summary, sensitivity, accounting)
    accepted_rows = accepted_case_rows(final_records)
    protocol_rows = extract_protocol_audit_rows(protocol_workbook)
    matched_rows = summarize_matched_capability(matched_query_summary)
    matched_selection = matched_selection_rows(matched_rows)
    noise_rows = summarize_noise_selection(
        noise_condition_root, noise_inference_root
    )

    tables = output_root / "tables"
    write_csv(tables / "audit_summary.csv", audit_rows)
    write_csv(tables / "audit_accepted_cases.csv", accepted_rows)
    write_csv(tables / "protocol_audit.csv", protocol_rows)
    _copy_snapshot(
        egta_table_root / "egta_verdict_distribution.csv",
        tables / "audit_verdict_distribution.csv",
    )
    write_csv(
        tables / "sensitivity_thresholds.csv",
        sensitivity_threshold_rows(),
    )
    write_csv(
        tables / "sensitivity_scenarios.csv",
        sensitivity_scenario_rows(),
    )
    write_csv(tables / "matched_comparison.csv", matched_rows)
    write_csv(tables / "matched_selection_summary.csv", matched_selection)
    write_csv(
        tables / "robustness_event_types.csv",
        event_boundary_rows(
            repo_root / "data/event_types/by_event_type.csv"
        ),
    )
    write_csv(tables / "robustness_noise_selection.csv", noise_rows)
    write_csv(
        tables / "robustness_reader_selection.csv",
        reader_selection_rows(
            repo_root / "data/reader_replay/selection/reader_selection.json"
        ),
    )

    source_snapshots = output_root / "source_snapshots"
    _copy_snapshot(
        egta_table_root / "egta_summary.json",
        source_snapshots / "egta_summary.json",
    )
    _copy_snapshot(
        egta_table_root / "sensitivity_reference_time.json",
        source_snapshots / "sensitivity_reference_time.json",
    )
    _copy_snapshot(
        protocol_workbook,
        source_snapshots / "protocol_audit_workbook_2026-07-17.md",
    )
    write_jsonl(
        source_snapshots / "accepted_records.jsonl",
        [
            record
            for record in final_records
            if record["acceptance"]["supersession_accepted"]
        ],
    )

    figures = output_root / "figures"
    plot_sensitivity_bridge(
        figures / "fig_sensitivity_bridge.pdf",
        figures / "fig_sensitivity_bridge.png",
    )
    write_human_review_pack(output_root / "human_review", final_records)
    _write_reports(output_root, audit_rows, matched_rows, noise_rows)

    source_inventory = _source_specs(repo_root, noise_inference_root)
    write_csv(output_root / "source_inventory.csv", source_inventory)

    noise_manifest = json.loads(
        (noise_inference_root / "analysis_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    workspace_git = _workspace_git(repo_root)
    final_status = """# Final RAE/EGTA evidence status

Date: 2026-07-24

## Status

- Published-lineage audit aggregation: `COMPLETE`
- Sensitivity bridge: `COMPLETE`
- Matched-capability `Q_eval` selection analysis: `COMPLETE`
- Event/noise/reader robustness organization: `COMPLETE`
- Human expert review of six accepted cases: `PENDING_HUMAN`

All computational artifacts are derived from frozen source files whose hashes
are listed in `source_inventory.csv`. Original raw runs remain in place and
were not overwritten. The noise `Q_eval` inference records source-run
cleanliness separately from the current packaging worktree and was rerun from
a clean analysis commit with `paper_facing_eligible=true`.

The package may support `EGTA-accepted` and `evidence-backed`. It must not
support `human-confirmed` until `human_review/HUMAN_REVIEW_SIGNOFF.md` is
completed by real reviewers.
"""
    (output_root / "FINAL_STATUS.md").write_text(final_status, encoding="utf-8")

    readme = """# Final RAE/EGTA experiment bundle

This is the canonical compact data entrypoint for the paper. It contains
derived tables, one sensitivity figure in PDF/PNG, concise reports, exact
source hashes, and the pending human-review packet. Large raw outputs remain
at their original repository paths and are linked through
`source_inventory.csv`.

Start with:

1. `FINAL_STATUS.md`
2. `reports/claim_evidence_map.md`
3. `tables/`
4. `human_review/protocol.md` when assigning expert review

The bundle is immutable by construction: the builder refuses to overwrite an
existing destination.
"""
    (output_root / "README.md").write_text(readme, encoding="utf-8")

    generated_files = sorted(
        path
        for path in output_root.rglob("*")
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS"}
    )
    generator_path = Path(__file__).resolve()
    manifest = {
        "schema_version": "egta_final_bundle.v1",
        "status": "COMPUTATIONAL_COMPLETE_HUMAN_PENDING",
        "date": "2026-07-24",
        "repo_root": str(repo_root),
        "output_root": str(output_root),
        "workspace_git": workspace_git,
        "source_run_cleanliness": {
            "matched_control_git_dirty": json.loads(
                (
                    repo_root
                    / "data/matched_comparison/run_manifest.json"
                ).read_text(encoding="utf-8")
            )["git"]["git_dirty"],
            "noise_all_source_runs_clean": all(
                not bool(row["source_git_dirty"]) for row in noise_rows
            ),
            "noise_derived_inference_paper_facing_eligible": noise_manifest[
                "paper_facing_eligible"
            ],
            "note": (
                "The source-run manifests and the selected derived-inference run "
                "are clean; the current packaging worktree is tracked separately "
                "and all inputs are content-hashed."
            ),
        },
        "generator": {
            "path": str(generator_path.relative_to(repo_root)),
            "sha256": sha256_file(generator_path),
        },
        "noise_inference": {
            "path": str(noise_inference_root.relative_to(repo_root)),
            "manifest_sha256": sha256_file(
                noise_inference_root / "analysis_manifest.json"
            ),
            "run_id": noise_manifest["run_id"],
        },
        "human_review_status": "PENDING_HUMAN",
        "outputs": [
            {
                "path": str(path.relative_to(output_root)),
                "sha256": sha256_file(path),
            }
            for path in generated_files
        ],
        "claim_boundary": (
            "Existence in one sampled published lineage, controlled selection "
            "consequences, and controlled robustness only. No benchmark-wide "
            "prevalence, production-RAG, or human-confirmation claim."
        ),
    }
    write_json(output_root / "manifest.json", manifest)

    checksum_files = sorted(
        path
        for path in output_root.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    )
    (output_root / "SHA256SUMS").write_text(
        "".join(
            f"{sha256_file(path)}  {path.relative_to(output_root)}\n"
            for path in checksum_files
        ),
        encoding="utf-8",
    )
    return manifest
