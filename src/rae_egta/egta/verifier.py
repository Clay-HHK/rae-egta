"""Build the EGTA disagreement queue and run a leakage-safe blind verifier."""

from __future__ import annotations

import argparse
import json
import unicodedata
from pathlib import Path
from typing import Any, Mapping, Sequence

from rae_egta.egta.backends import AUDIT_JUDGMENT_SCHEMA, run_audit_call, validate_audit_judgment
from rae_egta.egta.evidence import sha256_bytes, sha256_path
from rae_egta.egta.runner import load_jsonl, update_run_manifest_near, write_json, write_jsonl
from rae_egta.egta.schema import assert_blind


PROMPT_PATH = Path(__file__).with_name("prompts") / "verifier.md"


def _results_path(path: Path) -> Path:
    return path / "results.jsonl" if path.is_dir() else path


def load_results(path: Path) -> dict[str, dict[str, Any]]:
    rows = load_jsonl(_results_path(path))
    result = {row["item_id"]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"duplicate item_id values in {_results_path(path)}")
    return result


def _valid_result(result: dict[str, Any]) -> bool:
    status = result.get("status", {})
    return bool(
        status.get("terminal_status") == "ok"
        and status.get("evidence_contract_valid")
        and status.get("judgment_schema_valid")
        and status.get("zero_tool_calls")
    )


def _answer_key(answer: Any) -> tuple[str, str | None]:
    if not isinstance(answer, dict):
        return ("invalid", None)
    canonical = answer.get("canonical")
    normalized = None
    if isinstance(canonical, str):
        normalized = unicodedata.normalize("NFKC", canonical).casefold().strip()
        while normalized and (normalized[0].isspace() or unicodedata.category(normalized[0]).startswith("P")):
            normalized = normalized[1:]
        while normalized and (normalized[-1].isspace() or unicodedata.category(normalized[-1]).startswith("P")):
            normalized = normalized[:-1]
        normalized = " ".join(normalized.split())
    return (str(answer.get("state")), normalized)


def _answers_compatible(left: Any, right: Any, aliases: list[list[str]]) -> bool:
    left_state, left_value = _answer_key(left)
    right_state, right_value = _answer_key(right)
    if left_state != right_state or left_state == "invalid":
        return False
    if left_state == "unresolved":
        return True
    if left_state == "absent_or_retired":
        return True
    if left_value is None or right_value is None:
        return False
    if left_value == right_value:
        return True
    expanded_left, expanded_right = {left_value}, {right_value}
    for group in aliases:
        normalized = {_answer_key({"state": "value", "canonical": value})[1] for value in group if isinstance(value, str)}
        if left_value in normalized:
            expanded_left.update(normalized)
        if right_value in normalized:
            expanded_right.update(normalized)
    return bool(expanded_left & expanded_right)


def trigger_reasons(auditor_a: dict[str, Any], auditor_b: dict[str, Any]) -> list[str]:
    """Return the pre-registered §1.7 trigger reasons for one item."""

    reasons: list[str] = []
    for label, result in (("auditor_a", auditor_a), ("auditor_b", auditor_b)):
        status = result.get("status", {})
        if not status.get("evidence_contract_valid", False):
            reasons.append(f"{label}_evidence_contract_failure")
        if result.get("backend_result", {}).get("parse_failure"):
            reasons.append(f"{label}_parse_failure")
        elif not status.get("judgment_schema_valid", False):
            reasons.append(f"{label}_schema_failure")
        if result.get("backend_result", {}).get("tool_call_count", 0) > 0:
            reasons.append(f"{label}_tool_contract_violation")
    if not _valid_result(auditor_a) or not _valid_result(auditor_b):
        return sorted(set(reasons)) or ["auditor_terminal_failure"]
    a = auditor_a["judgment"]
    b = auditor_b["judgment"]
    if a["temporal_scope"] != b["temporal_scope"]:
        reasons.append("temporal_scope_disagreement")
    if a["verdict"] != b["verdict"]:
        reasons.append("verdict_disagreement")
    if a["event_type"] != b["event_type"] and (
        a["event_type"] in {"revision", "retirement"}
        or b["event_type"] in {"revision", "retirement"}
    ):
        reasons.append("revision_retirement_event_type_disagreement")
    aliases = auditor_a.get("evidence_aliases", [])
    if aliases != auditor_b.get("evidence_aliases", []):
        reasons.append("evidence_alias_snapshot_disagreement")
    if not _answers_compatible(a["proposed_current_answer"], b["proposed_current_answer"], aliases):
        reasons.append("current_answer_incompatible")
    if set(a["supporting_evidence_ids"]) != set(b["supporting_evidence_ids"]):
        reasons.append("supporting_evidence_disagreement")
    abstention_classes = {"ambiguous_context", "source_conflict"}
    if a["verdict"] in abstention_classes or b["verdict"] in abstention_classes:
        reasons.append("auditor_ambiguity_or_conflict")
    if a["credible_source_conflict"] or b["credible_source_conflict"]:
        reasons.append("auditor_ambiguity_or_conflict")
    return sorted(set(reasons))


