#!/usr/bin/env python3
"""Cluster-bootstrap reader method selection and current-valid regret.

The full functional is bootstrapped: within every paired cluster resample, the
script first selects a method by the stated criterion and then evaluates the
current-valid regret of that selected method.  It only reanalyzes preserved
per-row reader outputs and never invokes a reader or retriever.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rae_egta.rag_post_event.cluster_inference import (  # noqa: E402
    CONFIDENCE_LEVEL,
    EVENT_TYPES,
    QUANTILE_METHOD,
    TIE_EPSILON,
    percentile_interval,
    resample_sum_count,
    sha256_file,
    write_csv,
)
from recompute_reader_ranking import CURRENT_TYPES, preev_agree  # noqa: E402


BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 20_260_721
BATCH_SIZE = 128
RNG_ALGORITHM = "MT19937 (Python random.Random)"
METRICS = ("preev_retention", "current_valid_acc")
EXPECTED_METHODS = (
    "intent_aware_postevent_filter",
    "no_update",
    "oldref_adapted_preserver",
    "postevent_validity_filter",
    "query_rewrite_update",
    "store_only",
)
REQUIRED_FIELDS = {
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


@dataclass
class ArmData:
    reader: str
    retriever: str
    input_path: Path
    input_sha256: str
    raw_row_count: int
    methods: tuple[str, ...]
    clusters: tuple[tuple[int, str], ...]
    event_types: np.ndarray
    seeds: np.ndarray
    sums: np.ndarray
    counts: np.ndarray


def load_arm(
    path: Path, reader: str, raw_reader_id: str, retriever: str
) -> ArmData:
    with path.open(newline="", encoding="utf-8") as handle:
        csv_reader = csv.DictReader(handle)
        missing = sorted(REQUIRED_FIELDS - set(csv_reader.fieldnames or ()))
        if missing:
            raise RuntimeError(f"{path}: missing fields {missing}")
        rows = [row for row in csv_reader if row["retriever"] == retriever]
    if not rows:
        raise RuntimeError(f"no rows for arm {reader}/{retriever}: {path}")
    if {row["reader_id"] for row in rows} != {raw_reader_id}:
        raise RuntimeError(f"reader_id mismatch for {reader}/{retriever}")
    if any(row["exploratory"].lower() != "true" for row in rows):
        raise RuntimeError(f"non-exploratory row found for {reader}/{retriever}")

    observed_methods = tuple(sorted({row["method"] for row in rows}))
    if observed_methods != EXPECTED_METHODS:
        raise RuntimeError(
            f"method set mismatch for {reader}/{retriever}: {observed_methods}"
        )

    seen_rows: set[tuple[int, str, str, str]] = set()
    cluster_types: dict[tuple[int, str], str] = {}
    query_types: dict[tuple[tuple[int, str], str], str] = {}
    query_methods: dict[tuple[tuple[int, str], str], set[str]] = defaultdict(set)
    cluster_method_counts: dict[tuple[int, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        seed = int(row["seed"])
        event_id = row["event_id"]
        event_type = row["event_type"]
        query_id = row["query_id"]
        method = row["method"]
        query_type = row["query_type"]
        if event_type not in EVENT_TYPES:
            raise RuntimeError(f"invalid event type: {event_type}")
        cluster = (seed, event_id)
        row_key = (seed, event_id, query_id, method)
        if row_key in seen_rows:
            raise RuntimeError(f"duplicate row key in {reader}/{retriever}: {row_key}")
        seen_rows.add(row_key)
        previous_event_type = cluster_types.setdefault(cluster, event_type)
        if previous_event_type != event_type:
            raise RuntimeError(f"cluster changes event type: {cluster}")
        query_key = (cluster, query_id)
        previous_query_type = query_types.setdefault(query_key, query_type)
        if previous_query_type != query_type:
            raise RuntimeError(f"query changes query type: {query_key}")
        query_methods[query_key].add(method)
        cluster_method_counts[cluster][method] += 1

    expected_method_set = set(EXPECTED_METHODS)
    for query_key, methods in query_methods.items():
        if methods != expected_method_set:
            raise RuntimeError(f"unpaired methods at query {query_key}: {methods}")
    for cluster, method_counts in cluster_method_counts.items():
        if set(method_counts) != expected_method_set or len(set(method_counts.values())) != 1:
            raise RuntimeError(
                f"unpaired cluster/method row counts at {cluster}: {method_counts}"
            )

    clusters = tuple(sorted(cluster_types))
    cluster_index = {cluster: index for index, cluster in enumerate(clusters)}
    method_index = {method: index for index, method in enumerate(EXPECTED_METHODS)}
    sums = np.zeros((len(clusters), len(EXPECTED_METHODS), len(METRICS)))
    counts = np.zeros_like(sums, dtype=np.int64)
    for row in rows:
        cidx = cluster_index[(int(row["seed"]), row["event_id"])]
        midx = method_index[row["method"]]
        preevent_value = preev_agree(row)
        sums[cidx, midx, 0] += preevent_value
        counts[cidx, midx, 0] += 1
        if row["query_type"] in CURRENT_TYPES:
            current_value = int(row["correct_canonical_contained"])
            sums[cidx, midx, 1] += current_value
            counts[cidx, midx, 1] += 1

    if np.any(counts.sum(axis=0) == 0):
        raise RuntimeError(f"empty metric support for {reader}/{retriever}")
    if any(len(set(counts[:, :, metric_index].sum(axis=0))) != 1 for metric_index in range(2)):
        raise RuntimeError(f"unpaired finite metric support for {reader}/{retriever}")

    return ArmData(
        reader=reader,
        retriever=retriever,
        input_path=path,
        input_sha256=sha256_file(path),
        raw_row_count=len(rows),
        methods=EXPECTED_METHODS,
        clusters=clusters,
        event_types=np.asarray([cluster_types[cluster] for cluster in clusters], dtype=object),
        seeds=np.asarray([cluster[0] for cluster in clusters], dtype=np.int64),
        sums=sums,
        counts=counts,
    )


def point_scores(data: ArmData) -> np.ndarray:
    numerator = data.sums.sum(axis=0)
    denominator = data.counts.sum(axis=0)
    return np.divide(
        numerator,
        denominator,
        out=np.full(numerator.shape, np.nan, dtype=np.float64),
        where=denominator > 0,
    )


def bootstrap_scores(data: ArmData) -> np.ndarray:
    output = np.full(
        (BOOTSTRAP_REPLICATES, len(data.methods), len(METRICS)),
        np.nan,
        dtype=np.float64,
    )
    rng = random.Random(BOOTSTRAP_SEED)
    cluster_count = len(data.clusters)
    for start in range(0, BOOTSTRAP_REPLICATES, BATCH_SIZE):
        stop = min(start + BATCH_SIZE, BOOTSTRAP_REPLICATES)
        batch = stop - start
        draws = np.fromiter(
            (rng.randrange(cluster_count) for _ in range(batch * cluster_count)),
            dtype=np.int64,
            count=batch * cluster_count,
        ).reshape(batch, cluster_count)
        numerator, denominator = resample_sum_count(
            data.sums, data.counts, draws
        )
        output[start:stop] = np.divide(
            numerator,
            denominator,
            out=np.full(numerator.shape, np.nan, dtype=np.float64),
            where=denominator > 0,
        )
    if not np.all(np.isfinite(output)):
        raise RuntimeError(f"non-finite bootstrap score in {data.reader}/{data.retriever}")
    return output


def analyze_arm(
    data: ArmData, expected_selection: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    point = point_scores(data)
    samples = bootstrap_scores(data)
    point_pre_index = int(np.argmax(point[:, 0]))
    point_current_index = int(np.argmax(point[:, 1]))
    point_regret = float(point[point_current_index, 1] - point[point_pre_index, 1])

    replicate_indices = np.arange(BOOTSTRAP_REPLICATES)
    pre_selected = np.argmax(samples[:, :, 0], axis=1)
    current_selected = np.argmax(samples[:, :, 1], axis=1)
    regret_samples = (
        samples[replicate_indices, current_selected, 1]
        - samples[replicate_indices, pre_selected, 1]
    )
    if np.any(regret_samples < -TIE_EPSILON):
        raise RuntimeError(f"negative selection regret in {data.reader}/{data.retriever}")
    regret_samples = np.maximum(regret_samples, 0.0)
    ci_low, ci_high, valid = percentile_interval(regret_samples)

    point_pre_method = data.methods[point_pre_index]
    point_current_method = data.methods[point_current_index]
    expected_regret = float(expected_selection["regret_current_valid"])
    consistency_pass = (
        point_pre_method == expected_selection["argmax_preev_retention"]
        and point_current_method == expected_selection["argmax_current_valid_acc"]
        and round(point_regret, 4) == expected_regret
    )
    if not consistency_pass:
        raise RuntimeError(
            f"reader_selection.json mismatch for {data.reader}/{data.retriever}: "
            f"{point_pre_method}, {point_current_method}, {point_regret}"
        )

    common = {
        "analysis": "reader_selection_inference",
        "reader": data.reader,
        "retriever": data.retriever,
        "arm": f"{data.reader}/{data.retriever}",
        "confidence_level": CONFIDENCE_LEVEL,
        "bootstrap_replicates": BOOTSTRAP_REPLICATES,
        "bootstrap_rng_seed": BOOTSTRAP_SEED,
        "rng_algorithm": RNG_ALGORITHM,
        "interval": "percentile",
        "quantile_method": QUANTILE_METHOD,
        "cluster_key": "seed,event_id",
        "strata": "none",
        "resampling_scheme": "ordinary_cluster_bootstrap",
        "paired_resampling": True,
        "event_cluster_count": len(data.clusters),
        "raw_row_count": data.raw_row_count,
        "tie_break_rule": "first_lexicographically_sorted_method",
        "tie_epsilon": TIE_EPSILON,
    }
    rows: list[dict[str, Any]] = [
        {
            **common,
            "record_type": "regret",
            "criterion": "preev_retention_to_current_valid_regret",
            "method": "",
            "point_selected": "",
            "point_criterion_estimate": "",
            "point_current_valid_accuracy": "",
            "point_selected_by_preevent": point_pre_method,
            "point_selected_by_current_valid": point_current_method,
            "regret_point_estimate": point_regret,
            "regret_ci_low": ci_low,
            "regret_ci_high": ci_high,
            "valid_replicates": valid,
            "selected_count": "",
            "selection_frequency": "",
            "argmax_set_membership_count": "",
            "argmax_set_membership_frequency": "",
            "criterion_tie_replicate_count": "",
            "criterion_tie_replicate_frequency": "",
            "reader_selection_json_consistency": "PASS",
        }
    ]

    for criterion_index, criterion in enumerate(METRICS):
        selected = pre_selected if criterion_index == 0 else current_selected
        criterion_samples = samples[:, :, criterion_index]
        maxima = criterion_samples.max(axis=1, keepdims=True)
        argmax_membership = (maxima - criterion_samples) <= TIE_EPSILON
        tied_replicates = argmax_membership.sum(axis=1) > 1
        point_selected_index = (
            point_pre_index if criterion_index == 0 else point_current_index
        )
        for method_index, method in enumerate(data.methods):
            selected_count = int(np.count_nonzero(selected == method_index))
            membership_count = int(
                np.count_nonzero(argmax_membership[:, method_index])
            )
            rows.append(
                {
                    **common,
                    "record_type": "selection_frequency",
                    "criterion": criterion,
                    "method": method,
                    "point_selected": method_index == point_selected_index,
                    "point_criterion_estimate": float(point[method_index, criterion_index]),
                    "point_current_valid_accuracy": float(point[method_index, 1]),
                    "point_selected_by_preevent": point_pre_method,
                    "point_selected_by_current_valid": point_current_method,
                    "regret_point_estimate": "",
                    "regret_ci_low": "",
                    "regret_ci_high": "",
                    "valid_replicates": BOOTSTRAP_REPLICATES,
                    "selected_count": selected_count,
                    "selection_frequency": selected_count / BOOTSTRAP_REPLICATES,
                    "argmax_set_membership_count": membership_count,
                    "argmax_set_membership_frequency": membership_count
                    / BOOTSTRAP_REPLICATES,
                    "criterion_tie_replicate_count": int(tied_replicates.sum()),
                    "criterion_tie_replicate_frequency": float(tied_replicates.mean()),
                    "reader_selection_json_consistency": "PASS",
                }
            )

    summary = {
        "reader": data.reader,
        "retriever": data.retriever,
        "point_selected_by_preevent": point_pre_method,
        "point_selected_by_current_valid": point_current_method,
        "regret_point_estimate": point_regret,
        "regret_ci_low": ci_low,
        "regret_ci_high": ci_high,
        "valid_replicates": valid,
        "consistency_pass": consistency_pass,
        "selection_rows": [row for row in rows if row["record_type"] == "selection_frequency"],
    }
    return rows, summary


def fmt(value: Any) -> str:
    return f"{float(value):.4f}"


def create_report(
    path: Path,
    *,
    args: argparse.Namespace,
    arms: Sequence[ArmData],
    summaries: Sequence[Mapping[str, Any]],
) -> None:
    lines = [
        "# Reader Selection-Regret Cluster Inference",
        "",
        "Status: `PASS`. CPU-only reanalysis of preserved exploratory reader rows; no reader, retriever, or experiment was run.",
        "",
        "## Command",
        "",
        "```bash",
        "python3 data/reader_replay/selection/recompute_reader_selection_inference.py",
        "```",
        "",
        "## Inputs",
        "",
    ]
    for arm in arms:
        lines.append(
            f"- `{arm.reader}/{arm.retriever}`: `{arm.input_path.relative_to(PROJECT_ROOT)}`, "
            f"SHA256 `{arm.input_sha256}`, {arm.raw_row_count:,} arm rows, {len(arm.clusters)} event clusters."
        )
    lines.extend(
        [
            f"- Existing point-selection reference: `{args.selection_reference.relative_to(PROJECT_ROOT)}` (all three arms exactly reproduced after four-decimal rounding).",
            "",
            "## Protocol",
            "",
            "- Functional per bootstrap replicate: compute all method scores, select by the criterion, then evaluate the selected method's current-valid regret against the current-valid argmax.",
            "- Resampling unit: complete `(seed, event_id)` cluster. Following `recompute_reader_ranking.py`, this is an ordinary unstratified cluster bootstrap over the arm's 120 clusters; draws are paired across all six methods and both criteria.",
            f"- Bootstrap: {BOOTSTRAP_REPLICATES:,} percentile replicates, arm-local Python `random.Random` seed `{BOOTSTRAP_SEED}`, 95% CI, NumPy quantile method `{QUANTILE_METHOD}`.",
            "- Criteria and row-level definitions are imported from `recompute_reader_ranking.py`: unconditional pre-event containment agreement and unconditional current-valid accuracy.",
            "- Tie-break: first method in lexicographic order, matching the existing recomputation. The CSV also reports argmax-set membership and any-tie frequency so structural ties remain visible.",
            "",
            "## Selection-regret inference",
            "",
            "| Arm | Pre-event choice | Current-valid choice | Regret point | 95% cluster CI | JSON check |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for summary in summaries:
        lines.append(
            f"| {summary['reader']}/{summary['retriever']} | "
            f"{summary['point_selected_by_preevent']} | {summary['point_selected_by_current_valid']} | "
            f"{fmt(summary['regret_point_estimate'])} | "
            f"[{fmt(summary['regret_ci_low'])}, {fmt(summary['regret_ci_high'])}] | "
            f"{'PASS' if summary['consistency_pass'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## Bootstrap selection stability",
            "",
            "`Selected` is the deterministic tie-broken frequency. `Argmax-set` is the frequency with which the method belongs to the maximizing tie set.",
            "",
            "| Arm | Criterion | Method | Selected | Argmax-set | Any-tie |",
            "|---|---|---|---:|---:|---:|",
        ]
    )
    for summary in summaries:
        for row in summary["selection_rows"]:
            lines.append(
                f"| {row['arm']} | {row['criterion']} | {row['method']} | "
                f"{fmt(row['selection_frequency'])} | "
                f"{fmt(row['argmax_set_membership_frequency'])} | "
                f"{fmt(row['criterion_tie_replicate_frequency'])} |"
            )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "This remains an exploratory bridge analysis. Pre-event agreement is a containment proxy rather than a same-reader S0 rerun, and the intervals are conditional on the preserved 120-event controlled sample, three seeds, and fixed reader/retriever arms.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    reader_root = (
        PROJECT_ROOT
        / "data/reader_replay"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qwen-metrics",
        type=Path,
        default=reader_root / "qwen_metrics/rag_document_llm_metrics.csv",
    )
    parser.add_argument(
        "--grok-metrics",
        type=Path,
        default=reader_root / "grok_metrics/rag_document_llm_metrics.csv",
    )
    parser.add_argument(
        "--selection-reference",
        type=Path,
        default=PROJECT_ROOT / "data/reader_replay/selection/reader_selection.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT
        / "data/reader_replay/selection/reader_selection_inference.csv",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT
        / "data/reader_replay/selection/reader_selection_inference_report.md",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    expected = json.loads(args.selection_reference.read_text(encoding="utf-8"))
    arm_specs = (
        (args.qwen_metrics, "qwen3p5_9b", "qwen3p5_9b", "bm25"),
        (args.qwen_metrics, "qwen3p5_9b", "qwen3p5_9b", "dense"),
        (args.grok_metrics, "grok4p5", "grok_4_5", "bm25"),
    )
    arms = [load_arm(*spec) for spec in arm_specs]
    output_rows: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for arm in arms:
        key = f"{arm.reader}/{arm.retriever}"
        rows, summary = analyze_arm(arm, expected[key])
        output_rows.extend(rows)
        summaries.append(summary)
    write_csv(args.output, output_rows)
    create_report(args.report, args=args, arms=arms, summaries=summaries)
    print(
        json.dumps(
            {
                "status": "PASS",
                "output": str(args.output),
                "report": str(args.report),
                "output_sha256": sha256_file(args.output),
                "regret": [
                    {
                        key: value
                        for key, value in summary.items()
                        if key != "selection_rows"
                    }
                    for summary in summaries
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
