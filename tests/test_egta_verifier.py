from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from rae_egta.egta.runner import write_json, write_jsonl
from rae_egta.egta.schema import assert_blind, to_blind_view
from rae_egta.egta.verifier import build_disagreement_queue, run_verifier, trigger_reasons
from tests.egta_fixtures import judgment, migrated_record, result


class EGTAVerifierTest(unittest.TestCase):
    def test_queue_trigger_condition_matrix(self) -> None:
        item_id = "egta_fixture"
        base_a = result(judgment(item_id))
        base_b = result(judgment(item_id))
        self.assertEqual([], trigger_reasons(base_a, base_b))

        cases: list[tuple[str, str, object]] = []
        changed = copy.deepcopy(base_b)
        changed["judgment"]["temporal_scope"] = "uncertain"
        cases.append(("scope", "temporal_scope_disagreement", changed))
        changed = copy.deepcopy(base_b)
        changed["judgment"]["verdict"] = "still_valid"
        cases.append(("verdict", "verdict_disagreement", changed))
        changed = copy.deepcopy(base_b)
        changed["judgment"].update({"verdict": "superseded_revision", "event_type": "retirement"})
        left = copy.deepcopy(base_a)
        left["judgment"].update({"verdict": "superseded_revision", "event_type": "revision"})
        cases.append(("event_type", "revision_retirement_event_type_disagreement", (left, changed)))
        changed = copy.deepcopy(base_b)
        changed["judgment"]["proposed_current_answer"] = {"state": "value", "canonical": "new"}
        cases.append(("current_answer", "current_answer_incompatible", changed))
        changed = copy.deepcopy(base_b)
        changed["judgment"]["supporting_evidence_ids"] = ["src-1"]
        cases.append(("sources", "supporting_evidence_disagreement", changed))
        changed = copy.deepcopy(base_b)
        changed["judgment"]["verdict"] = "ambiguous_context"
        cases.append(("ambiguity", "auditor_ambiguity_or_conflict", changed))
        changed = result(judgment(item_id), valid=False)
        cases.append(("parse", "auditor_b_parse_failure", changed))
        changed = copy.deepcopy(base_b)
        changed["status"]["evidence_contract_valid"] = False
        cases.append(("evidence", "auditor_b_evidence_contract_failure", changed))

        for label, expected, changed in cases:
            with self.subTest(label=label):
                left, right = (changed if isinstance(changed, tuple) else (base_a, changed))
                self.assertIn(expected, trigger_reasons(left, right))

    def _write_auditor_dir(
        self,
        root: Path,
        role: str,
        row: dict[str, object],
        request: dict[str, object],
    ) -> Path:
        output = root / role
        item_id = str(row["item_id"])
        (output / item_id).mkdir(parents=True)
        write_json(output / item_id / "blind_request.json", request)
        write_jsonl(output / "results.jsonl", [row])
        return output

    def test_verifier_receives_only_blind_projection(self) -> None:
        record = migrated_record(1)
        request = to_blind_view(record)
        item_id = record["item_id"]
        a = result(judgment(item_id))
        b_judgment = judgment(
            item_id,
            verdict="still_valid",
            event_type="none",
            proposed_current_answer={"state": "value", "canonical": record["frozen_answer"]["raw"]},
        )
        b = result(b_judgment)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a_dir = self._write_auditor_dir(root, "auditor_a", a, request)
            b_dir = self._write_auditor_dir(root, "auditor_b", b, request)
            queue = root / "queue.jsonl"
            queue_manifest = build_disagreement_queue(auditor_a=a_dir, auditor_b=b_dir, output=queue)
            self.assertEqual(1, queue_manifest["queue_item_count"])
            output = root / "verifier"
            verifier_judgment = judgment(
                item_id,
                change_date={"earliest": "2024-02", "latest": "2024-02", "precision": "month"},
            )
            run_verifier(
                queue_path=queue,
                backend="mock",
                model="mock",
                output_dir=output,
                config={"mock_response": verifier_judgment},
            )
            saved = json.loads((output / item_id / "blind_request.json").read_text(encoding="utf-8"))
            parsed = json.loads((output / item_id / "parsed_output.json").read_text(encoding="utf-8"))
            assert_blind(saved)
            prompt = (output / item_id / "prompt.md").read_text(encoding="utf-8")
            status = json.loads((output / item_id / "status.json").read_text(encoding="utf-8"))
            self.assertNotIn("verdict_disagreement", prompt)
            self.assertNotIn("legacy hidden", prompt)
            self.assertNotIn("SUPERSEDED_CONFIRMED", prompt)
            self.assertEqual(set(request), set(saved))
            self.assertEqual(
                {"earliest": "2024-02", "latest": "2024-02", "precision": "month"},
                status["date_normalization"]["raw"],
            )
            self.assertEqual(
                {"earliest": "2024-02-01", "latest": "2024-02-29", "precision": "month"},
                parsed["change_date"],
            )


if __name__ == "__main__":
    unittest.main()
