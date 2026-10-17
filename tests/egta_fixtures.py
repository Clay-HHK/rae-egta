from __future__ import annotations

import copy
from typing import Any

from rae_egta.egta.backends import JUDGMENT_SCHEMA_VERSION
from rae_egta.egta.migrate import migrate_legacy_record


def judgment(item_id: str, **changes: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": JUDGMENT_SCHEMA_VERSION,
        "item_id": item_id,
        "temporal_scope": "temporal",
        "direction": "current",
        "verdict": "insufficient_evidence",
        "event_type": "unknown",
        "frozen_answer_identified": True,
        "proposed_current_answer": {"state": "unresolved", "canonical": None},
        "change_date": {"earliest": None, "latest": None, "precision": "unknown"},
        "supporting_evidence_ids": [],
        "credible_source_conflict": False,
        "manual_override": False,
        "rationale": "fixture",
    }
    payload.update(copy.deepcopy(changes))
    return payload


def result(payload: dict[str, Any], *, valid: bool = True, aliases: list[list[str]] | None = None) -> dict[str, Any]:
    status = {
        "terminal_status": "ok" if valid else "parse_failure",
        "evidence_contract_valid": True,
        "judgment_schema_valid": valid,
        "zero_tool_calls": True,
    }
    return {
        "item_id": payload["item_id"],
        "judgment": copy.deepcopy(payload) if valid else None,
        "status": status,
        "backend_result": {
            "backend": "mock",
            "resolved_model": "mock-model",
            "backend_version": "mock-v1",
            "parse_failure": not valid,
            "tool_call_count": 0,
        },
        "evidence_package_sha256": "a" * 64,
        "evidence_aliases": aliases or [],
        "prompt_template_sha256": "b" * 64,
    }


def source(evidence_id: str = "src-1", *, text: str = "The value changed on 2025-01-01.") -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "url": "https://example.test/source",
        "canonical_url": "https://example.test/source",
        "title": "Official source",
        "publisher": "Example",
        "source_tier": "official_primary",
        "publication_or_update_date": "2025-01-01",
        "access_date": "2026-07-21",
        "content_sha256": "0" * 64,
        "spans": [
            {
                "span_id": f"{evidence_id}-span-1",
                "text": text,
                "locator": "paragraph 1",
                "supports": ["current_answer", "change_date"],
            }
        ],
    }


def migrated_record(
    idx: int,
    *,
    question: str | None = None,
    frozen_answer: str = "Old Answer",
    dup_idxs: list[int] | None = None,
) -> dict[str, Any]:
    row = {
        "idx": idx,
        "dup_idxs": dup_idxs or [idx],
        "stratum": "A_deictic_pure",
        "loc": question or f"nq question: who is the current holder {idx}",
        "loc_ans": frozen_answer,
        "verdict": "UNVERIFIED",
        "current_answer_2026": "legacy hidden",
        "changed_since_freeze": "unknown",
        "stale_at_freeze": False,
        "evidence_url": "-",
        "note": "legacy hidden note",
    }
    return migrate_legacy_record(
        row,
        input_sha256="1" * 64,
        git={"git_sha": "2" * 40, "git_dirty": False},
        created_at_utc="2026-07-22T00:00:00+00:00",
    )
