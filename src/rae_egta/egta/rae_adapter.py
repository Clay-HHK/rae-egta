"""Map final EGTA records into the RAE item and row-index partitions."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Sequence

from rae_egta.egta.evidence import sha256_path
from rae_egta.egta.runner import load_jsonl, update_run_manifest_near, write_json, write_jsonl
from rae_egta.egta.schema import ScreeningDisposition, Verdict, validate_record


def _partition_record(record: dict[str, Any]) -> dict[str, Any]:
    item_id = record["item_id"]
    verdict = record["verdict"]
    accepted = record["acceptance"]["supersession_accepted"]
    source_supported = record["acceptance"]["source_supported"]
    if record["screening_disposition"] == ScreeningDisposition.NOT_TIME_SENSITIVE.value:
        return {
            "item_id": item_id,
            "partition": "non_temporal_pass_through",
            "q_mis": False,
            "retention_eligibility": "temporal_pass_through",
            "target_answer": {"state": "unresolved", "canonical": None},
            "claim_boundary": "not_a_comprehensive_factual_validation",
        }
    if accepted and verdict == Verdict.SUPERSEDED_REVISION.value:
        return {
            "item_id": item_id,
            "partition": "q_mis",
            "q_mis": True,
            "retention_eligibility": "ineligible",
            "target_answer": {
                "state": "value",
                "canonical": record["proposed_current_answer"]["canonical"],
            },
            "claim_boundary": None,
        }
    if accepted and verdict == Verdict.SUPERSEDED_RETIREMENT.value:
        return {
            "item_id": item_id,
            "partition": "q_mis",
            "q_mis": True,
            "retention_eligibility": "ineligible",
            "target_answer": {"state": "absent_or_retired", "canonical": None},
            "claim_boundary": None,
        }
    if source_supported and verdict == Verdict.STILL_VALID.value:
        canonical = record["proposed_current_answer"]["canonical"] or record["frozen_answer"]["raw"]
        return {
            "item_id": item_id,
            "partition": "retention_eligible",
            "q_mis": False,
            "retention_eligibility": "eligible",
            "target_answer": {"state": "value", "canonical": canonical},
            "claim_boundary": None,
        }
    if source_supported and verdict == Verdict.HISTORICALLY_VALID.value:
        return {
            "item_id": item_id,
            "partition": "retention_eligible_historical",
            "q_mis": False,
            "retention_eligibility": "eligible_for_explicit_historical_query",
            "target_answer": {"state": "value", "canonical": record["frozen_answer"]["raw"]},
            "claim_boundary": None,
        }
    if verdict not in {
        Verdict.AMBIGUOUS_CONTEXT.value,
        Verdict.INSUFFICIENT_EVIDENCE.value,
        Verdict.SOURCE_CONFLICT.value,
    }:
        raise ValueError(f"record {item_id} has no conservative RAE mapping: {verdict!r}")
    return {
        "item_id": item_id,
        "partition": "unresolved",
        "q_mis": None,
        "retention_eligibility": "unresolved_excluded",
        "target_answer": {"state": "unresolved", "canonical": None},
        "claim_boundary": verdict,
    }


def adapt_records(*, records_path: Path, output_dir: Path) -> dict[str, Any]:
    """Write the seven §1.10 artifacts without expanding duplicate rows."""

    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to reuse non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    records = load_jsonl(records_path)
    item_ids = [record["item_id"] for record in records]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("final records contain duplicate item_id values")
    for record in records:
        errors = validate_record(record)
        if errors:
            raise ValueError(f"invalid final record {record.get('item_id')}: {errors}")
    partitions = [_partition_record(record) for record in records]
    by_id = {row["item_id"]: row for row in partitions}

    q_mis = sorted(row["item_id"] for row in partitions if row["partition"] == "q_mis")
    retention = sorted(
        row["item_id"]
        for row in partitions
        if row["partition"] in {"retention_eligible", "retention_eligible_historical"}
    )
    current_valid = sorted(
        row["item_id"]
        for row in partitions
        if row["partition"] in {"q_mis", "retention_eligible"}
    )
    unresolved = sorted(row["item_id"] for row in partitions if row["partition"] == "unresolved")
    non_temporal = sorted(
        row["item_id"] for row in partitions if row["partition"] == "non_temporal_pass_through"
    )
    named = {
        "q_mis_item_ids.json": q_mis,
        "retention_eligible_item_ids.json": retention,
        "current_valid_item_ids.json": current_valid,
        "unresolved_item_ids.json": unresolved,
        "non_temporal_pass_through_item_ids.json": non_temporal,
    }
    for filename, values in named.items():
        write_json(output_dir / filename, values)
    write_jsonl(output_dir / "rae_partition.jsonl", sorted(partitions, key=lambda row: row["item_id"]))

    row_map: list[dict[str, Any]] = []
    seen_row_idxs: set[int] = set()
    for record in records:
        primary = record["legacy"]["primary_idx"]
        for row_idx in record["legacy"]["dup_idxs"]:
            if row_idx in seen_row_idxs:
                raise ValueError(f"legacy row_idx {row_idx} maps to more than one unique item")
            seen_row_idxs.add(row_idx)
            row_map.append(
                {
                    "row_idx": row_idx,
                    "item_id": record["item_id"],
                    "is_primary": row_idx == primary,
                    "partition": by_id[record["item_id"]]["partition"],
                }
            )
    with (output_dir / "row_idx_to_item_id.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["row_idx", "item_id", "is_primary", "partition"],
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(sorted(row_map, key=lambda row: row["row_idx"]))
    product_hashes = {
        path.name: sha256_path(path)
        for path in sorted(output_dir.iterdir())
        if path.is_file()
    }
    analysis_manifest_path = output_dir.parent / "analysis_manifest.json"
    if analysis_manifest_path.is_file():
        analysis = json.loads(analysis_manifest_path.read_text(encoding="utf-8"))
        analysis["rae_adapter"] = {
            "records_sha256": sha256_path(records_path),
            "products": product_hashes,
        }
        write_json(analysis_manifest_path, analysis)
    update_run_manifest_near(
        output_dir,
        section="adapter",
        key="rae",
        payload={
            "records_sha256": sha256_path(records_path),
            "unique_item_count": len(records),
            "products": product_hashes,
        },
    )
    return {
        "unique_item_count": len(records),
        "row_mapping_count": len(row_map),
        "q_mis_count": len(q_mis),
        "retention_eligible_count": len(retention),
        "current_valid_count": len(current_valid),
        "unresolved_count": len(unresolved),
        "non_temporal_pass_through_count": len(non_temporal),
        "artifact_count": 7,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = adapt_records(records_path=args.records, output_dir=args.output_dir)
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
