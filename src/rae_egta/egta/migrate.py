#!/usr/bin/env python3
"""Migrate the fixed 47 legacy annotations into canonical and blind EGTA inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from rae_egta.common.provenance import git_metadata
from rae_egta.egta.schema import (
    LEGACY_VERDICT_MAPPING,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    ScreeningDisposition,
    make_item_id,
    normalize_identity_text,
    to_blind_view,
    validate_record,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ANNOTATIONS = (
    REPO_ROOT
    / "data/audit_input"
    / "annotations_2026-07-21.json"
)
MIRROR_ANNOTATIONS = REPO_ROOT / "data/audit_input/mirror/annotations_2026-07-21.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "audit/runs"
AUDIT_AS_OF = "2026-07-21"
EXPECTED_RECORD_COUNT = 47
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_EXPLICIT_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_DEICTIC_PATTERNS = (
    re.compile(r"\bright now\b", re.IGNORECASE),
    re.compile(r"\bcurrently\b", re.IGNORECASE),
    re.compile(r"\bcurrent\b", re.IGNORECASE),
    re.compile(r"\blatest\b", re.IGNORECASE),
    re.compile(r"\bmost recent\b", re.IGNORECASE),
    re.compile(r"\bmost current\b", re.IGNORECASE),
    re.compile(r"\blast year's\b", re.IGNORECASE),
    re.compile(r"\blast year\b", re.IGNORECASE),
    re.compile(r"\bnow\b", re.IGNORECASE),
    re.compile(r"\bnew\b", re.IGNORECASE),
)
_LEGACY_FIELDS = {
    "idx",
    "dup_idxs",
    "stratum",
    "loc",
    "loc_ans",
    "verdict",
    "current_answer_2026",
    "changed_since_freeze",
    "stale_at_freeze",
    "evidence_url",
    "note",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_run_id(run_id: str) -> None:
    if not _RUN_ID_RE.fullmatch(run_id) or run_id in {".", ".."}:
        raise ValueError("run_id must be one safe path segment containing letters, digits, '.', '_', or '-'")


def _extract_anchors(question: str) -> list[dict[str, str]]:
    matches: list[tuple[int, int, str, str]] = []
    for match in _EXPLICIT_YEAR_RE.finditer(question):
        matches.append((match.start(), match.end(), match.group(0), "explicit_year"))
    for pattern in _DEICTIC_PATTERNS:
        for match in pattern.finditer(question):
            matches.append((match.start(), match.end(), match.group(0), "deictic"))
    # Prefer longer matches at a shared start and drop overlapping subphrases.
    selected: list[tuple[int, int, str, str]] = []
    for candidate in sorted(matches, key=lambda item: (item[0], -(item[1] - item[0]), item[3])):
        if any(candidate[0] < existing[1] and existing[0] < candidate[1] for existing in selected):
            continue
        selected.append(candidate)
    return [
        {"text": text, "kind": kind}
        for _, _, text, kind in sorted(selected, key=lambda item: (item[0], item[1]))
    ]


def _reference_time(anchors: list[dict[str, str]]) -> dict[str, str | None]:
    years = sorted({anchor["text"] for anchor in anchors if anchor["kind"] == "explicit_year"})
    if len(years) == 1:
        year = years[0]
        return {
            "earliest": f"{year}-01-01",
            "latest": f"{year}-12-31",
            "precision": "year",
            "provenance": "deterministic explicit-year anchor in the legacy question",
        }
    return {
        "earliest": None,
        "latest": None,
        "precision": "unknown",
        "provenance": "pending reference-time evidence backfill",
    }


def migrate_legacy_record(
    row: dict[str, Any],
    *,
    input_sha256: str,
    git: dict[str, Any],
    created_at_utc: str,
) -> dict[str, Any]:
    """Convert one legacy row without promoting its verdict into EGTA output."""

    missing = sorted(_LEGACY_FIELDS - set(row))
    extra = sorted(set(row) - _LEGACY_FIELDS)
    if missing or extra:
        raise ValueError(f"legacy row field mismatch: missing={missing}, extra={extra}")
    legacy_verdict = row["verdict"]
    if legacy_verdict not in LEGACY_VERDICT_MAPPING:
        raise ValueError(f"unknown legacy verdict: {legacy_verdict!r}")
    if not isinstance(row["idx"], int) or isinstance(row["idx"], bool):
        raise ValueError("legacy idx must be an integer")
    if not isinstance(row["dup_idxs"], list) or not all(
        isinstance(value, int) and not isinstance(value, bool) for value in row["dup_idxs"]
    ):
        raise ValueError("legacy dup_idxs must be an integer array")
    question = str(row["loc"])
    frozen_answer = str(row["loc_ans"])
    anchors = _extract_anchors(question)
    not_time_sensitive = legacy_verdict == ScreeningDisposition.NOT_TIME_SENSITIVE.value
    evidence_url = None if row["evidence_url"] == "-" else str(row["evidence_url"])
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "protocol_version": PROTOCOL_VERSION,
        "item_id": make_item_id(question, frozen_answer),
        "question": question,
        "frozen_answer": {
            "raw": frozen_answer,
            "normalized": normalize_identity_text(frozen_answer),
        },
        "query_temporal_context": {
            "scope": "non_temporal" if not_time_sensitive else "uncertain",
            "direction": "none" if not_time_sensitive else "ambiguous",
            "anchors": anchors,
            "audit_as_of": AUDIT_AS_OF,
            "reference_time": _reference_time(anchors),
        },
        "screening_disposition": (
            ScreeningDisposition.NOT_TIME_SENSITIVE.value
            if not_time_sensitive
            else ScreeningDisposition.TEMPORAL_CANDIDATE.value
        ),
        "proposed_current_answer": {
            "state": "unresolved",
            "canonical": None,
            "aliases": [],
            "valid_as_of": None,
        },
        "verdict": None,
        "event_type": "unknown",
        "change_date": {"earliest": None, "latest": None, "precision": "unknown"},
        "evidence_package_id": "",
        "evidence_sources": [],
        "auditor_agreement": {
            "initial_exact_class": None,
            "temporal_scope_agreement": None,
            "frozen_answer_agreement": None,
            "current_answer_compatible": None,
            "event_type_agreement": None,
            "shared_supporting_source": None,
            "source_jaccard": None,
        },
        "verification_status": "not_triggered",
        "acceptance": {
            "source_supported": False,
            "supersession_accepted": False,
            "conditions": {
                "independent_decision_pair": False,
                "same_supersession_class": False,
                "frozen_answer_identified": False,
                "current_answer_compatible": False,
                "timestamped_external_evidence": False,
                "change_after_reference": False,
                "current_directed_query": False,
                "no_unresolved_source_conflict": False,
                "no_manual_override": True,
            },
            "abstention_reason": "not_evaluated",
        },
        "legacy": {
            "primary_idx": row["idx"],
            "dup_idxs": list(row["dup_idxs"]),
            "stratum": str(row["stratum"]),
            "legacy_verdict": legacy_verdict,
            "legacy_ontology_candidates": [item.value for item in LEGACY_VERDICT_MAPPING[legacy_verdict]],
            "legacy_current_answer": str(row["current_answer_2026"]),
            "legacy_changed_since_freeze": str(row["changed_since_freeze"]),
            "legacy_stale_at_freeze": row["stale_at_freeze"],
            "legacy_evidence_url": evidence_url,
            "legacy_note": str(row["note"]),
        },
        "provenance": {
            "input_sha256": input_sha256,
            "evidence_package_sha256": None,
            "prompt_sha256": None,
            "git_sha": git.get("git_sha"),
            "git_dirty": bool(git.get("git_dirty", True)),
            "created_at_utc": created_at_utc,
        },
    }
    errors = validate_record(record)
    if errors:
        raise ValueError(f"migrated record {record['item_id']} is invalid: {errors}")
    return record


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def migrate_annotations(
    *,
    run_id: str,
    source_path: Path = SOURCE_ANNOTATIONS,
    mirror_path: Path = MIRROR_ANNOTATIONS,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    git: dict[str, Any] | None = None,
    created_at_utc: str | None = None,
) -> dict[str, Any]:
    """Migrate all 47 rows after proving source and mirror bytes are identical."""

    _validate_run_id(run_id)
    if not source_path.is_file() or not mirror_path.is_file():
        raise FileNotFoundError(f"legacy annotation pair is incomplete: {source_path}, {mirror_path}")
    # Both hashes are computed before the source of truth is parsed.
    input_hash = _sha256(source_path)
    mirror_hash = _sha256(mirror_path)
    if input_hash != mirror_hash:
        raise RuntimeError(
            "legacy annotation copies differ; refusing migration: "
            f"source={input_hash}, mirror={mirror_hash}"
        )
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or len(payload) != EXPECTED_RECORD_COUNT:
        observed = len(payload) if isinstance(payload, list) else type(payload).__name__
        raise ValueError(f"expected {EXPECTED_RECORD_COUNT} legacy rows, found {observed}")

    git_info = git_metadata() if git is None else dict(git)
    created_at = created_at_utc or datetime.now(timezone.utc).isoformat()
    records = [
        migrate_legacy_record(
            row,
            input_sha256=input_hash,
            git=git_info,
            created_at_utc=created_at,
        )
        for row in payload
    ]
    item_ids = [record["item_id"] for record in records]
    primary_idxs = [record["legacy"]["primary_idx"] for record in records]
    if len(set(item_ids)) != EXPECTED_RECORD_COUNT:
        raise ValueError("normalized (question, frozen_answer) pairs are not 47 unique items")
    if len(set(primary_idxs)) != EXPECTED_RECORD_COUNT:
        raise ValueError("legacy primary idx values are not unique")
    blind = [to_blind_view(record) for record in records]

    run_root = output_root / run_id
    if run_root.exists():
        raise FileExistsError(f"Refusing to overwrite output directory: {run_root}")
    input_dir = run_root / "input"
    input_dir.mkdir(parents=True)
    canonical_path = input_dir / "canonical_records.jsonl"
    blind_path = input_dir / "blind_candidates.jsonl"
    _write_jsonl(canonical_path, records)
    _write_jsonl(blind_path, blind)

    manifest: dict[str, Any] = {
        "schema_version": "egta_migration_manifest.v1",
        "protocol_version": PROTOCOL_VERSION,
        "run_id": run_id,
        "created_at_utc": created_at,
        "source_of_truth": str(source_path),
        "mirror_path": str(mirror_path),
        "input_sha256": input_hash,
        "mirror_sha256": mirror_hash,
        "source_mirror_hash_match": True,
        "input_record_count": len(payload),
        "canonical_record_count": len(records),
        "blind_candidate_count": len(blind),
        "unique_item_count": len(set(item_ids)),
        "canonical_records_sha256": _sha256(canonical_path),
        "blind_candidates_sha256": _sha256(blind_path),
        "git_sha": git_info.get("git_sha"),
        "git_dirty": bool(git_info.get("git_dirty", True)),
        "git_status_short": git_info.get("git_status_short", ""),
    }
    (run_root / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True, help="One new directory name under audit/runs")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = migrate_annotations(run_id=args.run_id)
    print(json.dumps(manifest, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
