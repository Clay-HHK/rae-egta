"""Isolated EGTA audit backends with one non-throwing result contract."""

from __future__ import annotations

import http.client
import json
import os
import shlex
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from rae_egta.egta._judgment import (
    AUDIT_JUDGMENT_SCHEMA,
    JUDGMENT_SCHEMA_VERSION,
    normalize_audit_judgment_dates,
    validate_audit_judgment,
)


@dataclass(frozen=True)
class BackendResult:
    backend: str
    role: str
    raw_text: str
    raw_stream: str
    parsed_json: dict[str, Any] | None
    date_normalization: dict[str, Any] | None
    exit_code: int | None
    http_status: int | None
    duration_seconds: float
    retry_count: int
    resolved_model: str | None
    backend_version: str | None
    tool_call_count: int
    parse_failure: bool
    contract_violation: bool
    status: str
    stderr: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_object(
    raw_text: str,
) -> tuple[dict[str, Any] | None, bool, dict[str, Any] | None]:
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return None, True, None
    if not isinstance(payload, dict):
        return None, True, None
    normalized, date_normalization = normalize_audit_judgment_dates(payload)
    return normalized, False, date_normalization


def _result(
    *,
    backend: str,
    role: str,
    raw_text: str,
    raw_stream: str | None = None,
    started: float,
    retry_count: int,
    resolved_model: str | None,
    backend_version: str | None,
    tool_call_count: int = 0,
    exit_code: int | None = None,
    http_status: int | None = None,
    stderr: str = "",
    terminal_error: bool = False,
) -> BackendResult:
    parsed, parse_failure, date_normalization = _parse_object(raw_text)
    violation = tool_call_count > 0
    if terminal_error:
        status = "backend_failure"
    elif violation:
        status = "tool_contract_violation"
    elif parse_failure:
        status = "parse_failure"
    else:
        status = "ok"
    return BackendResult(
        backend=backend,
        role=role,
        raw_text=raw_text,
        raw_stream=raw_text if raw_stream is None else raw_stream,
        parsed_json=parsed,
        date_normalization=date_normalization,
        exit_code=exit_code,
        http_status=http_status,
        duration_seconds=max(0.0, time.monotonic() - started),
        retry_count=retry_count,
        resolved_model=resolved_model,
        backend_version=backend_version,
        tool_call_count=tool_call_count,
        parse_failure=parse_failure,
        contract_violation=violation,
        status=status,
        stderr=stderr,
    )


def _mock_call(role: str, prompt_text: str, config: Mapping[str, Any]) -> BackendResult:
    started = time.monotonic()
    response = config.get("mock_response")
    responses = config.get("mock_responses")
    item_id = str(config.get("item_id", "mock_item"))
    if isinstance(responses, Mapping) and item_id in responses:
        response = responses[item_id]
    factory = config.get("mock_response_factory")
    if callable(factory):
        response = factory(role, prompt_text, config)
    if response is None:
        response = {
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
            "rationale": "deterministic mock response",
        }
    raw = response if isinstance(response, str) else json.dumps(response, sort_keys=True, ensure_ascii=False)
    return _result(
        backend="mock",
        role=role,
        raw_text=raw,
        started=started,
        retry_count=0,
        resolved_model=str(config.get("model", "mock-model")),
        backend_version="mock-v1",
        tool_call_count=int(config.get("mock_tool_call_count", 0)),
        exit_code=0,
    )


def _command_tokens(template: Any, values: Mapping[str, str]) -> list[str]:
    tokens = shlex.split(template) if isinstance(template, str) else list(template)
    if not tokens or not all(isinstance(token, str) for token in tokens):
        raise ValueError("command_template must contain command tokens")
    return [token.format_map(values) for token in tokens]


@lru_cache(maxsize=4)
def _installed_version(executable: str) -> str:
    try:
        return subprocess.check_output(
            [executable, "--version"], text=True, stderr=subprocess.DEVNULL, timeout=10
        ).strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return "unknown"


def _codex_stream_metadata(raw: str) -> tuple[int, str | None]:
    tool_ids: set[str] = set()
    resolved_model: str | None = None
    tool_types = {"command_execution", "mcp_tool_call", "web_search", "file_change"}
    for index, line in enumerate(raw.splitlines()):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        model = event.get("model") or event.get("model_id")
        if isinstance(model, str):
            resolved_model = model
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") in tool_types:
            tool_ids.add(str(item.get("id", index)))
    return len(tool_ids), resolved_model


