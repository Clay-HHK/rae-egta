#!/usr/bin/env python3
"""Deterministic metadata/intent noise assignments for document RAG.

The clean :class:`DocumentCorpus` objects are immutable inputs.  This module
constructs condition-specific views and an auditable assignment record; it
never mutates a document, chunk, query, or corpus in place.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any, Mapping, Sequence

from rae_egta.rag_post_event.document_backend import (
    INTENT_CURRENT_DEFAULT,
    INTENT_CURRENT_EXPLICIT,
    INTENT_HISTORICAL,
    INTENT_STABLE_CURRENT,
    Document,
    DocumentCorpus,
)


CLEAN = "clean"
METADATA_MISSING = "metadata_missing"
METADATA_CORRUPTED = "metadata_corrupted"
INTENT_ERROR = "intent_error"
UNKNOWN_METADATA = "UNKNOWN_METADATA"
NOISE_ASSIGNMENT_SEED = 20260714

NOISE_FACTORS = (CLEAN, METADATA_MISSING, METADATA_CORRUPTED, INTENT_ERROR)
SUPPORTED_RATES = (0.0, 0.05, 0.10, 0.20)
INTENT_LABELS = (
    INTENT_CURRENT_DEFAULT,
    INTENT_CURRENT_EXPLICIT,
    INTENT_HISTORICAL,
    INTENT_STABLE_CURRENT,
)


def _stable_hex(*parts: object) -> str:
    payload = "::".join(str(part) for part in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _frozen_mapping(values: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted((str(key), str(value)) for key, value in values.items()))


@dataclass(frozen=True)
class NoiseConfig:
    factor: str = CLEAN
    rate: float = 0.0
    assignment_seed: int = NOISE_ASSIGNMENT_SEED

    def __post_init__(self) -> None:
        if self.factor not in NOISE_FACTORS:
            raise ValueError(f"Unsupported noise factor: {self.factor}")
        if not any(math.isclose(self.rate, rate, abs_tol=1e-12) for rate in SUPPORTED_RATES):
            raise ValueError(f"Unsupported noise rate: {self.rate}")
        if self.factor == CLEAN and not math.isclose(self.rate, 0.0, abs_tol=1e-12):
            raise ValueError("clean noise factor requires rate=0")

    def normalized(self) -> "NoiseConfig":
        if math.isclose(self.rate, 0.0, abs_tol=1e-12):
            return NoiseConfig(factor=CLEAN, rate=0.0, assignment_seed=self.assignment_seed)
        return self


@dataclass(frozen=True)
class NoiseUnit:
    unit_type: str
    unit_id: str
    seed: int
    event_id: int
    event_type: str
    stratum_label: str
    before: tuple[tuple[str, str], ...]

    @property
    def stratum(self) -> tuple[int, str, str]:
        return (self.seed, self.event_type, self.stratum_label)

    def before_dict(self) -> dict[str, str]:
        return dict(self.before)


@dataclass(frozen=True)
class NoiseChange:
    unit_type: str
    unit_id: str
    seed: int
    event_id: int
    event_type: str
    stratum_label: str
    before: tuple[tuple[str, str], ...]
    after: tuple[tuple[str, str], ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "unit_type": self.unit_type,
            "unit_id": self.unit_id,
            "seed": self.seed,
            "event_id": self.event_id,
            "event_type": self.event_type,
            "stratum_label": self.stratum_label,
            "before": dict(self.before),
            "after": dict(self.after),
        }


@dataclass(frozen=True)
class NoiseAssignment:
    factor: str
    requested_rate: float
    realized_rate: float
    assignment_seed: int
    unit_type: str
    eligible_count: int
    affected_ids: tuple[str, ...]
    changes: tuple[NoiseChange, ...]
    assignment_hash: str

    @property
    def affected_count(self) -> int:
        return len(self.affected_ids)

    def to_json(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "factor": self.factor,
            "requested_rate": self.requested_rate,
            "realized_rate": self.realized_rate,
            "assignment_seed": self.assignment_seed,
            "unit_type": self.unit_type,
            "eligible_count": self.eligible_count,
            "affected_count": self.affected_count,
            "affected_ids": list(self.affected_ids),
            "changes": [change.to_json() for change in self.changes],
            "assignment_hash": self.assignment_hash,
        }
        if self.factor == INTENT_ERROR:
            confusion: dict[str, dict[str, int]] = {}
            for change in self.changes:
                clean_intent = dict(change.before)["intent"]
                effective_intent = dict(change.after)["intent"]
                confusion.setdefault(clean_intent, {})
                confusion[clean_intent][effective_intent] = (
                    confusion[clean_intent].get(effective_intent, 0) + 1
                )
            record["realized_confusion_matrix"] = confusion
        return record


@dataclass(frozen=True)
class NoiseView:
    corpora_by_seed: Mapping[int, DocumentCorpus]
    effective_intents: Mapping[tuple[int, str], str]
    assignment: NoiseAssignment


def _clean_assignment(config: NoiseConfig) -> NoiseAssignment:
    payload = {
        "factor": CLEAN,
        "requested_rate": 0.0,
        "assignment_seed": config.assignment_seed,
        "unit_type": "none",
        "eligible_ids": [],
        "changes": [],
    }
    return NoiseAssignment(
        factor=CLEAN,
        requested_rate=0.0,
        realized_rate=0.0,
        assignment_seed=config.assignment_seed,
        unit_type="none",
        eligible_count=0,
        affected_ids=(),
        changes=(),
        assignment_hash=_canonical_hash(payload),
    )


def _metadata_units(corpora_by_seed: Mapping[int, DocumentCorpus]) -> tuple[NoiseUnit, ...]:
    units: list[NoiseUnit] = []
    for seed, corpus in sorted(corpora_by_seed.items()):
        event_types = {event.event_id: event.event_type for event in corpus.events}
        for doc in corpus.documents:
            units.append(
                NoiseUnit(
                    unit_type="document",
                    unit_id=f"{seed}:{doc.doc_id}",
                    seed=seed,
                    event_id=doc.event_id,
                    event_type=event_types[doc.event_id],
                    stratum_label=doc.doc_role,
                    before=_frozen_mapping({"version": doc.version, "doc_role": doc.doc_role}),
                )
            )
    return tuple(units)


def _intent_units(
    corpora_by_seed: Mapping[int, DocumentCorpus],
    clean_intents: Mapping[tuple[int, str], str],
) -> tuple[NoiseUnit, ...]:
    units: list[NoiseUnit] = []
    for seed, corpus in sorted(corpora_by_seed.items()):
        event_types = {event.event_id: event.event_type for event in corpus.events}
        for query in corpus.queries:
            key = (seed, query.query_id)
            if key not in clean_intents:
                raise ValueError(f"Missing clean intent for query unit {seed}:{query.query_id}")
            clean_intent = clean_intents[key]
            if clean_intent not in INTENT_LABELS:
                raise ValueError(f"Unsupported clean intent label: {clean_intent}")
            units.append(
                NoiseUnit(
                    unit_type="query",
                    unit_id=f"{seed}:{query.query_id}",
                    seed=seed,
                    event_id=query.event_id,
                    event_type=event_types[query.event_id],
                    stratum_label=query.query_type,
                    before=_frozen_mapping({"intent": clean_intent}),
                )
            )
    return tuple(units)


def _stratified_order(units: Sequence[NoiseUnit], config: NoiseConfig) -> tuple[NoiseUnit, ...]:
    """Return one deterministic, proportionally interleaved order.

    Every rate selects a prefix of this same order.  Normalized within-stratum
    ranks preserve stratification while a global prefix gives an exact overall
    affected count whenever the requested fraction is arithmetically feasible.
    """

    grouped: dict[tuple[int, str, str], list[NoiseUnit]] = {}
    for unit in units:
        grouped.setdefault(unit.stratum, []).append(unit)

    prioritized: list[tuple[Fraction, str, NoiseUnit]] = []
    for stratum, members in grouped.items():
        ranked = sorted(
            members,
            key=lambda unit: _stable_hex(
                config.assignment_seed,
                config.factor,
                "mask_rank",
                *stratum,
                unit.unit_id,
            ),
        )
        denominator = 2 * len(ranked)
        for rank, unit in enumerate(ranked):
            quantile = Fraction(2 * rank + 1, denominator)
            tie_breaker = _stable_hex(
                config.assignment_seed,
                config.factor,
                "stratum_interleave",
                unit.unit_id,
            )
            prioritized.append((quantile, tie_breaker, unit))
    prioritized.sort(key=lambda item: (item[0], item[1], item[2].unit_id))
    return tuple(item[2] for item in prioritized)


def _selected_units(units: Sequence[NoiseUnit], config: NoiseConfig) -> tuple[NoiseUnit, ...]:
    ordered = _stratified_order(units, config)
    affected_count = int(math.floor(len(ordered) * config.rate + 0.5))
    return ordered[:affected_count]


def _metadata_after(
    unit: NoiseUnit,
    *,
    config: NoiseConfig,
    legal_bundles: Mapping[tuple[int, int], tuple[tuple[str, str], ...]],
) -> dict[str, str]:
    if config.factor == METADATA_MISSING:
        return {"version": UNKNOWN_METADATA, "doc_role": UNKNOWN_METADATA}

    before = unit.before_dict()
    before_bundle = (before["version"], before["doc_role"])
    candidates = [
        bundle
        for bundle in legal_bundles[(unit.seed, unit.event_id)]
        if bundle != before_bundle
    ]
    if not candidates:
        raise ValueError(
            "metadata_corrupted requires another legal metadata bundle in the same event: "
            f"{unit.unit_id}"
        )
    index = int(
        _stable_hex(
            config.assignment_seed,
            config.factor,
            "replacement",
            unit.unit_id,
        )[:16],
        16,
    ) % len(candidates)
    version, doc_role = candidates[index]
    return {"version": version, "doc_role": doc_role}


def _intent_after(unit: NoiseUnit, *, config: NoiseConfig) -> dict[str, str]:
    clean_intent = unit.before_dict()["intent"]
    candidates = sorted(label for label in INTENT_LABELS if label != clean_intent)
    index = int(
        _stable_hex(
            config.assignment_seed,
            config.factor,
            "replacement",
            unit.unit_id,
        )[:16],
        16,
    ) % len(candidates)
    return {"intent": candidates[index]}


def _build_assignment(
    units: Sequence[NoiseUnit],
    config: NoiseConfig,
) -> NoiseAssignment:
    selected = _selected_units(units, config)
    legal_bundles: dict[tuple[int, int], tuple[tuple[str, str], ...]] = {}
    if config.factor == METADATA_CORRUPTED:
        mutable_bundles: dict[tuple[int, int], set[tuple[str, str]]] = {}
        for unit in units:
            before = unit.before_dict()
            mutable_bundles.setdefault((unit.seed, unit.event_id), set()).add(
                (before["version"], before["doc_role"])
            )
        legal_bundles = {
            key: tuple(sorted(bundles))
            for key, bundles in mutable_bundles.items()
        }

    changes: list[NoiseChange] = []
    for unit in selected:
        if config.factor in {METADATA_MISSING, METADATA_CORRUPTED}:
            after = _metadata_after(unit, config=config, legal_bundles=legal_bundles)
        elif config.factor == INTENT_ERROR:
            after = _intent_after(unit, config=config)
        else:
            raise ValueError(f"Cannot build non-clean assignment for factor {config.factor}")
        changes.append(
            NoiseChange(
                unit_type=unit.unit_type,
                unit_id=unit.unit_id,
                seed=unit.seed,
                event_id=unit.event_id,
                event_type=unit.event_type,
                stratum_label=unit.stratum_label,
                before=unit.before,
                after=_frozen_mapping(after),
            )
        )

    changes.sort(key=lambda change: change.unit_id)
    affected_ids = tuple(change.unit_id for change in changes)
    payload = {
        "factor": config.factor,
        "requested_rate": config.rate,
        "assignment_seed": config.assignment_seed,
        "unit_type": units[0].unit_type if units else "none",
        "eligible_ids": sorted(unit.unit_id for unit in units),
        "changes": [change.to_json() for change in changes],
    }
    return NoiseAssignment(
        factor=config.factor,
        requested_rate=config.rate,
        realized_rate=(len(changes) / len(units)) if units else 0.0,
        assignment_seed=config.assignment_seed,
        unit_type=units[0].unit_type if units else "none",
        eligible_count=len(units),
        affected_ids=affected_ids,
        changes=tuple(changes),
        assignment_hash=_canonical_hash(payload),
    )


def _replace_document(doc: Document, *, seed: int, changes: Mapping[str, NoiseChange]) -> Document:
    change = changes.get(f"{seed}:{doc.doc_id}")
    if change is None:
        return doc
    after = dict(change.after)
    return replace(doc, version=after["version"], doc_role=after["doc_role"])


def _metadata_views(
    corpora_by_seed: Mapping[int, DocumentCorpus],
    assignment: NoiseAssignment,
) -> dict[int, DocumentCorpus]:
    changes = {change.unit_id: change for change in assignment.changes}
    views: dict[int, DocumentCorpus] = {}
    for seed, corpus in corpora_by_seed.items():
        views[seed] = DocumentCorpus(
            events=corpus.events,
            queries=corpus.queries,
            old_documents=tuple(
                _replace_document(doc, seed=seed, changes=changes)
                for doc in corpus.old_documents
            ),
            postevent_documents=tuple(
                _replace_document(doc, seed=seed, changes=changes)
                for doc in corpus.postevent_documents
            ),
        )
    return views


def build_noise_view(
    corpora_by_seed: Mapping[int, DocumentCorpus],
    clean_intents: Mapping[tuple[int, str], str],
    config: NoiseConfig,
) -> NoiseView:
    """Construct a deterministic condition view from immutable clean corpora."""

    normalized = config.normalized()
    if normalized.factor == CLEAN:
        return NoiseView(
            corpora_by_seed=dict(corpora_by_seed),
            effective_intents=dict(clean_intents),
            assignment=_clean_assignment(normalized),
        )

    if normalized.factor in {METADATA_MISSING, METADATA_CORRUPTED}:
        units = _metadata_units(corpora_by_seed)
        assignment = _build_assignment(units, normalized)
        return NoiseView(
            corpora_by_seed=_metadata_views(corpora_by_seed, assignment),
            effective_intents=dict(clean_intents),
            assignment=assignment,
        )

    units = _intent_units(corpora_by_seed, clean_intents)
    assignment = _build_assignment(units, normalized)
    effective_intents = dict(clean_intents)
    for change in assignment.changes:
        effective_intents[(change.seed, change.unit_id.split(":", 1)[1])] = dict(change.after)["intent"]
    return NoiseView(
        corpora_by_seed=dict(corpora_by_seed),
        effective_intents=effective_intents,
        assignment=assignment,
    )
