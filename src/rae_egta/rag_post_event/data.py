#!/usr/bin/env python3
"""Controlled evolving-store QA data for RAG post-event drift experiments.

This module intentionally stays dependency-light. It builds a structured
knowledge-store QA substrate that activates the reference-mismatch phenomenon
before a real RAG backend is attached.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import asdict, dataclass
from typing import Any


ADD = "ADD"
REVISE = "REVISE"
RETIRE = "RETIRE"

TARGET = "target"
CURRENT_VALID_STABLE = "current_valid_stable"
HISTORICAL = "historical"
OBSOLETE_UNQUALIFIED = "obsolete_unqualified"

UNKNOWN = "UNKNOWN"
NO_CURRENT = "NO_CURRENT_VALID_ANSWER"

# The version names the reader-visible natural-language query surface.  Gold
# answers, event construction, and method semantics are versioned elsewhere
# and are intentionally unaffected by this display-only template revision.
QUERY_TEMPLATE_VERSION = "controlled_rag_query_template_v2"


@dataclass(frozen=True)
class StableFact:
    key: str
    answer: str


@dataclass(frozen=True)
class KnowledgeEvent:
    event_id: int
    event_type: str
    subject: str
    attribute: str
    old_answer: str
    new_answer: str
    stable_facts: tuple[StableFact, ...]


@dataclass(frozen=True)
class Query:
    event_id: int
    query_id: str
    query_type: str
    prompt: str
    expected_old: str
    expected_postevent: str
    expected_current: str
    obsolete_answer: str
    reader_prompt: str = ""


def stable_int(*parts: object) -> int:
    payload = "::".join(str(part) for part in parts)
    return int(hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16], 16)


def stable_rng(*parts: object) -> random.Random:
    return random.Random(stable_int(*parts))


def event_type_schedule(
    event_count: int,
    *,
    seed: int,
    add_fraction: float,
    revise_fraction: float,
    retire_fraction: float,
) -> list[str]:
    if event_count <= 0:
        raise ValueError("event_count must be positive")
    total = add_fraction + revise_fraction + retire_fraction
    if total <= 0.0:
        raise ValueError("event type fractions must sum to a positive value")

    add_count = round(event_count * add_fraction / total)
    revise_count = round(event_count * revise_fraction / total)
    retire_count = event_count - add_count - revise_count
    if event_count >= 3:
        revise_count = max(1, revise_count)
        retire_count = max(1, retire_count)
        add_count = max(0, event_count - revise_count - retire_count)
    while add_count + revise_count + retire_count > event_count:
        if add_count > 0:
            add_count -= 1
        elif revise_count >= retire_count and revise_count > 1:
            revise_count -= 1
        else:
            retire_count -= 1
    while add_count + revise_count + retire_count < event_count:
        if revise_fraction >= retire_fraction:
            revise_count += 1
        else:
            retire_count += 1

    schedule = [ADD] * add_count + [REVISE] * revise_count + [RETIRE] * retire_count
    stable_rng("schedule", seed, event_count).shuffle(schedule)
    return schedule


def make_event(event_id: int, event_type: str, *, seed: int, stable_queries_per_event: int) -> KnowledgeEvent:
    subject = f"entity_{seed}_{event_id:04d}"
    attribute = "current_status"
    old_answer = UNKNOWN if event_type == ADD else f"{subject}_old_status"
    if event_type == RETIRE:
        new_answer = NO_CURRENT
    else:
        new_answer = f"{subject}_new_status"
    stable_facts = tuple(
        StableFact(key=f"{subject}_stable_{idx}", answer=f"{subject}_stable_answer_{idx}")
        for idx in range(stable_queries_per_event)
    )
    return KnowledgeEvent(
        event_id=event_id,
        event_type=event_type,
        subject=subject,
        attribute=attribute,
        old_answer=old_answer,
        new_answer=new_answer,
        stable_facts=stable_facts,
    )


def attribute_display_phrase(attribute: str) -> str:
    """Render a schema identifier as a natural reader-visible noun phrase."""

    tokens = attribute.replace("_", " ").split()
    if not tokens:
        raise ValueError("attribute must contain a non-whitespace display phrase")
    # Temporal scope belongs to the query template.  Removing a leading
    # modifier from compound schema names avoids both "current current ..."
    # and "as of now ... currently ..." without special-casing one attribute.
    if len(tokens) > 1 and tokens[0].casefold() in {"current", "currently"}:
        tokens = tokens[1:]
    return " ".join(tokens)


def reader_visible_query(
    event: KnowledgeEvent,
    query_type: str,
    *,
    stable_index: int | None = None,
) -> str:
    """Build the versioned display query without changing query semantics."""

    if query_type == TARGET:
        return (
            f"As of now, what is the {attribute_display_phrase(event.attribute)} "
            f"of {event.subject}?"
        )
    if query_type == OBSOLETE_UNQUALIFIED:
        return (
            f"What is the {attribute_display_phrase(event.attribute)} "
            f"of {event.subject}?"
        )
    if query_type == HISTORICAL:
        return (
            f"Before the update, what was the {attribute_display_phrase(event.attribute)} "
            f"of {event.subject}?"
        )
    if query_type == CURRENT_VALID_STABLE:
        if stable_index is None:
            raise ValueError("stable_index is required for a stable query")
        return f"As of now, what is the value of stable fact {stable_index} for {event.subject}?"
    raise ValueError(f"Unsupported query_type: {query_type}")


def retrieval_query(
    event: KnowledgeEvent,
    query_type: str,
    *,
    stable_index: int | None = None,
) -> str:
    """Preserve the pre-CL2 retrieval substrate without the doubled phrase."""

    if query_type == TARGET:
        # BM25 sees the exact pre-CL2 token multiset, only reordered so the
        # internal prompt does not retain the defective adjacent wording.
        return f"The {event.attribute} of {event.subject}: what is current?"
    if query_type == OBSOLETE_UNQUALIFIED:
        return f"What is the {event.attribute} of {event.subject}?"
    if query_type == HISTORICAL:
        return f"Before the update, what was the {event.attribute} of {event.subject}?"
    if query_type == CURRENT_VALID_STABLE:
        if stable_index is None:
            raise ValueError("stable_index is required for a stable retrieval query")
        return f"What is the stable fact {stable_index} for {event.subject}?"
    raise ValueError(f"Unsupported query_type: {query_type}")


def query_retrieval_prompt(query: Query) -> str:
    """Return the frozen internal retrieval view."""

    return query.prompt


def query_reader_prompt(query: Query) -> str:
    """Return the natural reader-visible view, with legacy-object fallback."""

    return query.reader_prompt or query.prompt


def queries_for_event(event: KnowledgeEvent) -> list[Query]:
    queries: list[Query] = [
        Query(
            event_id=event.event_id,
            query_id=f"e{event.event_id:04d}_target",
            query_type=TARGET,
            prompt=retrieval_query(event, TARGET),
            expected_old=event.old_answer,
            expected_postevent=event.new_answer,
            expected_current=event.new_answer,
            obsolete_answer=event.old_answer,
            reader_prompt=reader_visible_query(event, TARGET),
        )
    ]
    for idx, fact in enumerate(event.stable_facts):
        queries.append(
            Query(
                event_id=event.event_id,
                query_id=f"e{event.event_id:04d}_stable_{idx}",
                query_type=CURRENT_VALID_STABLE,
                prompt=retrieval_query(event, CURRENT_VALID_STABLE, stable_index=idx),
                expected_old=fact.answer,
                expected_postevent=fact.answer,
                expected_current=fact.answer,
                obsolete_answer=fact.answer,
                reader_prompt=reader_visible_query(
                    event,
                    CURRENT_VALID_STABLE,
                    stable_index=idx,
                ),
            )
        )

    if event.event_type in {REVISE, RETIRE}:
        queries.extend(
            [
                Query(
                    event_id=event.event_id,
                    query_id=f"e{event.event_id:04d}_historical",
                    query_type=HISTORICAL,
                    prompt=retrieval_query(event, HISTORICAL),
                    expected_old=event.old_answer,
                    expected_postevent=event.old_answer,
                    expected_current=event.old_answer,
                    obsolete_answer=event.old_answer,
                    reader_prompt=reader_visible_query(event, HISTORICAL),
                ),
                Query(
                    event_id=event.event_id,
                    query_id=f"e{event.event_id:04d}_obsolete",
                    query_type=OBSOLETE_UNQUALIFIED,
                    prompt=retrieval_query(event, OBSOLETE_UNQUALIFIED),
                    expected_old=event.old_answer,
                    expected_postevent=event.new_answer,
                    expected_current=event.new_answer,
                    obsolete_answer=event.old_answer,
                    reader_prompt=reader_visible_query(event, OBSOLETE_UNQUALIFIED),
                ),
            ]
        )
    return queries


def build_dataset(
    *,
    event_count: int,
    seed: int,
    stable_queries_per_event: int = 2,
    add_fraction: float = 0.10,
    revise_fraction: float = 0.45,
    retire_fraction: float = 0.45,
) -> tuple[list[KnowledgeEvent], list[Query]]:
    schedule = event_type_schedule(
        event_count,
        seed=seed,
        add_fraction=add_fraction,
        revise_fraction=revise_fraction,
        retire_fraction=retire_fraction,
    )
    events = [
        make_event(event_id, event_type, seed=seed, stable_queries_per_event=stable_queries_per_event)
        for event_id, event_type in enumerate(schedule)
    ]
    queries = [query for event in events for query in queries_for_event(event)]
    return events, queries


def event_to_json(event: KnowledgeEvent) -> dict[str, Any]:
    row = asdict(event)
    row["stable_facts"] = [asdict(fact) for fact in event.stable_facts]
    return row


def query_to_json(query: Query) -> dict[str, Any]:
    row = asdict(query)
    row["query_template_version"] = QUERY_TEMPLATE_VERSION
    return row
