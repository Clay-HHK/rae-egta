#!/usr/bin/env python3
"""Run controlled RAG post-event drift experiments.

This runner is designed for the T4 workflow. It uses a deterministic structured
QA substrate to validate the post-event reference schema before binding a real
RAG/LLM backend.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
from pathlib import Path
from typing import Any

from rae_egta.common.provenance import environment_metadata, git_metadata, stable_json_hash
from rae_egta.rag_post_event.data import (
    CURRENT_VALID_STABLE,
    HISTORICAL,
    NO_CURRENT,
    OBSOLETE_UNQUALIFIED,
    QUERY_TEMPLATE_VERSION,
    TARGET,
    KnowledgeEvent,
    Query,
    build_dataset,
    event_to_json,
    query_to_json,
    stable_rng,
)


ROOT = Path(__file__).resolve().parents[3]


DEFAULT_METHODS = ["store_only", "naive_update", "old_ref_reg_0p70", "postevent_ref_reg_0p85"]


CSV_FIELDS = [
    "run_id",
    "dataset",
    "seed",
    "event_id",
    "event_type",
    "query_id",
    "query_type",
    "method",
    "old_answer",
    "postevent_answer",
    "updated_answer",
    "target_success",
    "current_valid_accuracy",
    "obsolete_answer_rate",
    "old_reference_retention",
    "postevent_retention",
    "cvbd",
    "total_drift",
    "store_response_drift",
    "adaptation_side_effect",
]

CURRENT_VALID_TYPES = {TARGET, CURRENT_VALID_STABLE, OBSOLETE_UNQUALIFIED}


def enforce_t4_only() -> None:
    if platform.system() == "Darwin" and os.environ.get("ALLOW_LOCAL_RAG_POSTEVENT_RUN") != "1":
        raise SystemExit(
            "Refusing to run RAG post-event experiments on local macOS. "
            "Run formal experiments on a Linux server, e.g. through scripts/run_experiments.sh. "
            "Set ALLOW_LOCAL_RAG_POSTEVENT_RUN=1 only for deliberate local debugging."
        )


def parse_reg_strength(method: str, default: float) -> float:
    parts = method.rsplit("_", 1)
    if len(parts) != 2:
        return default
    raw = parts[1].replace("p", ".")
    try:
        value = float(raw)
    except ValueError:
        return default
    return min(1.0, max(0.0, value))


def exact_match(a: str, b: str) -> int:
    return int(a == b)


def maybe(rng: Any, probability: float, yes: str, no: str) -> str:
    return yes if rng.random() < probability else no


def spurious_answer(query: Query, method: str) -> str:
    return f"SPURIOUS_{method}_{query.event_id}_{query.query_type}"


def method_answer(method: str, query: Query, *, seed: int) -> str:
    rng = stable_rng("method_answer", method, seed, query.event_id, query.query_id)
    old = query.expected_old
    post = query.expected_postevent
    current = query.expected_current
    spurious = spurious_answer(query, method)

    if method == "no_update":
        return old
    if method == "store_only":
        return post
    if method == "naive_update":
        if query.query_type == TARGET:
            return maybe(rng, 0.90, current, old)
        if query.query_type == CURRENT_VALID_STABLE:
            return maybe(rng, 0.75, current, spurious)
        if query.query_type == OBSOLETE_UNQUALIFIED:
            return maybe(rng, 0.85, current, old)
        if query.query_type == HISTORICAL:
            return maybe(rng, 0.70, old, spurious)
        return maybe(rng, 0.80, current, spurious)
    if method == "replay_old":
        if query.query_type == TARGET:
            return maybe(rng, 0.25, current, old)
        if query.query_type == OBSOLETE_UNQUALIFIED:
            return maybe(rng, 0.10, current, old)
        return old
    if method.startswith("old_ref_reg"):
        strength = parse_reg_strength(method, 0.70)
        if query.query_type in {TARGET, OBSOLETE_UNQUALIFIED}:
            return maybe(rng, 1.0 - strength, current, old)
        if query.query_type == CURRENT_VALID_STABLE:
            return maybe(rng, 0.98, old, spurious)
        return old
    if method.startswith("postevent_ref_reg"):
        strength = parse_reg_strength(method, 0.85)
        if query.query_type in CURRENT_VALID_TYPES:
            fallback = old if old != post else spurious
            return maybe(rng, strength, post, fallback)
        if query.query_type == HISTORICAL:
            return maybe(rng, 0.95, post, spurious)
        return maybe(rng, strength, post, spurious)
    raise ValueError(f"Unknown method: {method}")


def row_for_query(
    *,
    run_id: str,
    seed: int,
    event: KnowledgeEvent,
    query: Query,
    method: str,
) -> dict[str, Any]:
    old_answer = query.expected_old
    postevent_answer = query.expected_postevent
    updated_answer = method_answer(method, query, seed=seed)
    current_valid = query.query_type in CURRENT_VALID_TYPES
    target = query.query_type == TARGET
    obsolete = query.query_type == OBSOLETE_UNQUALIFIED
    old_retention = exact_match(updated_answer, old_answer)
    postevent_retention = exact_match(updated_answer, postevent_answer)
    return {
        "run_id": run_id,
        "dataset": "controlled_structured_qa",
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
        "obsolete_answer_rate": exact_match(updated_answer, query.obsolete_answer) if obsolete else math.nan,
        "old_reference_retention": old_retention,
        "postevent_retention": postevent_retention,
        "cvbd": 1.0 - postevent_retention if query.query_type == CURRENT_VALID_STABLE else math.nan,
        "total_drift": 1.0 - exact_match(updated_answer, old_answer),
        "store_response_drift": 1.0 - exact_match(postevent_answer, old_answer),
        "adaptation_side_effect": 1.0 - postevent_retention,
    }


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    event_records: list[dict[str, Any]] = []
    for seed in seeds:
        events, queries = build_dataset(
            event_count=event_count,
            seed=seed,
            stable_queries_per_event=stable_queries_per_event,
            add_fraction=add_fraction,
            revise_fraction=revise_fraction,
            retire_fraction=retire_fraction,
        )
        events_by_id = {event.event_id: event for event in events}
        for event in events:
            record = event_to_json(event)
            record["seed"] = seed
            event_records.append(record)
        for query in queries:
            event = events_by_id[query.event_id]
            for method in methods:
                rows.append(row_for_query(run_id=run_id, seed=seed, event=event, query=query, method=method))
    return rows, event_records


def generate_query_records(
    *,
    event_count: int,
    seeds: list[int],
    stable_queries_per_event: int,
    add_fraction: float,
    revise_fraction: float,
    retire_fraction: float,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for seed in seeds:
        _, queries = build_dataset(
            event_count=event_count,
            seed=seed,
            stable_queries_per_event=stable_queries_per_event,
            add_fraction=add_fraction,
            revise_fraction=revise_fraction,
            retire_fraction=retire_fraction,
        )
        for query in queries:
            record = query_to_json(query)
            record["seed"] = seed
            records.append(record)
    return records


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


def write_manifest(args: argparse.Namespace, rows: list[dict[str, Any]], events: list[dict[str, Any]]) -> None:
    manifest = {
        "experiment": "rag_post_event_controlled_structured_qa",
        "query_template_version": QUERY_TEMPLATE_VERSION,
        "args": vars(args),
        "row_count": len(rows),
        "event_record_count": len(events),
        "schema_fields": CSV_FIELDS,
        "dataset_spec_hash": stable_json_hash(
            {
                "event_count": args.events,
                "seeds": args.seeds,
                "stable_queries_per_event": args.stable_queries_per_event,
                "add_fraction": args.add_fraction,
                "revise_fraction": args.revise_fraction,
                "retire_fraction": args.retire_fraction,
            }
        ),
        "methods": args.methods,
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
    parser.add_argument("--revise-fraction", type=float, default=0.45)
    parser.add_argument("--retire-fraction", type=float, default=0.45)
    parser.add_argument("--device", default="cuda")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    enforce_t4_only()
    if not args.run_id:
        args.run_id = args.output_dir.name
    rows, events = generate_rows(
        run_id=args.run_id,
        event_count=args.events,
        seeds=args.seeds,
        methods=args.methods,
        stable_queries_per_event=args.stable_queries_per_event,
        add_fraction=args.add_fraction,
        revise_fraction=args.revise_fraction,
        retire_fraction=args.retire_fraction,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "rag_postevent_metrics.csv", rows, CSV_FIELDS)
    write_csv(args.output_dir / "rag_postevent_metrics_partial.csv", rows, CSV_FIELDS)
    write_jsonl(args.output_dir / "events.jsonl", events)
    write_jsonl(
        args.output_dir / "queries.jsonl",
        generate_query_records(
            event_count=args.events,
            seeds=args.seeds,
            stable_queries_per_event=args.stable_queries_per_event,
            add_fraction=args.add_fraction,
            revise_fraction=args.revise_fraction,
            retire_fraction=args.retire_fraction,
        ),
    )
    write_manifest(args, rows, events)
    print(f"[done] RAG post-event metrics: {args.output_dir / 'rag_postevent_metrics.csv'}")
    print(f"[done] RAG post-event manifest: {args.output_dir / 'run_manifest.json'}")


if __name__ == "__main__":
    main()
