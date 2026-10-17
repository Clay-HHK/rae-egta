from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from rae_egta.egta.migrate import (
    MIRROR_ANNOTATIONS,
    SOURCE_ANNOTATIONS,
    migrate_annotations,
    migrate_legacy_record,
)
from rae_egta.egta.schema import assert_blind


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def all_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(key)
            keys.update(all_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(all_keys(child))
    return keys


class EGTAMigrateTest(unittest.TestCase):
    def test_all_47_records_migrate_with_stable_unique_ids_and_blind_views(self) -> None:
        fake_git = {"git_sha": "c" * 40, "git_dirty": True, "git_status_short": "fixture"}
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            first = migrate_annotations(
                run_id="fixture-a",
                output_root=output_root,
                git=fake_git,
                created_at_utc="2026-07-22T00:00:00+00:00",
            )
            second = migrate_annotations(
                run_id="fixture-b",
                output_root=output_root,
                git=fake_git,
                created_at_utc="2026-07-22T00:00:00+00:00",
            )
            first_records = load_jsonl(output_root / "fixture-a/input/canonical_records.jsonl")
            second_records = load_jsonl(output_root / "fixture-b/input/canonical_records.jsonl")
            blind = load_jsonl(output_root / "fixture-a/input/blind_candidates.jsonl")

            self.assertEqual(47, len(first_records))
            self.assertEqual(47, len(blind))
            self.assertEqual(47, len({row["item_id"] for row in first_records}))
            self.assertEqual(
                [row["item_id"] for row in first_records],
                [row["item_id"] for row in second_records],
            )
            self.assertEqual(47, first["unique_item_count"])
            self.assertEqual(first["input_sha256"], second["input_sha256"])

            forbidden = {
                "acceptance",
                "auditor_agreement",
                "current_answer",
                "current_answer_2026",
                "legacy",
                "legacy_current_answer",
                "legacy_note",
                "legacy_verdict",
                "note",
                "proposed_current_answer",
                "verdict",
                "verification_status",
            }
            for view in blind:
                assert_blind(view)
                self.assertTrue(forbidden.isdisjoint(all_keys(view)), view["item_id"])
                self.assertNotIn("provenance", view)

    def test_dup_idxs_are_preserved_exactly(self) -> None:
        source = json.loads(SOURCE_ANNOTATIONS.read_text(encoding="utf-8"))
        expected = {row["idx"]: row["dup_idxs"] for row in source}
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            migrate_annotations(
                run_id="dup-lineage",
                output_root=output_root,
                git={"git_sha": "d" * 40, "git_dirty": False},
                created_at_utc="2026-07-22T00:00:00+00:00",
            )
            records = load_jsonl(output_root / "dup-lineage/input/canonical_records.jsonl")
        observed = {row["legacy"]["primary_idx"]: row["legacy"]["dup_idxs"] for row in records}
        self.assertEqual(expected, observed)

    def test_item_id_ignores_idx_and_normalizes_pair_text(self) -> None:
        row = json.loads(SOURCE_ANNOTATIONS.read_text(encoding="utf-8"))[0]
        kwargs = {
            "input_sha256": "e" * 64,
            "git": {"git_sha": "f" * 40, "git_dirty": False},
            "created_at_utc": "2026-07-22T00:00:00+00:00",
        }
        original = migrate_legacy_record(row, **kwargs)
        changed = dict(row)
        changed["idx"] = 999999
        changed["dup_idxs"] = [999999]
        changed["loc"] = "  " + row["loc"].upper() + "  "
        changed["loc_ans"] = "  " + row["loc_ans"].upper() + "  "
        normalized = migrate_legacy_record(changed, **kwargs)
        self.assertEqual(original["item_id"], normalized["item_id"])

    def test_manifest_records_matching_source_and_mirror_hashes(self) -> None:
        expected_hash = hashlib.sha256(SOURCE_ANNOTATIONS.read_bytes()).hexdigest()
        self.assertEqual(SOURCE_ANNOTATIONS.read_bytes(), MIRROR_ANNOTATIONS.read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            manifest = migrate_annotations(
                run_id="manifest",
                output_root=output_root,
                git={"git_sha": "1" * 40, "git_dirty": False},
                created_at_utc="2026-07-22T00:00:00+00:00",
            )
            on_disk = json.loads((output_root / "manifest/run_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(expected_hash, manifest["input_sha256"])
        self.assertEqual(expected_hash, manifest["mirror_sha256"])
        self.assertTrue(manifest["source_mirror_hash_match"])
        self.assertEqual(manifest, on_disk)
        self.assertEqual("1" * 40, manifest["git_sha"])
        self.assertFalse(manifest["git_dirty"])

    def test_migration_refuses_mismatched_annotation_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.json"
            mirror = root / "mirror.json"
            source.write_bytes(SOURCE_ANNOTATIONS.read_bytes())
            mirror.write_text("[]\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                migrate_annotations(
                    run_id="mismatch",
                    source_path=source,
                    mirror_path=mirror,
                    output_root=root / "out",
                    git={"git_sha": "2" * 40, "git_dirty": False},
                )


if __name__ == "__main__":
    unittest.main()
