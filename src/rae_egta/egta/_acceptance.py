"""Pure EGTA decision-pair and conservative-acceptance rules."""

from __future__ import annotations

import unicodedata
from typing import Any, Iterable

from rae_egta.egta.schema import Verdict
from rae_egta.egta.verifier import trigger_reasons


ABSTENTION_CLASSES = {
    Verdict.AMBIGUOUS_CONTEXT.value,
    Verdict.INSUFFICIENT_EVIDENCE.value,
    Verdict.SOURCE_CONFLICT.value,
}
SUPERSESSION_CLASSES = {
    Verdict.SUPERSEDED_REVISION.value,
    Verdict.SUPERSEDED_RETIREMENT.value,
}


def normalize_answer(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    while normalized and (normalized[0].isspace() or unicodedata.category(normalized[0]).startswith("P")):
        normalized = normalized[1:]
    while normalized and (normalized[-1].isspace() or unicodedata.category(normalized[-1]).startswith("P")):
        normalized = normalized[:-1]
    return " ".join(normalized.split())


def _answer_parts(value: Any) -> tuple[str, str | None]:
    if isinstance(value, str):
        return "value", value
    if not isinstance(value, dict):
        return "invalid", None
    state = str(value.get("state", "invalid"))
    canonical = value.get("canonical")
    return state, canonical if isinstance(canonical, str) else None


def compatible_answer(x: Any, y: Any, alias_groups: Iterable[Iterable[str]] = ()) -> bool:
    """Compare answers using only aliases frozen in the evidence package."""

    x_state, x_value = _answer_parts(x)
    y_state, y_value = _answer_parts(y)
    if x_state != y_state or x_state in {"invalid", "unresolved"}:
        return False
    if x_state == "absent_or_retired":
        return True
    if x_value is None or y_value is None:
        return False
    x_norm, y_norm = normalize_answer(x_value), normalize_answer(y_value)
    if not x_norm or not y_norm:
        return False
    if x_norm == y_norm:
        return True
    groups = [
        {normalize_answer(value) for value in group if isinstance(value, str) and normalize_answer(value)}
        for group in alias_groups
    ]
    x_expanded, y_expanded = {x_norm}, {y_norm}
    for group in groups:
        if x_norm in group:
            x_expanded.update(group)
        if y_norm in group:
            y_expanded.update(group)
    return bool(x_expanded & y_expanded)


def _valid_result(result: dict[str, Any] | None) -> bool:
    if not result:
        return False
    status = result.get("status", {})
    return bool(
        status.get("terminal_status") == "ok"
        and status.get("evidence_contract_valid", True)
        and status.get("judgment_schema_valid")
        and status.get("zero_tool_calls")
    )


def _core_match(left: dict[str, Any], right: dict[str, Any], aliases: list[list[str]]) -> bool:
    return bool(
        left["temporal_scope"] == right["temporal_scope"]
        and left["verdict"] == right["verdict"]
        and left["event_type"] == right["event_type"]
        and compatible_answer(left["proposed_current_answer"], right["proposed_current_answer"], aliases)
    )


def choose_decision_pair(
    auditor_a: dict[str, Any],
    auditor_b: dict[str, Any],
    verifier: dict[str, Any] | None,
    *,
    alias_groups: list[list[str]],
) -> tuple[list[dict[str, Any]] | None, str]:
    valid_a, valid_b = _valid_result(auditor_a), _valid_result(auditor_b)
    if not valid_a and not valid_b:
        return None, "invalid_auditor_judgment"
    if valid_a and valid_b and auditor_a.get("evidence_package_sha256") != auditor_b.get("evidence_package_sha256"):
        return None, "evidence_snapshot_mismatch"
    if valid_a and valid_b and auditor_a.get("blind_request_sha256") != auditor_b.get("blind_request_sha256"):
        return None, "blind_request_snapshot_mismatch"
    if valid_a and valid_b and auditor_a.get("prompt_template_sha256") != auditor_b.get("prompt_template_sha256"):
        return None, "prompt_snapshot_mismatch"
    if valid_a and valid_b and not trigger_reasons(auditor_a, auditor_b):
        return [auditor_a["judgment"], auditor_b["judgment"]], "auditor_pair"
    if not _valid_result(verifier):
        return None, "verifier_missing_or_invalid"
    v = verifier["judgment"]
    if v["verdict"] in ABSTENTION_CLASSES or v["verdict"] is None:
        return None, "verifier_abstained"
    matches = [
        result
        for result in (auditor_a, auditor_b)
        if _valid_result(result) and _core_match(v, result["judgment"], alias_groups)
    ]
    if len(matches) != 1:
        return None, "verifier_not_exactly_one_match"
    matched = matches[0]
    if (
        verifier.get("evidence_package_sha256") is not None
        and matched.get("evidence_package_sha256") != verifier.get("evidence_package_sha256")
    ):
        return None, "verifier_evidence_snapshot_mismatch"
    if (
        verifier.get("blind_request_sha256") is not None
        and matched.get("blind_request_sha256") != verifier.get("blind_request_sha256")
    ):
        return None, "verifier_blind_request_mismatch"
    return [matched["judgment"], v], "auditor_verifier_pair"


def _valid_source(source: dict[str, Any]) -> bool:
    return bool(source.get("url") and source.get("title") and source.get("access_date") and source.get("spans"))


def _citation_support(decision: dict[str, Any], sources: list[dict[str, Any]]) -> tuple[bool, bool]:
    source_map = {source["evidence_id"]: source for source in sources}
    cited_ids = decision["supporting_evidence_ids"]
    if not cited_ids or any(evidence_id not in source_map for evidence_id in cited_ids):
        return False, False
    cited = [source_map[evidence_id] for evidence_id in cited_ids]
    valid = all(_valid_source(source) for source in cited)
    supports_change = any("change_date" in span["supports"] for source in cited for span in source["spans"])
    return valid, supports_change


def _strictly_after_reference(decision: dict[str, Any], reference: dict[str, Any]) -> bool:
    earliest, latest = decision["change_date"].get("earliest"), decision["change_date"].get("latest")
    ref_earliest, ref_latest = reference.get("earliest"), reference.get("latest")
    # The condition needs only the reference upper bound: the change must
    # start strictly after the latest possible reference time. A null
    # ref_earliest (unknown collection start) must not veto acceptance.
    if ref_earliest and ref_latest and ref_earliest > ref_latest:
        return False
    return bool(
        earliest and latest and ref_latest
        and earliest <= latest and earliest > ref_latest
    )


def acceptance_conditions(
    pair: list[dict[str, Any]] | None,
    *,
    reference_time: dict[str, Any],
    sources: list[dict[str, Any]],
    alias_groups: list[list[str]],
) -> dict[str, bool]:
    if pair is None:
        return {key: False for key in (
            "independent_decision_pair", "same_supersession_class", "frozen_answer_identified",
            "current_answer_compatible", "timestamped_external_evidence", "change_after_reference",
            "current_directed_query", "no_unresolved_source_conflict", "no_manual_override",
        )}
    left, right = pair
    left_source, left_change = _citation_support(left, sources)
    right_source, right_change = _citation_support(right, sources)
    return {
        "independent_decision_pair": True,
        "same_supersession_class": left["verdict"] == right["verdict"] and left["verdict"] in SUPERSESSION_CLASSES,
        "frozen_answer_identified": bool(left["frozen_answer_identified"] and right["frozen_answer_identified"]),
        "current_answer_compatible": compatible_answer(
            left["proposed_current_answer"], right["proposed_current_answer"], alias_groups
        ),
        "timestamped_external_evidence": bool(left_source and right_source and left_change and right_change),
        "change_after_reference": _strictly_after_reference(left, reference_time) and _strictly_after_reference(right, reference_time),
        "current_directed_query": (
            left["temporal_scope"] == right["temporal_scope"] == "temporal"
            and left["direction"] == right["direction"] == "current"
        ),
        "no_unresolved_source_conflict": not (left["credible_source_conflict"] or right["credible_source_conflict"]),
        "no_manual_override": not (left["manual_override"] or right["manual_override"]),
    }


def abstention_reason(
    pair: list[dict[str, Any]] | None,
    *,
    pair_reason: str,
    conditions: dict[str, bool],
    auditor_judgments: Iterable[dict[str, Any]],
) -> str:
    """Apply source_conflict > ambiguous_context > insufficient_evidence."""

    judgments = list(auditor_judgments)
    if any(
        decision.get("credible_source_conflict") or decision.get("verdict") == Verdict.SOURCE_CONFLICT.value
        for decision in judgments
    ):
        return Verdict.SOURCE_CONFLICT.value
    ambiguity = any(
        decision.get("verdict") == Verdict.AMBIGUOUS_CONTEXT.value
        or decision.get("temporal_scope") == "uncertain"
        or decision.get("direction") == "ambiguous"
        for decision in judgments
    )
    scopes = {decision.get("temporal_scope") for decision in judgments}
    directions = {decision.get("direction") for decision in judgments}
    if ambiguity or len(scopes) > 1 or len(directions) > 1:
        return Verdict.AMBIGUOUS_CONTEXT.value
    return Verdict.INSUFFICIENT_EVIDENCE.value
