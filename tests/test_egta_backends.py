from __future__ import annotations

import json
import os
import subprocess
import unittest
import urllib.error
from unittest import mock

from rae_egta.egta.backends import (
    AUDIT_JUDGMENT_SCHEMA,
    BackendResult,
    run_audit_call,
    validate_audit_judgment,
)
from tests.egta_fixtures import judgment


class _HTTPResponse:
    def __init__(self, payload: dict[str, object], status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_HTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


class EGTABackendsTest(unittest.TestCase):
    def assert_openai_strict_schema(self, schema: object, path: str = "$") -> None:
        if isinstance(schema, list):
            for index, child in enumerate(schema):
                self.assert_openai_strict_schema(child, f"{path}[{index}]")
            return
        if not isinstance(schema, dict):
            return

        schema_type = schema.get("type")
        is_object = schema_type == "object" or (
            isinstance(schema_type, list) and "object" in schema_type
        )
        properties = schema.get("properties")
        if is_object or properties is not None:
            self.assertIsInstance(properties, dict, path)
            self.assertTrue(is_object, path)
            self.assertIs(False, schema.get("additionalProperties"), path)
            self.assertEqual(set(properties), set(schema.get("required", [])), path)
            for name, property_schema in properties.items():
                property_path = f"{path}.properties.{name}"
                self.assertIsInstance(property_schema, dict, property_path)
                self.assertIn("type", property_schema, property_path)
                self.assert_openai_strict_schema(property_schema, property_path)

        for keyword in ("items", "anyOf", "allOf", "oneOf"):
            if keyword in schema:
                self.assert_openai_strict_schema(schema[keyword], f"{path}.{keyword}")

    def test_audit_judgment_schema_is_openai_strict_compatible(self) -> None:
        self.assert_openai_strict_schema(AUDIT_JUDGMENT_SCHEMA)

    def test_backend_result_contains_full_contract(self) -> None:
        payload = judgment("egta_fixture")
        result = run_audit_call(
            "auditor_a",
            "prompt",
            {"backend": "mock", "model": "mock-fixed", "item_id": "egta_fixture", "mock_response": payload},
        )
        self.assertIsInstance(result, BackendResult)
        self.assertEqual(
            {
                "backend", "role", "raw_text", "raw_stream", "parsed_json", "date_normalization",
                "exit_code", "http_status",
                "duration_seconds", "retry_count", "resolved_model", "backend_version",
                "tool_call_count", "parse_failure", "contract_violation", "status", "stderr",
            },
            set(result.to_dict()),
        )
        self.assertEqual(payload, result.parsed_json)
        self.assertEqual("ok", result.status)

    def test_year_precision_dates_are_normalized_before_validation(self) -> None:
        payload = judgment(
            "egta_fixture",
            change_date={"earliest": "2024", "latest": "2025", "precision": "year"},
        )
        result = run_audit_call(
            "auditor_a",
            "prompt",
            {"backend": "mock", "item_id": "egta_fixture", "mock_response": payload},
        )
        self.assertEqual(
            {"earliest": "2024-01-01", "latest": "2025-12-31", "precision": "year"},
            result.parsed_json["change_date"],
        )
        self.assertEqual(payload["change_date"], result.date_normalization["raw"])
        self.assertTrue(result.date_normalization["applied"])
        self.assertEqual([], validate_audit_judgment(result.parsed_json, expected_item_id="egta_fixture"))

    def test_year_month_dates_use_first_and_last_day_before_validation(self) -> None:
        payload = judgment(
            "egta_fixture",
            change_date={"earliest": "2024-02", "latest": "2024-02", "precision": "month"},
        )
        result = run_audit_call(
            "verifier",
            "prompt",
            {"backend": "mock", "item_id": "egta_fixture", "mock_response": payload},
        )
        self.assertEqual(
            {"earliest": "2024-02-01", "latest": "2024-02-29", "precision": "month"},
            result.parsed_json["change_date"],
        )
        self.assertEqual(payload["change_date"], result.date_normalization["raw"])
        self.assertEqual([], validate_audit_judgment(result.parsed_json, expected_item_id="egta_fixture"))

    def test_invalid_lower_precision_dates_remain_schema_errors(self) -> None:
        payload = judgment(
            "egta_fixture",
            change_date={"earliest": "2024-13", "latest": "not-a-date", "precision": "month"},
        )
        result = run_audit_call(
            "auditor_b",
            "prompt",
            {"backend": "mock", "item_id": "egta_fixture", "mock_response": payload},
        )
        self.assertEqual(payload["change_date"], result.parsed_json["change_date"])
        self.assertEqual(payload["change_date"], result.date_normalization["raw"])
        self.assertFalse(result.date_normalization["applied"])
        self.assertEqual(
            [
                "change_date.earliest must be an ISO date or null",
                "change_date.latest must be an ISO date or null",
            ],
            validate_audit_judgment(result.parsed_json, expected_item_id="egta_fixture"),
        )

    def test_parse_failure_is_recorded_without_raising(self) -> None:
        result = run_audit_call(
            "auditor_b",
            "prompt",
            {"backend": "mock", "item_id": "egta_fixture", "mock_response": "```json\n{}\n```"},
        )
        self.assertTrue(result.parse_failure)
        self.assertIsNone(result.parsed_json)
        self.assertEqual("parse_failure", result.status)

    def test_tool_calls_are_a_contract_violation(self) -> None:
        result = run_audit_call(
            "auditor_a",
            "prompt",
            {
                "backend": "mock",
                "item_id": "egta_fixture",
                "mock_response": judgment("egta_fixture"),
                "mock_tool_call_count": 1,
            },
        )
        self.assertTrue(result.contract_violation)
        self.assertEqual("tool_contract_violation", result.status)

    def test_grok_requires_both_environment_variables(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(EnvironmentError):
                run_audit_call("verifier", "prompt", {"backend": "grok_http"})

    def test_grok_retry_is_capped_at_two_retries(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"GROK_API_BASE": "https://example.test", "GROK_API_KEY": "fixture-only"},
            clear=True,
        ), mock.patch(
            "rae_egta.egta.backends.urllib.request.urlopen",
            side_effect=urllib.error.URLError("offline"),
        ) as urlopen:
            result = run_audit_call(
                "verifier",
                "prompt",
                {"backend": "grok_http", "retries": 99, "timeout_seconds": 0.01},
            )
        self.assertEqual(3, urlopen.call_count)
        self.assertEqual(2, result.retry_count)
        self.assertEqual("backend_failure", result.status)

    def test_grok_parses_openai_compatible_response_and_tool_count(self) -> None:
        payload = judgment("egta_fixture")
        response = {
            "model": "grok-resolved",
            "system_fingerprint": "endpoint-v1",
            "choices": [{"message": {"content": json.dumps(payload), "tool_calls": []}}],
        }
        with mock.patch.dict(
            os.environ,
            {"GROK_API_BASE": "https://example.test/base", "GROK_API_KEY": "fixture-only"},
            clear=True,
        ), mock.patch(
            "rae_egta.egta.backends.urllib.request.urlopen",
            return_value=_HTTPResponse(response),
        ) as urlopen:
            result = run_audit_call("verifier", "prompt", {"backend": "grok_http", "retries": 0})
        request = urlopen.call_args.args[0]
        self.assertEqual("https://example.test/base/v1/chat/completions", request.full_url)
        self.assertEqual("grok-resolved", result.resolved_model)
        self.assertEqual(payload, result.parsed_json)

    def test_cli_template_uses_no_shell_and_codex_isolated_cwd(self) -> None:
        payload = judgment("egta_fixture")
        completed = subprocess.CompletedProcess(["fake"], 0, json.dumps(payload), "")
        with mock.patch("rae_egta.egta.backends.subprocess.run", return_value=completed) as run:
            result = run_audit_call(
                "auditor_b",
                "prompt text",
                {
                    "backend": "codex_cli",
                    "model": "gpt-fixture",
                    "command_template": ["fake", "{model}", "{prompt}"],
                    "backend_version": "fixture-cli",
                },
            )
        kwargs = run.call_args.kwargs
        self.assertIsNotNone(kwargs["cwd"])
        self.assertFalse(kwargs["check"])
        self.assertEqual(payload, result.parsed_json)

    def test_default_codex_command_reads_last_message_and_counts_stream_tools(self) -> None:
        payload = judgment("egta_fixture")

        def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            output_path = command[command.index("--output-last-message") + 1]
            with open(output_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            stream = json.dumps(
                {"type": "item.started", "item": {"id": "tool-1", "type": "command_execution"}}
            )
            return subprocess.CompletedProcess(command, 0, stream, "")

        with mock.patch("rae_egta.egta.backends.subprocess.run", side_effect=fake_run) as run:
            result = run_audit_call(
                "auditor_b",
                "prompt text",
                {"backend": "codex_cli", "model": "gpt-fixture", "backend_version": "fixture-cli"},
            )
        command = run.call_args.args[0]
        self.assertIn("--sandbox", command)
        self.assertIn("read-only", command)
        self.assertIn("--ephemeral", command)
        self.assertNotIn("--ignore-user-config", command)
        self.assertNotIn("--ignore-rules", command)
        self.assertIn("--output-schema", command)
        self.assertEqual(payload, result.parsed_json)
        self.assertEqual(1, result.tool_call_count)
        self.assertEqual("tool_contract_violation", result.status)

    def test_claude_json_envelope_is_unwrapped(self) -> None:
        payload = judgment("egta_fixture")
        envelope = {"result": json.dumps(payload), "model": "claude-resolved", "tool_call_count": 0}
        completed = subprocess.CompletedProcess(["fake"], 0, json.dumps(envelope), "")
        with mock.patch("rae_egta.egta.backends.subprocess.run", return_value=completed):
            result = run_audit_call(
                "auditor_a",
                "prompt text",
                {"backend": "claude_cli", "command_template": ["fake"], "backend_version": "fixture-cli"},
            )
        self.assertEqual("claude-resolved", result.resolved_model)
        self.assertEqual(payload, result.parsed_json)


if __name__ == "__main__":
    unittest.main()
