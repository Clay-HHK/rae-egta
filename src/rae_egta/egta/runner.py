"""Run isolated EGTA auditors over blind candidates and frozen evidence."""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from rae_egta.egta.backends import (
    AUDIT_JUDGMENT_SCHEMA,
    BackendResult,
    run_audit_call,
    validate_audit_judgment,
)
from rae_egta.egta._provenance import git_metadata_ignoring_egta
from rae_egta.egta.evidence import (
    canonical_json_bytes,
    load_evidence_for_item,
    sha256_bytes,
    sha256_path,
    snapshot_evidence_root,
)
from rae_egta.egta.schema import assert_blind


PROMPT_PATH = Path(__file__).with_name("prompts") / "auditor.md"
ROLES = {"auditor_a", "auditor_b", "auditor_a_repeat"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        rows.append(payload)
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def update_run_manifest_near(
    output_dir: Path,
    *,
    section: str,
    key: str,
    payload: dict[str, Any],
) -> Path | None:
    """Merge one stage snapshot into the nearest migration run manifest."""

    manifest_path = next(
        (parent / "run_manifest.json" for parent in output_dir.parents if (parent / "run_manifest.json").is_file()),
        None,
    )
    if manifest_path is None:
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    snapshots = manifest.setdefault("egta_snapshots", {})
    snapshots.setdefault(section, {})[key] = payload
    git = git_metadata_ignoring_egta()
    manifest["git_sha"] = git.get("git_sha")
    manifest["git_dirty"] = bool(git["git_dirty"])
    manifest["git_status_short"] = git.get("git_status_short", "")
    manifest["git_dirty_ignored_prefixes"] = git.get("git_dirty_ignored_prefixes", [])
    write_json(manifest_path, manifest)
    return manifest_path


def render_prompt(template: str, *, role: str, request: dict[str, Any]) -> str:
    rendered = template.replace("{{ROLE}}", role)
    rendered = rendered.replace("{{REQUEST_JSON}}", json.dumps(request, indent=2, sort_keys=True, ensure_ascii=False))
    rendered = rendered.replace(
        "{{OUTPUT_SCHEMA}}",
        json.dumps(AUDIT_JUDGMENT_SCHEMA, indent=2, sort_keys=True, ensure_ascii=False),
    )
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("unresolved prompt-template placeholder")
    return rendered


def _failed_backend(role: str, backend: str, model: str, message: str) -> dict[str, Any]:
    return {
        "backend": backend,
        "role": role,
        "raw_text": "",
        "raw_stream": "",
        "parsed_json": None,
        "date_normalization": None,
        "exit_code": None,
        "http_status": None,
        "duration_seconds": 0.0,
        "retry_count": 0,
        "resolved_model": model,
        "backend_version": None,
        "tool_call_count": 0,
        "parse_failure": True,
        "contract_violation": False,
        "status": "evidence_contract_failure",
        "stderr": message,
    }


def _item_files(
    item_dir: Path,
    *,
    request: dict[str, Any],
    prompt: str,
    result: dict[str, Any],
    status: dict[str, Any],
    evidence_path: Path,
) -> dict[str, str | None]:
    write_json(item_dir / "blind_request.json", request)
    (item_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    (item_dir / "raw_output.txt").write_text(
        str(result.get("raw_stream", result["raw_text"])), encoding="utf-8"
    )
    (item_dir / "stderr.txt").write_text(str(result.get("stderr", "")), encoding="utf-8")
    write_json(item_dir / "parsed_output.json", result.get("parsed_json"))
    write_json(item_dir / "status.json", status)
    hashes: dict[str, str | None] = {
        "blind_request.json": sha256_path(item_dir / "blind_request.json"),
        "prompt.md": sha256_path(item_dir / "prompt.md"),
        "raw_output.txt": sha256_path(item_dir / "raw_output.txt"),
        "stderr.txt": sha256_path(item_dir / "stderr.txt"),
        "parsed_output.json": sha256_path(item_dir / "parsed_output.json"),
        "status.json": sha256_path(item_dir / "status.json"),
        "evidence_file": sha256_path(evidence_path) if evidence_path.is_file() else None,
    }
    write_json(item_dir / "hashes.json", hashes)
    return hashes


def run_auditor(
    *,
    input_path: Path,
    evidence_root: Path,
    backend: str,
    model: str,
    role: str,
    output_dir: Path,
    config: Mapping[str, Any] | None = None,
    repeat_of: str | None = None,
    prompt_path: Path = PROMPT_PATH,
) -> dict[str, Any]:
    """Run one auditor role and persist every terminal item artifact."""

    if role not in ROLES:
        raise ValueError(f"unsupported auditor role: {role!r}")
    if role == "auditor_a_repeat" and repeat_of not in {None, "auditor_a"}:
        raise ValueError("auditor_a_repeat may only repeat auditor_a")
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to reuse non-empty output directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = load_jsonl(input_path)
    item_ids = [str(row.get("item_id", "")) for row in rows]
    if len(item_ids) != len(set(item_ids)):
        raise ValueError("blind input contains duplicate item_id values")
    for row in rows:
        assert_blind(row)

    template = prompt_path.read_text(encoding="utf-8")
    template_hash = sha256_bytes(template.encode("utf-8"))
    try:
        evidence_manifest = snapshot_evidence_root(evidence_root, write=True)
        evidence_snapshot_error = None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        evidence_manifest = None
        evidence_snapshot_error = str(exc)

    results: list[dict[str, Any]] = []
    for blind in rows:
        item_id = blind["item_id"]
        item_dir = output_dir / item_id
        item_dir.mkdir()
        evidence_path = evidence_root / f"{item_id}.json"
        request = copy.deepcopy(blind)
        evidence_package: dict[str, Any] | None = None
        evidence_error: str | None = None
        try:
            evidence_package = load_evidence_for_item(evidence_root, item_id)
            request["evidence_package_id"] = evidence_package["evidence_package_id"]
            request["evidence_sources"] = copy.deepcopy(evidence_package["sources"])
            assert_blind(request)
        except (OSError, ValueError, json.JSONDecodeError, AssertionError) as exc:
            evidence_error = str(exc)
        prompt = render_prompt(template, role=role, request=request) if evidence_error is None else ""
        call_config = dict(config or {})
        call_config.update({"backend": backend, "model": model, "item_id": item_id})
        if evidence_error is None:
            backend_result: BackendResult | None = run_audit_call(role, prompt, call_config)
            result_dict = backend_result.to_dict()
        else:
            result_dict = _failed_backend(role, backend, model, evidence_error)
        parsed = result_dict.get("parsed_json")
        schema_errors = validate_audit_judgment(parsed, expected_item_id=item_id) if parsed is not None else []
        schema_valid = parsed is not None and not schema_errors
        tool_ok = result_dict["tool_call_count"] == 0
        terminal_status = str(result_dict["status"])
        if terminal_status == "ok" and not schema_valid:
            terminal_status = "schema_failure"
        status = {
            "item_id": item_id,
            "role": role,
            "backend": backend,
            "requested_model": model,
            "terminal_status": terminal_status,
            "evidence_contract_valid": evidence_error is None,
            "evidence_contract_error": evidence_error,
            "json_parse_valid": not bool(result_dict["parse_failure"]),
            "judgment_schema_valid": schema_valid,
            "judgment_schema_errors": schema_errors,
            "date_normalization": result_dict.get("date_normalization"),
            "zero_tool_calls": tool_ok,
        }
        hashes = _item_files(
            item_dir,
            request=request,
            prompt=prompt,
            result=result_dict,
            status=status,
            evidence_path=evidence_path,
        )
        results.append(
            {
                "item_id": item_id,
                "role": role,
                "backend_result": result_dict,
                "judgment": parsed,
                "status": status,
                "evidence_package_sha256": (
                    evidence_package["package_sha256"] if evidence_package is not None else None
                ),
                "evidence_package_id": (
                    evidence_package["evidence_package_id"] if evidence_package is not None else None
                ),
                "evidence_aliases": evidence_package["aliases"] if evidence_package is not None else [],
                "prompt_template_sha256": template_hash,
                "rendered_prompt_sha256": hashes["prompt.md"],
                "blind_request_sha256": hashes["blind_request.json"],
            }
        )

    results_path = output_dir / "results.jsonl"
    write_jsonl(results_path, results)
    status_counts: dict[str, int] = {}
    for result in results:
        key = result["status"]["terminal_status"]
        status_counts[key] = status_counts.get(key, 0) + 1
    resolved_models = sorted(
        {
            str(row["backend_result"]["resolved_model"])
            for row in results
            if row["backend_result"].get("resolved_model") is not None
        }
    )
    versions = sorted(
        {
            str(row["backend_result"]["backend_version"])
            for row in results
            if row["backend_result"].get("backend_version") is not None
        }
    )
    manifest = {
        "schema_version": "egta_auditor_manifest.v1",
        "role": role,
        "repeat_of": repeat_of,
        "backend": backend,
        "requested_model": model,
        "resolved_models": resolved_models,
        "backend_versions": versions,
        "item_count": len(results),
        "status_counts": status_counts,
        "input_sha256": sha256_path(input_path),
        "prompt_template_sha256": template_hash,
        "evidence_snapshot_sha256": (
            evidence_manifest["snapshot_sha256"] if evidence_manifest is not None else None
        ),
        "evidence_snapshot_error": evidence_snapshot_error,
        "results_sha256": sha256_path(results_path),
        "session_persistence": False,
        "cache_reused": False,
        "tool_calls_allowed": False,
    }
    write_json(output_dir / "manifest.json", manifest)
    update_run_manifest_near(
        output_dir,
        section="auditors",
        key=role,
        payload={
            "backend": backend,
            "requested_model": model,
            "resolved_models": resolved_models,
            "backend_versions": versions,
            "item_count": len(results),
            "status_counts": status_counts,
            "input_sha256": manifest["input_sha256"],
            "evidence_snapshot_sha256": manifest["evidence_snapshot_sha256"],
            "prompt_sha256": template_hash,
            "results_sha256": manifest["results_sha256"],
        },
    )
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--backend", choices=("claude_cli", "codex_cli", "grok_http", "mock"), required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--role", choices=sorted(ROLES), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repeat-of", choices=("auditor_a",))
    parser.add_argument("--command-template", nargs="+")
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--retries", type=int, default=0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config: dict[str, Any] = {"timeout_seconds": args.timeout_seconds, "retries": args.retries}
    if args.command_template:
        config["command_template"] = args.command_template
    manifest = run_auditor(
        input_path=args.input,
        evidence_root=args.evidence_root,
        backend=args.backend,
        model=args.model,
        role=args.role,
        output_dir=args.output_dir,
        config=config,
        repeat_of=args.repeat_of,
    )
    print(json.dumps(manifest, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
