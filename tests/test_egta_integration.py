from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import json

from rae_egta.egta.aggregate import aggregate_run
from rae_egta.egta.evidence import build_evidence_package, sha256_path, write_evidence_package
from rae_egta.egta.migrate import migrate_annotations
from rae_egta.egta.rae_adapter import adapt_records
from rae_egta.egta.runner import load_jsonl, run_auditor
from rae_egta.egta.verifier import build_disagreement_queue, run_verifier
from tests.egta_fixtures import judgment


class EGTAIntegrationTest(unittest.TestCase):
    def test_mock_full_chain_from_migration_through_rae_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            migrate_annotations(
                run_id="r0_mock",
                output_root=root,
                git={"git_sha": "4" * 40, "git_dirty": False},
                created_at_utc="2026-07-22T00:00:00+00:00",
            )
            run_root = root / "r0_mock"
            canonical = run_root / "input/canonical_records.jsonl"
            blind = run_root / "input/blind_candidates.jsonl"
            evidence_root = run_root / "evidence"
            evidence_root.mkdir()
            blind_rows = load_jsonl(blind)
            for row in blind_rows:
                write_evidence_package(
                    evidence_root / f"{row['item_id']}.json",
                    build_evidence_package(row["item_id"], []),
                )
            first_id = blind_rows[0]["item_id"]
            current_answer = blind_rows[0]["frozen_answer"]["raw"]
            b_override = judgment(
                first_id,
                verdict="still_valid",
                event_type="none",
                proposed_current_answer={"state": "value", "canonical": current_answer},
            )
            verifier_override = judgment(
                first_id,
                verdict="still_valid",
                event_type="none",
                proposed_current_answer={"state": "value", "canonical": current_answer},
            )
            auditors = run_root / "auditors"
            run_auditor(
                input_path=blind,
                evidence_root=evidence_root,
                backend="mock",
                model="mock-a",
                role="auditor_a",
                output_dir=auditors / "auditor_a",
            )
            run_auditor(
                input_path=blind,
                evidence_root=evidence_root,
                backend="mock",
                model="mock-b",
                role="auditor_b",
                output_dir=auditors / "auditor_b",
                config={"mock_responses": {first_id: b_override}},
            )
            run_auditor(
                input_path=blind,
                evidence_root=evidence_root,
                backend="mock",
                model="mock-a",
                role="auditor_a_repeat",
                repeat_of="auditor_a",
                output_dir=auditors / "auditor_a_repeat",
            )
            queue = run_root / "verifier_queue.jsonl"
            queue_manifest = build_disagreement_queue(
                auditor_a=auditors / "auditor_a",
                auditor_b=auditors / "auditor_b",
                output=queue,
            )
            self.assertEqual(1, queue_manifest["queue_item_count"])
            verifier_dir = run_root / "verifier"
            verifier_manifest = run_verifier(
                queue_path=queue,
                backend="mock",
                model="mock-v",
                output_dir=verifier_dir,
                config={"mock_responses": {first_id: verifier_override}},
            )
            self.assertEqual(1, verifier_manifest["terminal_item_count"])
            run_manifest = json.loads((run_root / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                {"auditor_a", "auditor_b", "auditor_a_repeat"},
                set(run_manifest["egta_snapshots"]["auditors"]),
            )
            self.assertIn("audit/", run_manifest["git_dirty_ignored_prefixes"])

            aggregate_dir = run_root / "aggregate"
            summary = aggregate_run(
                records_path=canonical,
                evidence_root=evidence_root,
                auditor_a=auditors / "auditor_a",
                auditor_b=auditors / "auditor_b",
                auditor_a_repeat=auditors / "auditor_a_repeat",
                verifier=verifier_dir,
                output_dir=aggregate_dir,
            )
            self.assertEqual(47, summary["unique_item_count"])
            self.assertEqual(47, summary["terminal_item_count"])
            expected_products = {
                "final_records.jsonl",
                "egta_verdict_distribution.csv",
                "egta_auditor_agreement.csv",
                "egta_verifier_trigger.csv",
                "egta_abstention.csv",
                "egta_source_quality.csv",
                "egta_source_quality_summary.csv",
                "egta_stability.csv",
                "egta_item_accounting.csv",
                "egta_summary.json",
                "analysis_manifest.json",
                "egta_report.md",
            }
            self.assertTrue(expected_products.issubset({path.name for path in aggregate_dir.iterdir()}))
            adapter_summary = adapt_records(
                records_path=aggregate_dir / "final_records.jsonl",
                output_dir=aggregate_dir / "rae_partition",
            )
            self.assertEqual(47, adapter_summary["unique_item_count"])
            self.assertEqual(47, adapter_summary["unresolved_count"])

            second_aggregate = run_root / "aggregate_replay"
            aggregate_run(
                records_path=canonical,
                evidence_root=evidence_root,
                auditor_a=auditors / "auditor_a",
                auditor_b=auditors / "auditor_b",
                auditor_a_repeat=auditors / "auditor_a_repeat",
                verifier=verifier_dir,
                output_dir=second_aggregate,
            )
            self.assertEqual(
                sha256_path(aggregate_dir / "final_records.jsonl"),
                sha256_path(second_aggregate / "final_records.jsonl"),
            )
            self.assertEqual(
                sha256_path(aggregate_dir / "egta_summary.json"),
                sha256_path(second_aggregate / "egta_summary.json"),
            )


if __name__ == "__main__":
    unittest.main()
