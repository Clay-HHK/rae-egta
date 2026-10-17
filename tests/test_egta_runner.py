from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rae_egta.egta.evidence import build_evidence_package, write_evidence_package
from rae_egta.egta.migrate import migrate_annotations
from rae_egta.egta.runner import load_jsonl, run_auditor
from rae_egta.egta.schema import assert_blind
from tests.egta_fixtures import judgment


class EGTARunnerTest(unittest.TestCase):
    def _prepared_run(self, root: Path) -> tuple[Path, Path]:
        migrate_annotations(
            run_id="fixture",
            output_root=root,
            git={"git_sha": "3" * 40, "git_dirty": False},
            created_at_utc="2026-07-22T00:00:00+00:00",
        )
        run_root = root / "fixture"
        blind_path = run_root / "input/blind_candidates.jsonl"
        evidence_root = run_root / "evidence"
        evidence_root.mkdir()
        for row in load_jsonl(blind_path):
            package = build_evidence_package(row["item_id"], [])
            write_evidence_package(evidence_root / f"{row['item_id']}.json", package)
        return blind_path, evidence_root

    def test_mock_backend_runs_all_47_and_persists_complete_item_artifacts(self) -> None:
        def year_precision_response(
            _role: str, _prompt: str, config: dict[str, object]
        ) -> dict[str, object]:
            return judgment(
                str(config["item_id"]),
                change_date={"earliest": "2024", "latest": "2025", "precision": "year"},
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blind_path, evidence_root = self._prepared_run(root)
            output = root / "fixture/auditors/auditor_a"
            manifest = run_auditor(
                input_path=blind_path,
                evidence_root=evidence_root,
                backend="mock",
                model="mock-model",
                role="auditor_a",
                output_dir=output,
                config={"mock_response_factory": year_precision_response},
            )
            results = load_jsonl(output / "results.jsonl")
            self.assertEqual(47, manifest["item_count"])
            self.assertEqual({"ok": 47}, manifest["status_counts"])
            self.assertEqual(47, len(results))
            self.assertEqual(1, len({row["prompt_template_sha256"] for row in results}))
            required = {
                "blind_request.json", "prompt.md", "raw_output.txt", "stderr.txt",
                "parsed_output.json", "status.json", "hashes.json",
            }
            for row in results:
                item_dir = output / row["item_id"]
                self.assertTrue(required.issubset({path.name for path in item_dir.iterdir()}))
                request = json.loads((item_dir / "blind_request.json").read_text(encoding="utf-8"))
                parsed = json.loads((item_dir / "parsed_output.json").read_text(encoding="utf-8"))
                status = json.loads((item_dir / "status.json").read_text(encoding="utf-8"))
                assert_blind(request)
                self.assertEqual(
                    {"earliest": "2024", "latest": "2025", "precision": "year"},
                    status["date_normalization"]["raw"],
                )
                self.assertEqual(
                    {"earliest": "2024-01-01", "latest": "2025-12-31", "precision": "year"},
                    parsed["change_date"],
                )
                prompt = (item_dir / "prompt.md").read_text(encoding="utf-8")
                self.assertNotIn("legacy hidden", prompt)
                self.assertNotIn("legacy_verdict", prompt)

    def test_blind_assertion_rejects_leaked_input_before_any_backend_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blind_path, evidence_root = self._prepared_run(root)
            rows = load_jsonl(blind_path)
            rows[0]["legacy"] = {"legacy_verdict": "SUPERSEDED_CONFIRMED"}
            leaked = root / "leaked.jsonl"
            leaked.write_text(
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
                encoding="utf-8",
            )
            with self.assertRaises(AssertionError):
                run_auditor(
                    input_path=leaked,
                    evidence_root=evidence_root,
                    backend="mock",
                    model="mock-model",
                    role="auditor_a",
                    output_dir=root / "leaked-output",
                )

    def test_corrupt_evidence_hash_becomes_a_terminal_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            blind_path, evidence_root = self._prepared_run(root)
            rows = load_jsonl(blind_path)
            one_input = root / "one.jsonl"
            one_input.write_text(json.dumps(rows[0], sort_keys=True) + "\n", encoding="utf-8")
            evidence_path = evidence_root / f"{rows[0]['item_id']}.json"
            package = json.loads(evidence_path.read_text(encoding="utf-8"))
            package["package_sha256"] = "0" * 64
            evidence_path.write_text(json.dumps(package), encoding="utf-8")
            output = root / "bad-output"
            manifest = run_auditor(
                input_path=one_input,
                evidence_root=evidence_root,
                backend="mock",
                model="mock-model",
                role="auditor_a",
                output_dir=output,
            )
            self.assertEqual({"evidence_contract_failure": 1}, manifest["status_counts"])
            status = json.loads((output / rows[0]["item_id"] / "status.json").read_text())
            self.assertFalse(status["evidence_contract_valid"])


if __name__ == "__main__":
    unittest.main()