def _request_for_result(root: Path, result: dict[str, Any]) -> dict[str, Any]:
    item_id = result["item_id"]
    if root.is_dir():
        path = root / item_id / "blind_request.json"
        request = json.loads(path.read_text(encoding="utf-8"))
    else:
        request = result.get("blind_request")
        if request is None:
            raise ValueError("a standalone results JSONL must embed blind_request")
    assert_blind(request)
    return request


def build_disagreement_queue(
    *,
    auditor_a: Path,
    auditor_b: Path,
    output: Path,
) -> dict[str, Any]:
    """Compare A1/A2 and save only triggered blind requests plus reason metadata."""

    a_results = load_results(auditor_a)
    b_results = load_results(auditor_b)
    if set(a_results) != set(b_results):
        raise ValueError("auditor result item sets differ")
    queue: list[dict[str, Any]] = []
    reason_counts: dict[str, int] = {}
    for item_id in sorted(a_results):
        a_row = a_results[item_id]
        b_row = b_results[item_id]
        reasons = trigger_reasons(a_row, b_row)
        if not reasons:
            continue
        request_a = _request_for_result(auditor_a, a_row)
        request_b = _request_for_result(auditor_b, b_row)
        if sha256_bytes(json.dumps(request_a, sort_keys=True).encode()) != sha256_bytes(
            json.dumps(request_b, sort_keys=True).encode()
        ):
            reasons = sorted(set([*reasons, "auditor_request_mismatch"]))
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        queue.append(
            {
                "item_id": item_id,
                "trigger_reasons": reasons,
                "blind_request": request_a,
                "evidence_package_sha256": a_row.get("evidence_package_sha256"),
                "blind_request_sha256": a_row.get("blind_request_sha256"),
            }
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(output, queue)
    manifest = {
        "schema_version": "egta_verifier_queue_manifest.v1",
        "auditor_item_count": len(a_results),
        "queue_item_count": len(queue),
        "reason_counts": reason_counts,
        "auditor_a_results_sha256": sha256_path(_results_path(auditor_a)),
        "auditor_b_results_sha256": sha256_path(_results_path(auditor_b)),
        "queue_sha256": sha256_path(output),
    }
    write_json(output.with_suffix(output.suffix + ".manifest.json"), manifest)
    return manifest


def render_verifier_prompt(template: str, request: dict[str, Any]) -> str:
    rendered = template.replace(
        "{{REQUEST_JSON}}",
        json.dumps(request, indent=2, sort_keys=True, ensure_ascii=False),
    ).replace(
        "{{OUTPUT_SCHEMA}}",
        json.dumps(AUDIT_JUDGMENT_SCHEMA, indent=2, sort_keys=True, ensure_ascii=False),
    )
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("unresolved verifier prompt-template placeholder")
    return rendered


def run_verifier(
    *,
    queue_path: Path,
    backend: str = "grok_http",
    model: str = "grok-4.5-build-free",
    output_dir: Path,
    config: Mapping[str, Any] | None = None,
    prompt_path: Path = PROMPT_PATH,
) -> dict[str, Any]:
    """Run only the blind projection of each queue row."""

    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to reuse non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    queue = load_jsonl(queue_path)
    template = prompt_path.read_text(encoding="utf-8")
    template_hash = sha256_bytes(template.encode("utf-8"))
    results: list[dict[str, Any]] = []
    for row in queue:
        item_id = row["item_id"]
        request = row["blind_request"]
        assert_blind(request)
        prompt = render_verifier_prompt(template, request)
        call_config = dict(config or {})
        call_config.update({"backend": backend, "model": model, "item_id": item_id})
        backend_result = run_audit_call("verifier", prompt, call_config)
        parsed = backend_result.parsed_json
        schema_errors = validate_audit_judgment(parsed, expected_item_id=item_id) if parsed is not None else []
        status_name = backend_result.status
        if status_name == "ok" and schema_errors:
            status_name = "schema_failure"
        status = {
            "item_id": item_id,
            "terminal_status": status_name,
            "json_parse_valid": not backend_result.parse_failure,
            "judgment_schema_valid": parsed is not None and not schema_errors,
            "judgment_schema_errors": schema_errors,
            "date_normalization": backend_result.date_normalization,
            "zero_tool_calls": backend_result.tool_call_count == 0,
            "evidence_contract_valid": True,
        }
        item_dir = output_dir / item_id
        item_dir.mkdir()
        write_json(item_dir / "blind_request.json", request)
        (item_dir / "prompt.md").write_text(prompt, encoding="utf-8")
        (item_dir / "raw_output.txt").write_text(backend_result.raw_stream, encoding="utf-8")
        (item_dir / "stderr.txt").write_text(backend_result.stderr, encoding="utf-8")
        write_json(item_dir / "parsed_output.json", parsed)
        write_json(item_dir / "status.json", status)
        hashes = {
            name: sha256_path(item_dir / name)
            for name in (
                "blind_request.json", "prompt.md", "raw_output.txt", "stderr.txt",
                "parsed_output.json", "status.json",
            )
        }
        write_json(item_dir / "hashes.json", hashes)
        results.append(
            {
                "item_id": item_id,
                "role": "verifier",
                "backend_result": backend_result.to_dict(),
                "judgment": parsed,
                "status": status,
                "prompt_template_sha256": template_hash,
                "rendered_prompt_sha256": hashes["prompt.md"],
                "blind_request_sha256": hashes["blind_request.json"],
                "evidence_package_sha256": row.get("evidence_package_sha256"),
            }
        )
    results_path = output_dir / "results.jsonl"
    write_jsonl(results_path, results)
    status_counts: dict[str, int] = {}
    for result in results:
        key = result["status"]["terminal_status"]
        status_counts[key] = status_counts.get(key, 0) + 1
    manifest = {
        "schema_version": "egta_verifier_manifest.v1",
        "backend": backend,
        "requested_model": model,
        "queue_item_count": len(queue),
        "terminal_item_count": len(results),
        "status_counts": status_counts,
        "queue_sha256": sha256_path(queue_path),
        "prompt_template_sha256": template_hash,
        "results_sha256": sha256_path(results_path),
        "input_projection": "blind_request_only",
        "tool_calls_allowed": False,
        "session_persistence": False,
    }
    write_json(output_dir / "manifest.json", manifest)
    update_run_manifest_near(
        output_dir,
        section="verifier",
        key="blind_verifier",
        payload={
            "backend": backend,
            "requested_model": model,
            "queue_item_count": len(queue),
            "status_counts": status_counts,
            "queue_sha256": manifest["queue_sha256"],
            "prompt_sha256": template_hash,
            "results_sha256": manifest["results_sha256"],
        },
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-queue")
    build.add_argument("--auditor-a", type=Path, required=True)
    build.add_argument("--auditor-b", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--queue", type=Path, required=True)
    run.add_argument("--backend", choices=("claude_cli", "codex_cli", "grok_http", "mock"), default="grok_http")
    run.add_argument("--model", default="grok-4.5-build-free")
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--timeout-seconds", type=float, default=120.0)
    run.add_argument("--retries", type=int, default=2)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "build-queue":
        manifest = build_disagreement_queue(auditor_a=args.auditor_a, auditor_b=args.auditor_b, output=args.output)
    else:
        manifest = run_verifier(
            queue_path=args.queue,
            backend=args.backend,
            model=args.model,
            output_dir=args.output_dir,
            config={"timeout_seconds": args.timeout_seconds, "retries": args.retries},
        )
    print(json.dumps(manifest, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
