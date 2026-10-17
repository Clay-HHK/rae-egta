#!/usr/bin/env python3
"""Frozen deterministic subsampling for the controlled-corpus LLM block.

Membership is selected only from immutable identifiers and a protocol-version
namespace.  Reader outputs, retrieval scores, methods, and arm configuration
are deliberately absent from both selection and hashing, so every
method/retriever/reader arm can share the exact same paired substrate.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Mapping, Sequence

from rae_egta.rag_post_event.data import (
    ADD,
    CURRENT_VALID_STABLE,
    HISTORICAL,
    OBSOLETE_UNQUALIFIED,
    RETIRE,
    REVISE,
    TARGET,
    QUERY_TEMPLATE_VERSION,
    KnowledgeEvent,
    Query,
)


SUBSAMPLE_PROTOCOL_VERSION = "controlled_llm_subsample_v2"
# Keep the v1 ranking namespace so the formal membership and offset-0 pilot
# exactly reproduce the v1/v2 selection.  The v2 protocol adds explicit query
# surface and holdout-block provenance without silently re-ranking candidates.
SUBSAMPLE_RANKING_NAMESPACE = "controlled_llm_subsample_v1"
PILOT_HOLDOUT_SELECTION_VERSION = "controlled_llm_pilot_holdout_blocks_v1"
FORMAL_EVENT_COUNTS = {ADD: 4, REVISE: 20, RETIRE: 16}
PILOT_EVENT_COUNTS = {ADD: 1, REVISE: 6, RETIRE: 5}
FORMAL_SEEDS = (13, 21, 34)
PILOT_SEED = 13
DEFAULT_PILOT_HOLDOUT_OFFSET = 1
STABLE_QUERIES_PER_EVENT = 2


def _hash_key(*parts: object) -> str:
    payload = "::".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SelectedEvent:
    """One selected event and its frozen query membership."""

    seed: int
    event: KnowledgeEvent
    queries: tuple[Query, ...]

    def canonical_record(self) -> dict[str, object]:
        return {
            "seed": self.seed,
            "event_id": self.event.event_id,
            "event_type": self.event.event_type,
            "queries": [
                {"query_id": query.query_id, "query_type": query.query_type}
                for query in self.queries
            ],
        }


@dataclass(frozen=True)
class SubsampleSelection:
    """Immutable paired event/query selection shared by all experiment arms."""

    mode: str
    selected_events: tuple[SelectedEvent, ...]
    protocol_version: str = SUBSAMPLE_PROTOCOL_VERSION
    ranking_namespace: str = SUBSAMPLE_RANKING_NAMESPACE
    query_template_version: str = QUERY_TEMPLATE_VERSION
    holdout_offset: int = 0

    @property
    def subsample_hash(self) -> str:
        payload = json.dumps(
            {
                "protocol_version": self.protocol_version,
                "ranking_namespace": self.ranking_namespace,
                "query_template_version": self.query_template_version,
                "holdout_offset": self.holdout_offset,
                "mode": self.mode,
                "selected_events": [item.canonical_record() for item in self.selected_events],
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @property
    def event_keys(self) -> tuple[tuple[int, int], ...]:
        return tuple((item.seed, item.event.event_id) for item in self.selected_events)

    @property
    def query_keys(self) -> tuple[tuple[int, int, str], ...]:
        return tuple(
            (item.seed, item.event.event_id, query.query_id)
            for item in self.selected_events
            for query in item.queries
        )

    def to_manifest(self) -> dict[str, object]:
        event_counts: Counter[str] = Counter()
        query_counts: Counter[str] = Counter()
        per_seed_event_counts: dict[int, Counter[str]] = defaultdict(Counter)
        for item in self.selected_events:
            event_counts[item.event.event_type] += 1
            per_seed_event_counts[item.seed][item.event.event_type] += 1
            query_counts.update(query.query_type for query in item.queries)
        return {
            "protocol_version": self.protocol_version,
            "ranking_namespace": self.ranking_namespace,
            "query_template_version": self.query_template_version,
            "pilot_holdout_selection_version": PILOT_HOLDOUT_SELECTION_VERSION,
            "holdout_offset": self.holdout_offset,
            "mode": self.mode,
            "subsample_hash": self.subsample_hash,
            "event_count": len(self.selected_events),
            "query_count": len(self.query_keys),
            "event_counts": dict(sorted(event_counts.items())),
            "query_counts": dict(sorted(query_counts.items())),
            "per_seed_event_counts": {
                str(seed): dict(sorted(counts.items()))
                for seed, counts in sorted(per_seed_event_counts.items())
            },
            "selected_events": [item.canonical_record() for item in self.selected_events],
        }


def _select_queries(
    *,
    seed: int,
    event: KnowledgeEvent,
    queries: Sequence[Query],
    stable_query_count: int,
) -> tuple[Query, ...]:
    if stable_query_count <= 0:
        raise ValueError("stable_query_count must be positive")
    by_type: dict[str, list[Query]] = defaultdict(list)
    for query in queries:
        if query.event_id != event.event_id:
            raise ValueError(
                f"Query {query.query_id} belongs to event {query.event_id}, expected {event.event_id}"
            )
        by_type[query.query_type].append(query)

    def exactly_one(query_type: str) -> Query:
        matches = by_type.get(query_type, [])
        if len(matches) != 1:
            raise ValueError(
                f"Expected exactly one {query_type} query for seed={seed} event={event.event_id}; "
                f"found {len(matches)}"
            )
        return matches[0]

    selected: list[Query] = [exactly_one(TARGET)]
    if event.event_type in {REVISE, RETIRE}:
        selected.extend([exactly_one(OBSOLETE_UNQUALIFIED), exactly_one(HISTORICAL)])
    elif by_type.get(OBSOLETE_UNQUALIFIED) or by_type.get(HISTORICAL):
        raise ValueError(f"ADD event {event.event_id} unexpectedly has obsolete/historical queries")

    stable = sorted(
        by_type.get(CURRENT_VALID_STABLE, []),
        key=lambda query: (
            _hash_key(
                SUBSAMPLE_RANKING_NAMESPACE,
                "stable_query",
                seed,
                event.event_id,
                query.query_id,
            ),
            query.query_id,
        ),
    )
    if len(stable) < stable_query_count:
        raise ValueError(
            f"Need {stable_query_count} stable queries for seed={seed} event={event.event_id}; "
            f"found {len(stable)}"
        )
    selected.extend(stable[:stable_query_count])
    return tuple(selected)


def select_subsample(
    events_by_seed: Mapping[int, Sequence[KnowledgeEvent]],
    queries_by_seed: Mapping[int, Sequence[Query]],
    *,
    event_counts: Mapping[str, int] = FORMAL_EVENT_COUNTS,
    stable_query_count: int = STABLE_QUERIES_PER_EVENT,
    mode: str = "formal",
) -> SubsampleSelection:
    """Select a stratified deterministic event/query subset.

    Event membership is hash-ranked independently inside each ``seed x
    event_type`` stratum.  Query membership then keeps one target, one
    obsolete and one historical query for REVISE/RETIRE, plus exactly two
    hash-ranked stable queries.  ADD therefore contributes three queries and
    REVISE/RETIRE contribute five.
    """

    if not events_by_seed:
        raise ValueError("events_by_seed must be non-empty")
    if set(events_by_seed) != set(queries_by_seed):
        raise ValueError("events_by_seed and queries_by_seed must have identical seed keys")
    normalized_counts = {event_type: int(event_counts.get(event_type, 0)) for event_type in (ADD, REVISE, RETIRE)}
    if any(count < 0 for count in normalized_counts.values()) or sum(normalized_counts.values()) <= 0:
        raise ValueError("event_counts must contain a positive non-negative stratification")

    selected_events: list[SelectedEvent] = []
    for seed in sorted(events_by_seed):
        events = list(events_by_seed[seed])
        event_ids = [event.event_id for event in events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError(f"Duplicate event_id values for seed {seed}")
        queries_for_event: dict[int, list[Query]] = defaultdict(list)
        for query in queries_by_seed[seed]:
            queries_for_event[query.event_id].append(query)

        for event_type in (ADD, REVISE, RETIRE):
            candidates = [event for event in events if event.event_type == event_type]
            candidates.sort(
                key=lambda event: (
                    _hash_key(
                        SUBSAMPLE_RANKING_NAMESPACE,
                        "formal_event",
                        seed,
                        event_type,
                        event.event_id,
                    ),
                    event.event_id,
                )
            )
            required = normalized_counts[event_type]
            if len(candidates) < required:
                raise ValueError(
                    f"Need {required} {event_type} events for seed={seed}; found {len(candidates)}"
                )
            for event in candidates[:required]:
                selected_events.append(
                    SelectedEvent(
                        seed=seed,
                        event=event,
                        queries=_select_queries(
                            seed=seed,
                            event=event,
                            queries=queries_for_event.get(event.event_id, []),
                            stable_query_count=stable_query_count,
                        ),
                    )
                )

    selected_events.sort(key=lambda item: (item.seed, item.event.event_id))
    return SubsampleSelection(mode=mode, selected_events=tuple(selected_events))


def select_headroom_pilot(
    formal_selection: SubsampleSelection,
    *,
    seed: int = PILOT_SEED,
    event_counts: Mapping[str, int] = PILOT_EVENT_COUNTS,
    holdout_offset: int = 0,
) -> SubsampleSelection:
    """Derive one deterministic, non-overlapping pilot block.

    ``holdout_offset=0`` reproduces the original v1/v2 12-event pilot.  Each
    positive offset advances by a full stratum-specific pilot block, so offset
    1 is disjoint from offset 0 rather than merely shifting by one event.
    """

    if holdout_offset < 0:
        raise ValueError("holdout_offset must be non-negative")

    selected: list[SelectedEvent] = []
    for event_type in (ADD, REVISE, RETIRE):
        candidates = [
            item
            for item in formal_selection.selected_events
            if item.seed == seed and item.event.event_type == event_type
        ]
        candidates.sort(
            key=lambda item: (
                _hash_key(
                    SUBSAMPLE_RANKING_NAMESPACE,
                    "headroom_pilot_event",
                    seed,
                    event_type,
                    item.event.event_id,
                ),
                item.event.event_id,
            )
        )
        required = int(event_counts.get(event_type, 0))
        start = holdout_offset * required
        stop = start + required
        if len(candidates) < stop:
            raise ValueError(
                f"Need {stop} formal {event_type} events for pilot seed={seed} "
                f"holdout_offset={holdout_offset}; found {len(candidates)}"
            )
        selected.extend(candidates[start:stop])
    selected.sort(key=lambda item: (item.seed, item.event.event_id))
    return SubsampleSelection(
        mode="pilot",
        selected_events=tuple(selected),
        holdout_offset=holdout_offset,
    )
