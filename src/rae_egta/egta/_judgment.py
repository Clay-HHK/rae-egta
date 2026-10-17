"""Structured output schema shared by all EGTA model backends."""

from __future__ import annotations

import copy
import re
from calendar import monthrange
from datetime import date
from typing import Any


JUDGMENT_SCHEMA_VERSION = "egta_judgment.v1"
_YEAR_RE = re.compile(r"^(\d{4})$")
_YEAR_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
AUDIT_JUDGMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "schema_version", "item_id", "temporal_scope", "direction", "verdict",
        "event_type", "frozen_answer_identified", "proposed_current_answer",
        "change_date", "supporting_evidence_ids", "credible_source_conflict",
        "manual_override", "rationale",
    ],
    "properties": {
        "schema_version": {"type": "string", "enum": [JUDGMENT_SCHEMA_VERSION]},
        "item_id": {"type": "string"},
        "temporal_scope": {"type": "string", "enum": ["temporal", "non_temporal", "uncertain"]},
        "direction": {"type": "string", "enum": ["current", "historical", "ambiguous", "none"]},
        "verdict": {"type": ["string", "null"], "enum": [
            "still_valid", "superseded_revision", "superseded_retirement",
            "historically_valid", "ambiguous_context", "insufficient_evidence",
            "source_conflict", None,
        ]},
        "event_type": {
            "type": "string",
            "enum": ["addition", "revision", "retirement", "none", "unknown"],
        },
        "frozen_answer_identified": {"type": "boolean"},
        "proposed_current_answer": {
            "type": "object",
            "additionalProperties": False,
            "required": ["state", "canonical"],
            "properties": {
                "state": {"type": "string", "enum": ["value", "absent_or_retired", "unresolved"]},
                "canonical": {"type": ["string", "null"]},
            },
        },
        "change_date": {
            "type": "object",
            "additionalProperties": False,
            "required": ["earliest", "latest", "precision"],
            "properties": {
                "earliest": {"type": ["string", "null"]},
                "latest": {"type": ["string", "null"]},
                "precision": {"type": "string", "enum": ["day", "month", "year", "interval", "unknown"]},
            },
        },
        "supporting_evidence_ids": {"type": "array", "items": {"type": "string"}},
        "credible_source_conflict": {"type": "boolean"},
        "manual_override": {"type": "boolean"},
        "rationale": {"type": "string"},
    },
}


def _normalize_date_bound(value: Any, *, endpoint: str) -> Any:
    if not isinstance(value, str):
        return value
    year_match = _YEAR_RE.fullmatch(value)
    year_month_match = _YEAR_MONTH_RE.fullmatch(value)
    try:
        if year_match:
            year = int(year_match.group(1))
            return date(year, 1, 1).isoformat() if endpoint == "earliest" else date(year, 12, 31).isoformat()
        if year_month_match:
            year, month = (int(part) for part in year_month_match.groups())
            day = 1 if endpoint == "earliest" else monthrange(year, month)[1]
            return date(year, month, day).isoformat()
    except ValueError:
        # Leave malformed lower-precision values untouched so schema validation rejects them.
        return value
    return value


def normalize_audit_judgment_dates(payload: Any) -> tuple[Any, dict[str, Any] | None]:
    """Expand year/month bounds without mutating the raw parsed judgment."""

    normalized = copy.deepcopy(payload)
    if not isinstance(normalized, dict) or not isinstance(normalized.get("change_date"), dict):
        return normalized, None
    change = normalized["change_date"]
    raw = copy.deepcopy(change)
    for endpoint in ("earliest", "latest"):
        if endpoint in change:
            change[endpoint] = _normalize_date_bound(change[endpoint], endpoint=endpoint)
    return normalized, {
        "applied": change != raw,
        "raw": raw,
        "normalized": copy.deepcopy(change),
    }


def validate_audit_judgment(payload: Any, *, expected_item_id: str | None = None) -> list[str]:
    if not isinstance(payload, dict):
        return ["judgment must be an object"]
    required = set(AUDIT_JUDGMENT_SCHEMA["required"])
    errors = [f"{key} is required" for key in sorted(required - set(payload))]
    errors.extend(f"{key} is not allowed" for key in sorted(set(payload) - required))
    if errors:
        return errors
    if payload["schema_version"] != JUDGMENT_SCHEMA_VERSION:
        errors.append("schema_version is invalid")
    if not isinstance(payload["item_id"], str) or not payload["item_id"]:
        errors.append("item_id must be a non-empty string")
    if expected_item_id is not None and payload["item_id"] != expected_item_id:
        errors.append("item_id does not match the request")
    if payload["temporal_scope"] not in {"temporal", "non_temporal", "uncertain"}:
        errors.append("temporal_scope is invalid")
    if payload["direction"] not in {"current", "historical", "ambiguous", "none"}:
        errors.append("direction is invalid")
    if payload["verdict"] not in set(AUDIT_JUDGMENT_SCHEMA["properties"]["verdict"]["enum"]):
        errors.append("verdict is invalid")
    if payload["event_type"] not in {"addition", "revision", "retirement", "none", "unknown"}:
        errors.append("event_type is invalid")
    if payload["verdict"] == "superseded_revision" and payload["event_type"] != "revision":
        errors.append("superseded_revision requires event_type=revision")
    if payload["verdict"] == "superseded_retirement" and payload["event_type"] != "retirement":
        errors.append("superseded_retirement requires event_type=retirement")
    for key in ("frozen_answer_identified", "credible_source_conflict", "manual_override"):
        if not isinstance(payload[key], bool):
            errors.append(f"{key} must be boolean")
    answer = payload["proposed_current_answer"]
    if not isinstance(answer, dict) or set(answer) != {"state", "canonical"}:
        errors.append("proposed_current_answer has an invalid shape")
    else:
        if answer["state"] not in {"value", "absent_or_retired", "unresolved"}:
            errors.append("proposed_current_answer.state is invalid")
        if answer["state"] == "value" and not isinstance(answer["canonical"], str):
            errors.append("value answer requires a string canonical value")
        if answer["state"] != "value" and answer["canonical"] is not None:
            errors.append("non-value answer requires canonical=null")
    change = payload["change_date"]
    if not isinstance(change, dict) or set(change) != {"earliest", "latest", "precision"}:
        errors.append("change_date has an invalid shape")
    else:
        if change["precision"] not in {"day", "month", "year", "interval", "unknown"}:
            errors.append("change_date.precision is invalid")
        for key in ("earliest", "latest"):
            if change[key] is not None:
                try:
                    date.fromisoformat(change[key])
                except (TypeError, ValueError):
                    errors.append(f"change_date.{key} must be an ISO date or null")
        if change["precision"] == "unknown" and (change["earliest"] is not None or change["latest"] is not None):
            errors.append("unknown change_date cannot have bounds")
        if change["precision"] != "unknown" and change["earliest"] is None and change["latest"] is None:
            errors.append("known change_date requires a date bound")
        if change["earliest"] and change["latest"] and change["earliest"] > change["latest"]:
            errors.append("change_date.earliest must not be after latest")
    ids = payload["supporting_evidence_ids"]
    if not isinstance(ids, list) or not all(isinstance(value, str) and value for value in ids):
        errors.append("supporting_evidence_ids must be a string array")
    elif len(ids) != len(set(ids)):
        errors.append("supporting_evidence_ids must not contain duplicates")
    if not isinstance(payload["rationale"], str):
        errors.append("rationale must be a string")
    if payload["temporal_scope"] == "non_temporal" and payload["verdict"] is not None:
        errors.append("non_temporal judgment must use verdict=null")
    return errors
