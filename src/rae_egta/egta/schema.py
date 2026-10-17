"""Canonical ``egta.v1`` record schema and blind-view contract."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from datetime import date, datetime
from enum import Enum
from typing import Any


SCHEMA_VERSION = "egta.v1"
PROTOCOL_VERSION = "egta_47pair_v1"


class Verdict(str, Enum):
    """The seven outcome classes in the EGTA ontology."""

    STILL_VALID = "still_valid"
    SUPERSEDED_REVISION = "superseded_revision"
    SUPERSEDED_RETIREMENT = "superseded_retirement"
    HISTORICALLY_VALID = "historically_valid"
    AMBIGUOUS_CONTEXT = "ambiguous_context"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SOURCE_CONFLICT = "source_conflict"


class ScreeningDisposition(str, Enum):
    """Candidate-screening outcomes, deliberately outside ``Verdict``."""

    TEMPORAL_CANDIDATE = "TEMPORAL_CANDIDATE"
    NOT_TIME_SENSITIVE = "NOT_TIME_SENSITIVE"


LEGACY_VERDICT_MAPPING: dict[str, tuple[Verdict, ...]] = {
    "SUPERSEDED_CONFIRMED": (Verdict.SUPERSEDED_REVISION, Verdict.SUPERSEDED_RETIREMENT),
    "STILL_VALID": (Verdict.STILL_VALID,),
    "YEAR_ANCHORED_VALID": (Verdict.HISTORICALLY_VALID,),
    "STALE_DEIXIS_YEAR_RESCUED": (Verdict.HISTORICALLY_VALID, Verdict.AMBIGUOUS_CONTEXT),
    "UNVERIFIED": (
        Verdict.INSUFFICIENT_EVIDENCE,
        Verdict.AMBIGUOUS_CONTEXT,
        Verdict.SOURCE_CONFLICT,
    ),
    "NOT_TIME_SENSITIVE": (),
}


_REFERENCE_TIME_SCHEMA = {
    "earliest": (str, type(None)),
    "latest": (str, type(None)),
    "precision": str,
    "provenance": str,
}
_CHANGE_DATE_SCHEMA = {
    "earliest": (str, type(None)),
    "latest": (str, type(None)),
    "precision": str,
}
_EVIDENCE_SOURCE_SCHEMA = {
    "evidence_id": str,
    "url": str,
    "canonical_url": str,
    "title": str,
    "publisher": str,
    "source_tier": str,
    "publication_or_update_date": (str, type(None)),
    "access_date": str,
    "content_sha256": str,
    "spans": [
        {
            "span_id": str,
            "text": str,
            "locator": str,
            "supports": [str],
        }
    ],
}


CANONICAL_RECORD_SCHEMA: dict[str, Any] = {
    "schema_version": str,
    "protocol_version": str,
    "item_id": str,
    "question": str,
    "frozen_answer": {"raw": str, "normalized": str},
    "query_temporal_context": {
        "scope": str,
        "direction": str,
        "anchors": [{"text": str, "kind": str}],
        "audit_as_of": str,
        "reference_time": _REFERENCE_TIME_SCHEMA,
    },
    "screening_disposition": str,
    "proposed_current_answer": {
        "state": str,
        "canonical": (str, type(None)),
        "aliases": [str],
        "valid_as_of": (str, type(None)),
    },
    "verdict": (str, type(None)),
    "event_type": str,
    "change_date": _CHANGE_DATE_SCHEMA,
    "evidence_package_id": str,
    "evidence_sources": [_EVIDENCE_SOURCE_SCHEMA],
    "auditor_agreement": {
        "initial_exact_class": (bool, type(None)),
        "temporal_scope_agreement": (bool, type(None)),
        "frozen_answer_agreement": (bool, type(None)),
        "current_answer_compatible": (bool, type(None)),
        "event_type_agreement": (bool, type(None)),
        "shared_supporting_source": (bool, type(None)),
        "source_jaccard": (float, int, type(None)),
    },
    "verification_status": str,
    "acceptance": {
        "source_supported": bool,
        "supersession_accepted": bool,
        "conditions": {
            "independent_decision_pair": bool,
            "same_supersession_class": bool,
            "frozen_answer_identified": bool,
            "current_answer_compatible": bool,
            "timestamped_external_evidence": bool,
            "change_after_reference": bool,
            "current_directed_query": bool,
            "no_unresolved_source_conflict": bool,
            "no_manual_override": bool,
        },
        "abstention_reason": (str, type(None)),
    },
    "legacy": {
        "primary_idx": int,
        "dup_idxs": [int],
        "stratum": str,
        "legacy_verdict": str,
        "legacy_ontology_candidates": [str],
        "legacy_current_answer": str,
        "legacy_changed_since_freeze": str,
        "legacy_stale_at_freeze": bool,
        "legacy_evidence_url": (str, type(None)),
        "legacy_note": str,
    },
    "provenance": {
        "input_sha256": str,
        "evidence_package_sha256": (str, type(None)),
        "prompt_sha256": (str, type(None)),
        "git_sha": (str, type(None)),
        "git_dirty": bool,
        "created_at_utc": str,
    },
}


BLIND_VIEW_SCHEMA: dict[str, Any] = {
    key: CANONICAL_RECORD_SCHEMA[key]
    for key in (
        "schema_version",
        "protocol_version",
        "item_id",
        "question",
        "frozen_answer",
        "query_temporal_context",
        "evidence_package_id",
        "evidence_sources",
    )
}


_QUERY_SCOPES = {"temporal", "non_temporal", "uncertain"}
_QUERY_DIRECTIONS = {"current", "historical", "ambiguous", "none"}
_ANCHOR_KINDS = {"deictic", "explicit_date", "explicit_year"}
_DATE_PRECISIONS = {"day", "month", "year", "interval", "unknown"}
_CURRENT_ANSWER_STATES = {"value", "absent_or_retired", "unresolved"}
_EVENT_TYPES = {"addition", "revision", "retirement", "none", "unknown"}
_SOURCE_TIERS = {
    "official_primary",
    "authoritative_database",
    "versioned_encyclopedia",
    "reliable_secondary",
    "other",
}
_VERIFICATION_STATUSES = {
    "not_triggered",
    "verifier_resolved",
    "verifier_abstained",
    "verifier_failed",
}
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ITEM_ID_RE = re.compile(r"^egta_[0-9a-f]{64}$")


def normalize_identity_text(value: str) -> str:
    """Normalize identity text without erasing semantically useful punctuation."""

    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return " ".join(normalized.split())


def make_item_id(question: str, frozen_answer: str) -> str:
    """Return the stable ID for a normalized question/answer pair."""

    identity = {
        "frozen_answer": normalize_identity_text(frozen_answer),
        "question": normalize_identity_text(question),
    }
    serialized = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "egta_" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _type_matches(value: Any, expected: type[Any] | tuple[type[Any], ...]) -> bool:
    expected_types = expected if isinstance(expected, tuple) else (expected,)
    if bool not in expected_types and int in expected_types and isinstance(value, bool):
        return False
    return isinstance(value, expected_types)


def _shape_errors(value: Any, schema: Any, path: str) -> list[str]:
    errors: list[str] = []
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            return [f"{path} must be an object"]
        missing = sorted(set(schema) - set(value))
        extra = sorted(set(value) - set(schema))
        errors.extend(f"{path}.{key} is required" for key in missing)
        errors.extend(f"{path}.{key} is not allowed" for key in extra)
        for key in sorted(set(schema) & set(value)):
            errors.extend(_shape_errors(value[key], schema[key], f"{path}.{key}"))
        return errors
    if isinstance(schema, list):
        if not isinstance(value, list):
            return [f"{path} must be an array"]
        item_schema = schema[0]
        for index, item in enumerate(value):
            errors.extend(_shape_errors(item, item_schema, f"{path}[{index}]"))
        return errors
    if not _type_matches(value, schema):
        names = schema if isinstance(schema, tuple) else (schema,)
        label = " or ".join(item.__name__ for item in names)
        errors.append(f"{path} must be {label}")
    return errors


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _validate_interval(payload: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    precision = payload["precision"]
    if precision not in _DATE_PRECISIONS:
        errors.append(f"{path}.precision has invalid value {precision!r}")
    for field in ("earliest", "latest"):
        value = payload[field]
        if value is not None and not _is_iso_date(value):
            errors.append(f"{path}.{field} must be an ISO date or null")
    earliest = payload["earliest"]
    latest = payload["latest"]
    if _is_iso_date(earliest) and _is_iso_date(latest) and earliest > latest:
        errors.append(f"{path}.earliest must not be after latest")
    if precision == "unknown" and (earliest is not None or latest is not None):
        errors.append(f"{path} with unknown precision must not contain date bounds")
    if precision != "unknown" and earliest is None and latest is None:
        errors.append(f"{path} with known precision requires a date bound")
    return errors


def _safe_semantic_checks(record: dict[str, Any]) -> list[str]:
    """Run semantic checks after the exact shape has already passed."""

    errors: list[str] = []
    if record["schema_version"] != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    if record["protocol_version"] != PROTOCOL_VERSION:
        errors.append(f"protocol_version must be {PROTOCOL_VERSION!r}")
    if not record["question"].strip():
        errors.append("question must be non-empty")
    frozen = record["frozen_answer"]
    if not frozen["raw"].strip():
        errors.append("frozen_answer.raw must be non-empty")
    if frozen["normalized"] != normalize_identity_text(frozen["raw"]):
        errors.append("frozen_answer.normalized does not match canonical normalization")
    if not _ITEM_ID_RE.fullmatch(record["item_id"]):
        errors.append("item_id must be egta_ followed by a lowercase SHA256")
    elif record["item_id"] != make_item_id(record["question"], frozen["raw"]):
        errors.append("item_id does not match normalized (question, frozen_answer)")

    temporal = record["query_temporal_context"]
    if temporal["scope"] not in _QUERY_SCOPES:
        errors.append("query_temporal_context.scope has an invalid value")
    if temporal["direction"] not in _QUERY_DIRECTIONS:
        errors.append("query_temporal_context.direction has an invalid value")
    if not _is_iso_date(temporal["audit_as_of"]):
        errors.append("query_temporal_context.audit_as_of must be an ISO date")
    for index, anchor in enumerate(temporal["anchors"]):
        if not anchor["text"].strip():
            errors.append(f"query_temporal_context.anchors[{index}].text must be non-empty")
        if anchor["kind"] not in _ANCHOR_KINDS:
            errors.append(f"query_temporal_context.anchors[{index}].kind has an invalid value")
    errors.extend(_validate_interval(temporal["reference_time"], "query_temporal_context.reference_time"))

    disposition = record["screening_disposition"]
    allowed_dispositions = {item.value for item in ScreeningDisposition}
    if disposition not in allowed_dispositions:
        errors.append("screening_disposition has an invalid value")
    if disposition == ScreeningDisposition.NOT_TIME_SENSITIVE.value:
        if temporal["scope"] != "non_temporal":
            errors.append("NOT_TIME_SENSITIVE requires scope=non_temporal")
        if record["verdict"] is not None:
            errors.append("NOT_TIME_SENSITIVE must not be encoded as a verdict")

    proposed = record["proposed_current_answer"]
    if proposed["state"] not in _CURRENT_ANSWER_STATES:
        errors.append("proposed_current_answer.state has an invalid value")
    if proposed["state"] == "value" and not proposed["canonical"]:
        errors.append("a value current answer requires proposed_current_answer.canonical")
    if proposed["state"] != "value" and proposed["canonical"] is not None:
        errors.append("non-value current answer must have canonical=null")
    if proposed["valid_as_of"] is not None and not _is_iso_date(proposed["valid_as_of"]):
        errors.append("proposed_current_answer.valid_as_of must be an ISO date or null")

    if record["verdict"] is not None and record["verdict"] not in {item.value for item in Verdict}:
        errors.append("verdict is not one of the seven EGTA ontology values")
    if record["event_type"] not in _EVENT_TYPES:
        errors.append("event_type has an invalid value")
    errors.extend(_validate_interval(record["change_date"], "change_date"))

    evidence_ids: list[str] = []
    for index, source in enumerate(record["evidence_sources"]):
        prefix = f"evidence_sources[{index}]"
        evidence_ids.append(source["evidence_id"])
        for field in ("evidence_id", "url", "canonical_url", "title", "publisher", "access_date"):
            if not source[field].strip():
                errors.append(f"{prefix}.{field} must be non-empty")
        if source["source_tier"] not in _SOURCE_TIERS:
            errors.append(f"{prefix}.source_tier has an invalid value")
        if not _is_iso_date(source["access_date"]):
            errors.append(f"{prefix}.access_date must be an ISO date")
        publication_date = source["publication_or_update_date"]
        if publication_date is not None and not _is_iso_date(publication_date):
            errors.append(f"{prefix}.publication_or_update_date must be an ISO date or null")
        if not _HASH_RE.fullmatch(source["content_sha256"]):
            errors.append(f"{prefix}.content_sha256 must be a lowercase SHA256")
        span_ids: list[str] = []
        for span_index, span in enumerate(source["spans"]):
            span_ids.append(span["span_id"])
            if not span["span_id"].strip() or not span["text"].strip():
                errors.append(f"{prefix}.spans[{span_index}] requires non-empty span_id and text")
        if len(span_ids) != len(set(span_ids)):
            errors.append(f"{prefix}.spans contains duplicate span_id values")
    if len(evidence_ids) != len(set(evidence_ids)):
        errors.append("evidence_sources contains duplicate evidence_id values")

    jaccard = record["auditor_agreement"]["source_jaccard"]
    if jaccard is not None and not 0.0 <= float(jaccard) <= 1.0:
        errors.append("auditor_agreement.source_jaccard must be in [0, 1]")
    if record["verification_status"] not in _VERIFICATION_STATUSES:
        errors.append("verification_status has an invalid value")

    acceptance = record["acceptance"]
    if acceptance["supersession_accepted"]:
        if record["verdict"] not in {
            Verdict.SUPERSEDED_REVISION.value,
            Verdict.SUPERSEDED_RETIREMENT.value,
        }:
            errors.append("supersession_accepted requires a supersession verdict")
        if not acceptance["source_supported"] or not all(acceptance["conditions"].values()):
            errors.append("supersession_accepted requires source support and every condition")

    legacy = record["legacy"]
    expected_candidates = LEGACY_VERDICT_MAPPING.get(legacy["legacy_verdict"])
    if expected_candidates is None:
        errors.append("legacy.legacy_verdict is unknown")
    elif legacy["legacy_ontology_candidates"] != [item.value for item in expected_candidates]:
        errors.append("legacy.legacy_ontology_candidates does not match the registered mapping")
    if legacy["primary_idx"] not in legacy["dup_idxs"]:
        errors.append("legacy.primary_idx must be present in legacy.dup_idxs")
    if len(legacy["dup_idxs"]) != len(set(legacy["dup_idxs"])):
        errors.append("legacy.dup_idxs must not contain duplicates")

    provenance = record["provenance"]
    if not _HASH_RE.fullmatch(provenance["input_sha256"]):
        errors.append("provenance.input_sha256 must be a lowercase SHA256")
    for field in ("evidence_package_sha256", "prompt_sha256"):
        value = provenance[field]
        if value is not None and not _HASH_RE.fullmatch(value):
            errors.append(f"provenance.{field} must be a lowercase SHA256 or null")
    try:
        datetime.fromisoformat(provenance["created_at_utc"].replace("Z", "+00:00"))
    except ValueError:
        errors.append("provenance.created_at_utc must be an ISO timestamp")
    return errors


def validate_record(record: dict[str, Any]) -> list[str]:
    """Return all detected ``egta.v1`` violations; an empty list is valid."""

    errors = _shape_errors(record, CANONICAL_RECORD_SCHEMA, "record")
    if errors:
        return errors
    return _safe_semantic_checks(record)


def _walk_keys(value: Any, path: str = "view") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            found.append((child_path, str(key)))
            found.extend(_walk_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_keys(child, f"{path}[{index}]"))
    return found


def assert_blind(view: dict[str, Any]) -> None:
    """Assert that an auditor/verifier view contains only blind-safe fields."""

    errors = _shape_errors(view, BLIND_VIEW_SCHEMA, "view")
    forbidden_fragments = {
        "acceptance",
        "agreement",
        "auditor",
        "current_answer",
        "disagreement",
        "disposition",
        "legacy",
        "note",
        "verdict",
        "verification",
        "verifier",
    }
    for path, key in _walk_keys(view):
        normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
        if any(fragment in normalized for fragment in forbidden_fragments):
            errors.append(f"{path} is forbidden in a blind view")
    if not errors:
        if view["schema_version"] != SCHEMA_VERSION or view["protocol_version"] != PROTOCOL_VERSION:
            errors.append("blind view schema/protocol version mismatch")
        if view["item_id"] != make_item_id(view["question"], view["frozen_answer"]["raw"]):
            errors.append("blind view item_id does not match its question/answer")
        if view["frozen_answer"]["normalized"] != normalize_identity_text(view["frozen_answer"]["raw"]):
            errors.append("blind view frozen answer normalization mismatch")
    if errors:
        raise AssertionError("blind-view contract violated: " + "; ".join(errors))


def to_blind_view(record: dict[str, Any]) -> dict[str, Any]:
    """Project a canonical record to the exact auditor-visible allowlist."""

    errors = validate_record(record)
    if errors:
        raise ValueError("cannot blind an invalid canonical record: " + "; ".join(errors))
    view = {key: copy.deepcopy(record[key]) for key in BLIND_VIEW_SCHEMA}
    # Scope/direction in migrated records may encode a legacy screening prior.
    # Auditors receive only deterministic anchors and reference-time metadata.
    view["query_temporal_context"]["scope"] = "uncertain"
    view["query_temporal_context"]["direction"] = "ambiguous"
    assert_blind(view)
    return view