def _cli_call(backend: str, role: str, prompt_text: str, config: Mapping[str, Any]) -> BackendResult:
    started = time.monotonic()
    model = str(config.get("model") or ("claude-sonnet-5" if backend == "claude_cli" else "gpt-5.6-terra"))
    defaults = {
        "claude_cli": [
            "claude", "-p", "{prompt}", "--model", "{model}", "--tools", "",
            "--output-format", "json", "--json-schema", "{output_schema}", "--no-session-persistence",
            "--safe-mode", "--disable-slash-commands",
        ],
        "codex_cli": [
            # Retain user config for provider auth; ephemeral mode, read-only sandbox, and a temp cwd provide isolation.
            "codex", "exec", "--model", "{model}", "--sandbox", "read-only", "--ephemeral",
            "--skip-git-repo-check",
            "--output-schema", "{output_schema_path}",
            "--output-last-message", "{output_path}", "--json", "{prompt}",
        ],
    }
    retries = min(2, max(0, int(config.get("retries", 0))))
    timeout = float(config.get("timeout_seconds", 300.0))
    last_error = ""
    last_code: int | None = None
    raw = ""
    codex_last_message: str | None = None
    with tempfile.TemporaryDirectory(prefix="egta-codex-") if backend == "codex_cli" else _null_tempdir() as cwd:
        output_path = Path(cwd) / "last_message.json" if cwd else Path("last_message.json")
        output_schema_path = Path(cwd) / "output_schema.json" if cwd else Path("output_schema.json")
        if backend == "codex_cli":
            output_schema_path.write_text(json.dumps(AUDIT_JUDGMENT_SCHEMA), encoding="utf-8")
        values = {
            "prompt": prompt_text,
            "model": model,
            "role": role,
            "output_schema": json.dumps(AUDIT_JUDGMENT_SCHEMA, separators=(",", ":")),
            "output_schema_path": str(output_schema_path),
            "output_path": str(output_path),
            "cwd": str(cwd or ""),
        }
        command = _command_tokens(config.get("command_template", defaults[backend]), values)
        for attempt in range(retries + 1):
            try:
                completed = subprocess.run(
                    command,
                    cwd=cwd,
                    text=True,
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                    env=dict(os.environ),
                )
                raw = completed.stdout
                last_error = completed.stderr
                last_code = completed.returncode
                if completed.returncode == 0:
                    break
            except (OSError, subprocess.TimeoutExpired) as exc:
                last_error = str(exc)
                last_code = None
            if attempt == retries:
                return _result(
                    backend=backend, role=role, raw_text=raw, started=started,
                    raw_stream=raw,
                    retry_count=attempt, resolved_model=model,
                    backend_version=str(config.get("backend_version") or _installed_version(command[0])),
                    exit_code=last_code, stderr=last_error, terminal_error=True,
                )
        if backend == "codex_cli" and output_path.is_file():
            codex_last_message = output_path.read_text(encoding="utf-8")
    resolved = model
    tool_calls = 0
    assistant_text = raw
    if backend == "codex_cli":
        if codex_last_message is not None:
            assistant_text = codex_last_message
        tool_calls, stream_model = _codex_stream_metadata(raw)
        resolved = stream_model or model
    else:
        try:
            envelope = json.loads(raw)
            if isinstance(envelope, dict):
                if isinstance(envelope.get("structured_output"), dict):
                    assistant_text = json.dumps(envelope["structured_output"], ensure_ascii=False)
                elif isinstance(envelope.get("result"), str):
                    assistant_text = envelope["result"]
                model_usage = envelope.get("modelUsage")
                usage_models = list(model_usage) if isinstance(model_usage, dict) else []
                resolved = str(envelope.get("model") or (usage_models[0] if len(usage_models) == 1 else model))
                tool_calls = int(envelope.get("tool_call_count", 0))
        except json.JSONDecodeError:
            pass
    return _result(
        backend=backend, role=role, raw_text=assistant_text, raw_stream=raw, started=started,
        retry_count=attempt, resolved_model=resolved,
        backend_version=str(config.get("backend_version") or _installed_version(command[0])),
        tool_call_count=tool_calls, exit_code=last_code, stderr=last_error,
    )


class _null_tempdir:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: Any) -> None:
        return None


def _grok_call(role: str, prompt_text: str, config: Mapping[str, Any]) -> BackendResult:
    base = os.environ.get("GROK_API_BASE")
    key = os.environ.get("GROK_API_KEY")
    if not base or not key:
        raise EnvironmentError("GROK_API_BASE and GROK_API_KEY must both be set")
    started = time.monotonic()
    model = str(config.get("model", "grok-4.5-build-free"))
    retries = min(2, max(0, int(config.get("retries", 2))))
    body = json.dumps(
        {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": f"You are the fixed EGTA {role}. Return JSON only."},
                {"role": "user", "content": prompt_text},
            ],
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        base.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            # Cloudflare in front of the endpoint rejects the default
            # urllib user agent with error 1010.
            "User-Agent": str(config.get("user_agent", "egta-audit/1.0 (curl-compatible)")),
            "Accept": "application/json",
        },
        method="POST",
    )
    raw_error = ""
    status: int | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=float(config.get("timeout_seconds", 120.0))) as response:
                status = int(response.status)
                envelope = json.loads(response.read().decode("utf-8"))
            message = envelope["choices"][0]["message"]
            raw = message.get("content") or ""
            tool_calls = len(message.get("tool_calls") or [])
            return _result(
                backend="grok_http", role=role, raw_text=raw,
                raw_stream=json.dumps(envelope, sort_keys=True, ensure_ascii=False), started=started,
                retry_count=attempt, resolved_model=str(envelope.get("model") or model),
                backend_version=str(envelope.get("system_fingerprint") or "openai-compatible"),
                tool_call_count=tool_calls, http_status=status,
            )
        except urllib.error.HTTPError as exc:
            status = exc.code
            raw_error = exc.read().decode("utf-8", errors="replace")
            retryable = exc.code in {408, 429} or exc.code >= 500
            if not retryable:
                break
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            # RemoteDisconnected escapes urlopen unwrapped while the
            # response is being read; OSError covers the reset family.
            OSError,
            TimeoutError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
        ) as exc:
            raw_error = str(exc)
        if attempt == retries:
            break
    return _result(
        backend="grok_http", role=role, raw_text=raw_error, started=started,
        retry_count=attempt, resolved_model=model, backend_version="openai-compatible",
        http_status=status, stderr=raw_error, terminal_error=True,
    )


def run_audit_call(role: str, prompt_text: str, config: Mapping[str, Any]) -> BackendResult:
    """Dispatch one isolated call; JSON parse failures remain terminal records."""

    backend = str(config.get("backend", "mock"))
    if backend == "mock":
        return _mock_call(role, prompt_text, config)
    if backend in {"claude_cli", "codex_cli"}:
        return _cli_call(backend, role, prompt_text, config)
    if backend == "grok_http":
        return _grok_call(role, prompt_text, config)
    raise ValueError(f"unsupported EGTA backend: {backend!r}")
