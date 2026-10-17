"""Deterministic EGTA aggregation and §1.9 statistical-product entrypoint."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Sequence

from rae_egta.egta._acceptance import (
    ABSTENTION_CLASSES,
    _citation_support,
    _valid_result,
    acceptance_conditions,
    abstention_reason,
    choose_decision_pair,
    compatible_answer,
    normalize_answer,
)
from rae_egta.egta._provenance import git_metadata_ignoring_egta
from rae_egta.egta.evidence import load_evidence_for_item, sha256_path
from rae_egta.egta.runner import load_jsonl, update_run_manifest_near, write_jsonl
from rae_egta.egta.schema import ScreeningDisposition, Verdict, validate_record
from rae_egta.egta.verifier import load_results, trigger_reasons


def _agreement(a: dict[str, Any], b: dict[str, Any], aliases: list[list[str]]) -> dict[str, Any]:
    if not _valid_result(a) or not _valid_result(b):
        return {key: None for key in (
            "initial_exact_class", "temporal_scope_agreement", "frozen_answer_agreement",
            "current_answer_compatible", "event_type_agreement", "shared_supporting_source",
            "source_jaccard",
        )}
    left, right = a["judgment"], b["judgment"]
    left_ids, right_ids = set(left["supporting_evidence_ids"]), set(right["supporting_evidence_ids"])
    union = left_ids | right_ids
    return {
        "initial_exact_class": left["verdict"] == right["verdict"],
        "temporal_scope_agreement": left["temporal_scope"] == right["temporal_scope"],
        "frozen_answer_agreement": left["frozen_answer_identified"] == right["frozen_answer_identified"],
        "current_answer_compatible": (
            True
            if left["proposed_current_answer"]["state"] == right["proposed_current_answer"]["state"] == "unresolved"
            else compatible_answer(left["proposed_current_answer"], right["proposed_current_answer"], aliases)
        ),
        "event_type_agreement": left["event_type"] == right["event_type"],
        "shared_supporting_source": bool(left_ids & right_ids),
        "source_jaccard": len(left_ids & right_ids) / len(union) if union else 1.0,
    }


def _record_outcome(
    record: dict[str, Any],
    package: dict[str, Any],
    a: dict[str, Any],
    b: dict[str, Any],
    v: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    aliases = package["aliases"]
    reasons = trigger_reasons(a, b)
    pair, pair_reason = choose_decision_pair(a, b, v, alias_groups=aliases)
    conditions = acceptance_conditions(
        pair,
        reference_time=record["query_temporal_context"]["reference_time"],
        sources=package["sources"],
        alias_groups=aliases,
    )
    accepted = all(conditions.values())
    valid_judgments = [row["judgment"] for row in (a, b, v) if _valid_result(row)]
    final = copy.deepcopy(record)
    final["evidence_package_id"] = package["evidence_package_id"]
    final["evidence_sources"] = copy.deepcopy(package["sources"])
    final["auditor_agreement"] = _agreement(a, b, aliases)
    final["provenance"]["evidence_package_sha256"] = package["package_sha256"]
    prompt_hashes = {row.get("prompt_template_sha256") for row in (a, b) if row}
    final["provenance"]["prompt_sha256"] = prompt_hashes.pop() if len(prompt_hashes) == 1 else None
    git = git_metadata_ignoring_egta()
    final["provenance"]["git_sha"] = git.get("git_sha")
    final["provenance"]["git_dirty"] = bool(git["git_dirty"])

    if not reasons:
        final["verification_status"] = "not_triggered"
    elif _valid_result(v) and pair_reason == "auditor_verifier_pair":
        final["verification_status"] = "verifier_resolved"
    elif _valid_result(v):
        final["verification_status"] = "verifier_abstained"
    else:
        final["verification_status"] = "verifier_failed"

    pair_scope = pair[0]["temporal_scope"] if pair else None
    if pair and pair_scope == "non_temporal" and pair[1]["temporal_scope"] == "non_temporal":
        final["screening_disposition"] = ScreeningDisposition.NOT_TIME_SENSITIVE.value
        final["query_temporal_context"].update({"scope": "non_temporal", "direction": "none"})
        final["verdict"], final["event_type"] = None, "none"
        final["proposed_current_answer"] = {
            "state": "unresolved", "canonical": None, "aliases": [], "valid_as_of": None,
        }
        abstain = None
    else:
        final["screening_disposition"] = ScreeningDisposition.TEMPORAL_CANDIDATE.value
        if pair:
            final["query_temporal_context"]["scope"] = pair[0]["temporal_scope"]
            final["query_temporal_context"]["direction"] = pair[0]["direction"]
            final["event_type"] = pair[0]["event_type"]
            final["change_date"] = copy.deepcopy(pair[0]["change_date"])
        if accepted and pair:
            final["verdict"] = pair[0]["verdict"]
            answer = pair[0]["proposed_current_answer"]
            answer_aliases = sorted({
                value
                for group in aliases
                if answer["canonical"] is not None and any(
                    normalize_answer(value) == normalize_answer(answer["canonical"]) for value in group
                )
                for value in group
            })
            final["proposed_current_answer"] = {
                "state": answer["state"],
                "canonical": answer["canonical"],
                "aliases": answer_aliases,
                "valid_as_of": final["query_temporal_context"]["audit_as_of"],
            }
            abstain = None
        elif pair and pair[0]["verdict"] == pair[1]["verdict"] and pair[0]["verdict"] in ABSTENTION_CLASSES:
            abstain = abstention_reason(
                pair, pair_reason=pair_reason, conditions=conditions, auditor_judgments=valid_judgments
            )
            final["verdict"] = abstain
        elif pair and pair[0]["verdict"] == pair[1]["verdict"] and pair[0]["verdict"] in {
            Verdict.STILL_VALID.value, Verdict.HISTORICALLY_VALID.value,
        }:
            source_ok = all(_citation_support(decision, package["sources"])[0] for decision in pair)
            final["verdict"] = pair[0]["verdict"] if source_ok else Verdict.INSUFFICIENT_EVIDENCE.value
            abstain = None if source_ok else Verdict.INSUFFICIENT_EVIDENCE.value
            if source_ok and pair[0]["verdict"] == Verdict.STILL_VALID.value:
                answer = pair[0]["proposed_current_answer"]
                final["proposed_current_answer"] = {
                    "state": answer["state"],
                    "canonical": answer["canonical"],
                    "aliases": [],
                    "valid_as_of": final["query_temporal_context"]["audit_as_of"],
                }
        else:
            abstain = abstention_reason(
                pair, pair_reason=pair_reason, conditions=conditions, auditor_judgments=valid_judgments
            )
            final["verdict"] = abstain

    final["acceptance"] = {
        "source_supported": bool(
            accepted or final["verdict"] in {Verdict.STILL_VALID.value, Verdict.HISTORICALLY_VALID.value}
        ),
        "supersession_accepted": accepted,
        "conditions": conditions,
        "abstention_reason": abstain,
    }
    errors = validate_record(final)
    if errors:
        raise ValueError(f"aggregated record {final['item_id']} is invalid: {errors}")
    accounting = {
        "item_id": final["item_id"],
        "initial_triggered": bool(reasons),
        "trigger_reasons": reasons,
        "pair_reason": pair_reason,
        "final_verdict": final["verdict"],
        "screening_disposition": final["screening_disposition"],
        "supersession_accepted": accepted,
        "abstention_reason": abstain,
        "conditions": conditions,
        "supporting_evidence_ids": sorted({
            evidence_id for decision in (pair or []) for evidence_id in decision["supporting_evidence_ids"]
        }),
    }
    return final, accounting


def _assert_run_snapshot_consistency(
    item_ids: set[str],
    auditor_a: dict[str, dict[str, Any]],
    auditor_b: dict[str, dict[str, Any]],
    repeat: dict[str, dict[str, Any]],
) -> None:
    for item_id in item_ids:
        rows = (auditor_a[item_id], auditor_b[item_id], repeat[item_id])
        for field in ("evidence_package_sha256", "blind_request_sha256", "prompt_template_sha256"):
            values = {row.get(field) for row in rows}
            if len(values) != 1:
                raise ValueError(f"{item_id} has a cross-auditor {field} mismatch")
        a_backend, repeat_backend = rows[0]["backend_result"], rows[2]["backend_result"]
        if a_backend.get("backend") != repeat_backend.get("backend"):
            raise ValueError(f"{item_id} A-repeat backend differs from Auditor A")
        if a_backend.get("resolved_model") != repeat_backend.get("resolved_model"):
            raise ValueError(f"{item_id} A-repeat resolved model differs from Auditor A")


def aggregate_run(
    *,
    records_path: Path,
    evidence_root: Path,
    auditor_a: Path,
    auditor_b: Path,
    auditor_a_repeat: Path,
    verifier: Path,
    output_dir: Path,
) -> dict[str, Any]:
    from rae_egta.egta._aggregate_stats import write_statistical_products

    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to reuse non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    records = load_jsonl(records_path)
    a_results, b_results = load_results(auditor_a), load_results(auditor_b)
    repeat_results = load_results(auditor_a_repeat)
    verifier_results = load_results(verifier) if (verifier / "results.jsonl").is_file() or verifier.is_file() else {}
    expected = {record["item_id"] for record in records}
    if set(a_results) != expected or set(b_results) != expected or set(repeat_results) != expected:
        raise ValueError("canonical and auditor item sets must match exactly")
    _assert_run_snapshot_consistency(expected, a_results, b_results, repeat_results)
    final_records: list[dict[str, Any]] = []
    accounting: list[dict[str, Any]] = []
    for record in records:
        item_id = record["item_id"]
        package = load_evidence_for_item(evidence_root, item_id)
        for label, result in (
            ("auditor_a", a_results[item_id]),
            ("auditor_b", b_results[item_id]),
            ("auditor_a_repeat", repeat_results[item_id]),
            ("verifier", verifier_results.get(item_id)),
        ):
            if result is not None and result.get("evidence_package_sha256") != package["package_sha256"]:
                raise ValueError(f"{item_id} {label} evidence hash differs from the aggregate snapshot")
        final, account = _record_outcome(
            record,
            package,
            a_results[item_id],
            b_results[item_id],
            verifier_results.get(item_id),
        )
        final_records.append(final)
        accounting.append(account)
    final_path = output_dir / "final_records.jsonl"
    write_jsonl(final_path, final_records)
    summary = write_statistical_products(
        output_dir=output_dir,
        final_records=final_records,
        accounting=accounting,
        auditor_a=a_results,
        auditor_b=b_results,
        repeat=repeat_results,
        verifier=verifier_results,
        input_paths={
            "canonical_records": records_path,
            "auditor_a": auditor_a / "results.jsonl" if auditor_a.is_dir() else auditor_a,
            "auditor_b": auditor_b / "results.jsonl" if auditor_b.is_dir() else auditor_b,
            "auditor_a_repeat": auditor_a_repeat / "results.jsonl" if auditor_a_repeat.is_dir() else auditor_a_repeat,
            "verifier": verifier / "results.jsonl" if verifier.is_dir() else verifier,
        },
        evidence_root=evidence_root,
    )
    update_run_manifest_near(
        output_dir,
        section="aggregate",
        key="deterministic",
        payload={
            "unique_item_count": summary["unique_item_count"],
            "terminal_item_count": summary["terminal_item_count"],
            "final_records_sha256": sha256_path(final_path),
        },
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--auditor-a", type=Path, required=True)
    parser.add_argument("--auditor-b", type=Path, required=True)
    parser.add_argument("--auditor-a-repeat", type=Path, required=True)
    parser.add_argument("--verifier", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = aggregate_run(
        records_path=args.records,
        evidence_root=args.evidence_root,
        auditor_a=args.auditor_a,
        auditor_b=args.auditor_b,
        auditor_a_repeat=args.auditor_a_repeat,
        verifier=args.verifier,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
