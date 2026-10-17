from __future__ import annotations

import unittest
from collections import Counter

from rae_egta.rag_post_event.data import (
    ADD,
    CURRENT_VALID_STABLE,
    HISTORICAL,
    OBSOLETE_UNQUALIFIED,
    QUERY_TEMPLATE_VERSION,
    RETIRE,
    REVISE,
    TARGET,
    build_dataset,
)
from rae_egta.rag_post_event.subsample import (
    DEFAULT_PILOT_HOLDOUT_OFFSET,
    FORMAL_EVENT_COUNTS,
    FORMAL_SEEDS,
    PILOT_EVENT_COUNTS,
    SUBSAMPLE_PROTOCOL_VERSION,
    SUBSAMPLE_RANKING_NAMESPACE,
    select_headroom_pilot,
    select_subsample,
)


def source_records() -> tuple[dict[int, list[object]], dict[int, list[object]]]:
    events_by_seed: dict[int, list[object]] = {}
    queries_by_seed: dict[int, list[object]] = {}
    for seed in FORMAL_SEEDS:
        events, queries = build_dataset(
            event_count=80,
            seed=seed,
            stable_queries_per_event=4,
            add_fraction=0.10,
            revise_fraction=0.50,
            retire_fraction=0.40,
        )
        events_by_seed[seed] = events
        queries_by_seed[seed] = queries
    return events_by_seed, queries_by_seed


class SubsampleTests(unittest.TestCase):
    def test_formal_stratification_and_query_counts(self) -> None:
        events_by_seed, queries_by_seed = source_records()
        selection = select_subsample(events_by_seed, queries_by_seed)
        self.assertEqual(120, len(selection.event_keys))
        self.assertEqual(576, len(selection.query_keys))
        for seed in FORMAL_SEEDS:
            selected = [item for item in selection.selected_events if item.seed == seed]
            self.assertEqual(FORMAL_EVENT_COUNTS, Counter(item.event.event_type for item in selected))
            for item in selected:
                query_types = Counter(query.query_type for query in item.queries)
                self.assertEqual(1, query_types[TARGET])
                self.assertEqual(2, query_types[CURRENT_VALID_STABLE])
                if item.event.event_type in {REVISE, RETIRE}:
                    self.assertEqual(1, query_types[OBSOLETE_UNQUALIFIED])
                    self.assertEqual(1, query_types[HISTORICAL])
                    self.assertEqual(5, len(item.queries))
                else:
                    self.assertEqual(ADD, item.event.event_type)
                    self.assertEqual(3, len(item.queries))

    def test_selection_and_hash_are_stable_under_input_reordering(self) -> None:
        events_by_seed, queries_by_seed = source_records()
        first = select_subsample(events_by_seed, queries_by_seed)
        reversed_events = {seed: list(reversed(events)) for seed, events in events_by_seed.items()}
        reversed_queries = {seed: list(reversed(queries)) for seed, queries in queries_by_seed.items()}
        second = select_subsample(reversed_events, reversed_queries)
        self.assertEqual(first.event_keys, second.event_keys)
        self.assertEqual(first.query_keys, second.query_keys)
        self.assertEqual(first.subsample_hash, second.subsample_hash)
        self.assertEqual(64, len(first.subsample_hash))

    def test_headroom_pilot_is_nested_and_stratified(self) -> None:
        events_by_seed, queries_by_seed = source_records()
        formal = select_subsample(events_by_seed, queries_by_seed)
        pilot = select_headroom_pilot(formal, holdout_offset=0)
        self.assertEqual(12, len(pilot.event_keys))
        self.assertEqual(58, len(pilot.query_keys))
        self.assertEqual(
            PILOT_EVENT_COUNTS,
            Counter(item.event.event_type for item in pilot.selected_events),
        )
        self.assertTrue(set(pilot.event_keys).issubset(formal.event_keys))
        self.assertTrue(set(pilot.query_keys).issubset(formal.query_keys))
        self.assertNotEqual(formal.subsample_hash, pilot.subsample_hash)

    def test_fresh_holdout_block_is_deterministic_and_disjoint_from_original_pilot(self) -> None:
        events_by_seed, queries_by_seed = source_records()
        formal = select_subsample(events_by_seed, queries_by_seed)
        original = select_headroom_pilot(formal, holdout_offset=0)
        fresh_first = select_headroom_pilot(
            formal,
            holdout_offset=DEFAULT_PILOT_HOLDOUT_OFFSET,
        )
        fresh_second = select_headroom_pilot(
            formal,
            holdout_offset=DEFAULT_PILOT_HOLDOUT_OFFSET,
        )

        self.assertEqual("controlled_llm_subsample_v1", SUBSAMPLE_RANKING_NAMESPACE)
        self.assertEqual(1, DEFAULT_PILOT_HOLDOUT_OFFSET)
        self.assertTrue(set(original.event_keys).isdisjoint(fresh_first.event_keys))
        self.assertTrue(set(original.query_keys).isdisjoint(fresh_first.query_keys))
        self.assertEqual(fresh_first.event_keys, fresh_second.event_keys)
        self.assertEqual(fresh_first.query_keys, fresh_second.query_keys)
        self.assertEqual(fresh_first.subsample_hash, fresh_second.subsample_hash)

        manifest = fresh_first.to_manifest()
        self.assertEqual(SUBSAMPLE_PROTOCOL_VERSION, manifest["protocol_version"])
        self.assertEqual(QUERY_TEMPLATE_VERSION, manifest["query_template_version"])
        self.assertEqual(1, manifest["holdout_offset"])
        self.assertEqual(12, manifest["event_count"])
        self.assertEqual(
            list(fresh_first.event_keys),
            [
                (int(item["seed"]), int(item["event_id"]))
                for item in manifest["selected_events"]
            ],
        )

        with self.assertRaisesRegex(ValueError, "non-negative"):
            select_headroom_pilot(formal, holdout_offset=-1)


if __name__ == "__main__":
    unittest.main()
