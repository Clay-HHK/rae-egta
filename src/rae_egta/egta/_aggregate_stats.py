"""CSV/JSON/report writers for :mod:`rae_egta.egta.aggregate`."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from rae_egta.egta.aggregate import compatible_answer, git_metadata_ignoring_egta
from rae_egta.egta.evidence import sha256_path, snapshot_evidence_root
from rae_egta.egta.schema import ScreeningDisposition, Verdict


def wilson_interval(successes: int, denominator: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    """Return the standard two-sided Wilson score interval."""

    if denominator <= 0:
        return None, None
    p = successes / denominator
    z2 = z * z
    center = (p + z2 / (2 * denominator)) / (1 + z2 / denominator)
    margin = z * math.sqrt((p * (1 - p) + z2 / (4 * denominator)) / denominator) / (1 + z2 / denominator)
    return center - margin, center + margin


def rate_payload(successes: int, denominator: int) -> dict[str, int | float | None]:
    low, high = wilson_interval(successes, denominator)
    return {
        "numerator": successes,
        "denominator": denominator,
        "rate": successes / denominator if denominator else None,
        "wilson95_low": low,
        "wilson95_high": high,
    }


def _write_csv(path: Path, fields: list[str], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _distribution_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    outcomes = [item.value for item in Verdict] + ["non_temporal_exclusion"]
    groups: dict[str, list[dict[str, Any]]] = {"total": records}
    for label in ("A", "B", "C"):
        groups[label] = [record for record in records if record["legacy"]["stratum"].startswith(label)]
    rows: list[dict[str, Any]] = []
    for group, subset in groups.items():
        counts = Counter(
            "non_temporal_exclusion"
            if record["screening_disposition"] == ScreeningDisposition.NOT_TIME_SENSITIVE.value
            else record["verdict"]
            for record in subset
        )
        for outcome in outcomes:
            rows.append({"stratum": group, "outcome": outcome, **rate_payload(counts[outcome], len(subset))})
    return rows


def _agreement_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapping = {
        "exact_class": "initial_exact_class",
        "temporal_scope": "temporal_scope_agreement",
        "frozen_answer": "frozen_answer_agreement",
        "current_answer": "current_answer_compatible",
        "event_type": "event_type_agreement",
        "shared_supporting_source": "shared_supporting_source",
    }
    rows: list[dict[str, Any]] = []
    for metric, key in mapping.items():
        values = [record["auditor_agreement"][key] for record in records]
        observed = [value for value in values if value is not None]
        rows.append({"metric": metric, **rate_payload(sum(value is True for value in observed), len(observed))})
    jaccards = [
        record["auditor_agreement"]["source_jaccard"]
        for record in records
        if record["auditor_agreement"]["source_jaccard"] is not None
    ]
    rows.append(
        {
            "metric": "mean_source_jaccard",
            "numerator": "",
            "denominator": len(jaccards),
            "rate": sum(jaccards) / len(jaccards) if jaccards else None,
            "wilson95_low": None,
            "wilson95_high": None,
        }
    )
    return rows


def _verifier_rows(accounting: list[dict[str, Any]], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    status = {record["item_id"]: record["verification_status"] for record in records}
    rows: list[dict[str, Any]] = []
    by_reason: dict[str, list[str]] = defaultdict(list)
    for account in accounting:
        for reason in account["trigger_reasons"]:
            by_reason[reason].append(status[account["item_id"]])
    for reason in sorted(by_reason):
        values = by_reason[reason]
        rows.append(
            {
                "trigger_reason": reason,
                "count": len(values),
                "resolved": values.count("verifier_resolved"),
                "abstained": values.count("verifier_abstained"),
                "failed": values.count("verifier_failed"),
            }
        )
    return rows


def _abstention_rows(accounting: list[dict[str, Any]], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    record_map = {record["item_id"]: record for record in records}
    abstentions = {item.value for item in (Verdict.AMBIGUOUS_CONTEXT, Verdict.INSUFFICIENT_EVIDENCE, Verdict.SOURCE_CONFLICT)}
    rows: list[dict[str, Any]] = []
    for scope in ("all_47", "temporal_scope"):
        subset = [
            account for account in accounting
            if scope == "all_47" or record_map[account["item_id"]]["query_temporal_context"]["scope"] == "temporal"
        ]
        denominator = len(subset)
        counts = Counter(
            (account["abstention_reason"], account["pair_reason"])
            for account in subset
            if account["abstention_reason"] in abstentions
        )
        for (reason, detail), count in sorted(counts.items()):
            rows.append(
                {
                    "denominator_scope": scope,
                    "abstention_reason": reason,
                    "detail": detail,
                    **rate_payload(count, denominator),
                }
            )
    return rows


TIER_ORDER = {
    "official_primary": 0,
    "authoritative_database": 1,
    "versioned_encyclopedia": 2,
    "reliable_secondary": 3,
    "other": 4,
}


def _source_rows(records: list[dict[str, Any]], accounting: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accounts = {account["item_id"]: account for account in accounting}
    rows: list[dict[str, Any]] = []
    for record in records:
        account = accounts[record["item_id"]]
        selected = set(account.get("supporting_evidence_ids", []))
        sources = [source for source in record["evidence_sources"] if source["evidence_id"] in selected]
        best = min((source["source_tier"] for source in sources), key=lambda tier: TIER_ORDER[tier], default="none")
        rows.append(
            {
                "item_id": record["item_id"],
                "best_source_tier": best,
                "supporting_source_count": len(sources),
                "multiple_source": len(sources) >= 2,
                "credible_source_conflict": record["verdict"] == Verdict.SOURCE_CONFLICT.value,
            }
        )
    return rows


def _source_summary_rows(source_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tiers = [*TIER_ORDER, "none"]
    counts = Counter(row["best_source_tier"] for row in source_rows)
    return [{"best_source_tier": tier, **rate_payload(counts[tier], len(source_rows))} for tier in tiers]


def _stable_result(result: dict[str, Any] | None) -> bool:
    return bool(result and result.get("status", {}).get("terminal_status") == "ok" and result["status"].get("judgment_schema_valid"))


def _stability_rows(auditor_a: dict[str, dict[str, Any]], repeat: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    comparisons: dict[str, list[bool]] = defaultdict(list)
    for item_id in sorted(auditor_a):
        left, right = auditor_a[item_id], repeat[item_id]
        if not _stable_result(left) or not _stable_result(right):
            continue
        a, b = left["judgment"], right["judgment"]
        aliases = left.get("evidence_aliases", [])
        comparisons["exact_class"].append(a["verdict"] == b["verdict"])
        comparisons["current_answer"].append(
            True
            if a["proposed_current_answer"]["state"] == b["proposed_current_answer"]["state"] == "unresolved"
            else compatible_answer(a["proposed_current_answer"], b["proposed_current_answer"], aliases)
        )
        comparisons["source_selection"].append(set(a["supporting_evidence_ids"]) == set(b["supporting_evidence_ids"]))
    return [
        {"metric": metric, **rate_payload(sum(values), len(values))}
        for metric, values in sorted(comparisons.items())
    ]


def _accounting_rows(accounting: list[dict[str, Any]], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    record_map = {record["item_id"]: record for record in records}
    rows: list[dict[str, Any]] = []
    for account in accounting:
        record = record_map[account["item_id"]]
        rows.append(
            {
                "item_id": account["item_id"],
                "stratum": record["legacy"]["stratum"],
                "screening_disposition": account["screening_disposition"],
                "final_verdict": account["final_verdict"],
                "initial_triggered": account["initial_triggered"],
                "trigger_reasons": "|".join(account["trigger_reasons"]),
                "verification_status": record["verification_status"],
                "pair_reason": account["pair_reason"],
                "supersession_accepted": account["supersession_accepted"],
                "abstention_reason": account["abstention_reason"],
            }
        )
    return rows


def _models(results: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    return {
        "backends": sorted({
            str(row["backend_result"]["backend"])
            for row in results.values()
            if row["backend_result"].get("backend") is not None
        }),
        "resolved_models": sorted({
            str(row["backend_result"]["resolved_model"])
            for row in results.values()
            if row["backend_result"].get("resolved_model") is not None
        }),
        "backend_versions": sorted({
            str(row["backend_result"]["backend_version"])
            for row in results.values()
            if row["backend_result"].get("backend_version") is not None
        }),
    }


def write_statistical_products(
    *,
    output_dir: Path,
    final_records: list[dict[str, Any]],
    accounting: list[dict[str, Any]],
    auditor_a: dict[str, dict[str, Any]],
    auditor_b: dict[str, dict[str, Any]],
    repeat: dict[str, dict[str, Any]],
    verifier: dict[str, dict[str, Any]],
    input_paths: dict[str, Path],
    evidence_root: Path,
) -> dict[str, Any]:
    distribution = _distribution_rows(final_records)
    agreement = _agreement_rows(final_records)
    verifier_rows = _verifier_rows(accounting, final_records)
    abstention = _abstention_rows(accounting, final_records)
    source_rows = _source_rows(final_records, accounting)
    source_summary = _source_summary_rows(source_rows)
    stability = _stability_rows(auditor_a, repeat)
    item_rows = _accounting_rows(accounting, final_records)
    rate_fields = ["numerator", "denominator", "rate", "wilson95_low", "wilson95_high"]
    _write_csv(output_dir / "egta_verdict_distribution.csv", ["stratum", "outcome", *rate_fields], distribution)
    _write_csv(output_dir / "egta_auditor_agreement.csv", ["metric", *rate_fields], agreement)
    _write_csv(output_dir / "egta_verifier_trigger.csv", ["trigger_reason", "count", "resolved", "abstained", "failed"], verifier_rows)
    _write_csv(output_dir / "egta_abstention.csv", ["denominator_scope", "abstention_reason", "detail", *rate_fields], abstention)
    _write_csv(output_dir / "egta_source_quality.csv", ["item_id", "best_source_tier", "supporting_source_count", "multiple_source", "credible_source_conflict"], source_rows)
    _write_csv(output_dir / "egta_source_quality_summary.csv", ["best_source_tier", *rate_fields], source_summary)
    _write_csv(output_dir / "egta_stability.csv", ["metric", *rate_fields], stability)
    _write_csv(output_dir / "egta_item_accounting.csv", list(item_rows[0]) if item_rows else ["item_id"], item_rows)

    verdict_counts = Counter(
        "non_temporal_exclusion" if record["verdict"] is None else record["verdict"]
        for record in final_records
    )
    summary = {
        "schema_version": "egta_summary.v1",
        "unique_item_count": len(final_records),
        "terminal_item_count": len(final_records),
        "verdict_counts": dict(sorted(verdict_counts.items())),
        "verdict_distribution": {
            outcome: rate_payload(count, len(final_records))
            for outcome, count in sorted(verdict_counts.items())
        },
        "accepted_supersession": rate_payload(
            sum(record["acceptance"]["supersession_accepted"] for record in final_records),
            len(final_records),
        ),
        "verifier_trigger": rate_payload(sum(account["initial_triggered"] for account in accounting), len(accounting)),
        "agreement": {row["metric"]: {key: row[key] for key in rate_fields} for row in agreement},
        "stability": {row["metric"]: {key: row[key] for key in rate_fields} for row in stability},
    }
    (output_dir / "egta_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = (
        "# EGTA deterministic aggregation report\n\n"
        f"- Unique item denominator: {len(final_records)}\n"
        f"- Accepted supersession: {summary['accepted_supersession']['numerator']}/{len(final_records)}\n"
        f"- Initial verifier triggers: {summary['verifier_trigger']['numerator']}/{len(final_records)}\n"
        "- Stability label: decision rerun stability under a fixed evidence package.\n"
        "- This report does not claim end-to-end evidence-retrieval stability or population prevalence.\n"
    )
    (output_dir / "egta_report.md").write_text(report, encoding="utf-8")

    product_names = sorted(path.name for path in output_dir.iterdir() if path.is_file() and path.name != "analysis_manifest.json")
    code_root = Path(__file__).parent
    code_paths = sorted(code_root.glob("*.py")) + sorted((code_root / "prompts").glob("*.md"))
    manifest = {
        "schema_version": "egta_analysis_manifest.v1",
        "git": git_metadata_ignoring_egta(),
        "inputs": {name: sha256_path(path) if path.is_file() else None for name, path in sorted(input_paths.items())},
        "evidence_snapshot": snapshot_evidence_root(evidence_root, write=False),
        "code": {
            str(path.relative_to(code_root)): sha256_path(path)
            for path in code_paths
        },
        "models": {
            "auditor_a": _models(auditor_a),
            "auditor_b": _models(auditor_b),
            "auditor_a_repeat": _models(repeat),
            "verifier": _models(verifier),
        },
        "prompt_hashes": sorted({
            row.get("prompt_template_sha256")
            for results in (auditor_a, auditor_b, repeat, verifier)
            for row in results.values()
            if row.get("prompt_template_sha256")
        }),
        "products": {name: sha256_path(output_dir / name) for name in product_names},
    }
    (output_dir / "analysis_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary
