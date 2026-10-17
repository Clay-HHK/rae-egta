#!/usr/bin/env python3
"""Deterministic five-way scoring for controlled-corpus LLM answers.

Normalization is intentionally small and auditable:

1. Convert the raw value to text, apply Unicode NFKC, and ``casefold``.
2. Treat underscores and hyphens as token separators.
3. Replace every remaining non-alphanumeric Unicode character with one space.
4. Collapse repeated whitespace and strip the result.
5. Apply whole-token-sequence containment to the normalized canonical answers.

If distinct correct-side and conflicting-version canonicals are both present,
the result is ``MIXED_VERSION``.  A correct-side canonical alone is
``CORRECT_SIDE_CONTAINMENT``; a conflicting canonical or unrelated parseable
answer is ``WRONG_OR_OTHER``.  Explicit refusal/unanswerable phrases are
``ABSTAIN``; empty or punctuation-only output is
``REFUSAL_OR_UNPARSEABLE``.  Abstention and negation context is resolved before
generic containment.  The sole exception is exact canonical output, so a
legitimate gold such as ``UNKNOWN`` remains scoreable.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


CORRECT_SIDE_CONTAINMENT = "CORRECT_SIDE_CONTAINMENT"
WRONG_OR_OTHER = "WRONG_OR_OTHER"
MIXED_VERSION = "MIXED_VERSION"
ABSTAIN = "ABSTAIN"
REFUSAL_OR_UNPARSEABLE = "REFUSAL_OR_UNPARSEABLE"
OUTCOME_CLASSES = (
    CORRECT_SIDE_CONTAINMENT,
    WRONG_OR_OTHER,
    MIXED_VERSION,
    ABSTAIN,
    REFUSAL_OR_UNPARSEABLE,
)

_REFUSAL_PHRASES = (
    "cannot answer",
    "can not answer",
    "cannot determine",
    "can not determine",
    "cannot be determined",
    "unable to answer",
    "unable to determine",
    "insufficient information",
    "not enough information",
    "not provided in the documents",
    "not specified in the documents",
    "no retrieved documents",
    "no supported answer",
    "i do not know",
    "i don t know",
    "unknown",
    "unanswerable",
    "n a",
)


@dataclass(frozen=True)
class LLMScore:
    """One parsed answer and its five-way containment outcome."""

    raw_output: str
    normalized_output: str
    parsed_answer: str
    outcome_class: str
    correct_contained: bool
    conflicting_contained: bool


def normalize_answer(text: object) -> str:
    """Normalize reader text and canonical answers under the frozen rules."""

    value = unicodedata.normalize("NFKC", "" if text is None else str(text)).casefold()
    value = re.sub(r"[_\-]+", " ", value)
    value = "".join(character if character.isalnum() else " " for character in value)
    return " ".join(value.split())


def canonical_contained(normalized_output: str, canonical_answer: object) -> bool:
    """Return whole-token-sequence containment for one canonical answer."""

    normalized_canonical = normalize_answer(canonical_answer)
    if not normalized_output or not normalized_canonical:
        return False
    return f" {normalized_canonical} " in f" {normalized_output} "


def _is_token_subsequence(shorter: tuple[str, ...], longer: tuple[str, ...]) -> bool:
    if not shorter or len(shorter) > len(longer):
        return False
    width = len(shorter)
    return any(longer[index : index + width] == shorter for index in range(len(longer) - width + 1))


def validate_non_overlapping_canonicals(canonical_answers: object) -> None:
    """Fail when distinct normalized canonicals overlap as token sequences."""

    if isinstance(canonical_answers, (str, bytes)):
        values = [canonical_answers]
    else:
        values = list(canonical_answers)  # type: ignore[arg-type]
    normalized = sorted(
        {
            tuple(normalize_answer(value).split())
            for value in values
            if normalize_answer(value)
        }
    )
    for index, left in enumerate(normalized):
        for right in normalized[index + 1 :]:
            if _is_token_subsequence(left, right) or _is_token_subsequence(right, left):
                raise ValueError(
                    "Distinct canonical answers must be token-sequence non-overlapping: "
                    f"{' '.join(left)!r} vs {' '.join(right)!r}"
                )


def _affirmative_canonical_mention(normalized_output: str, canonical_answer: object) -> bool:
    """Return true only when at least one canonical mention is not negated."""

    output_tokens = tuple(normalized_output.split())
    canonical_tokens = tuple(normalize_answer(canonical_answer).split())
    if not canonical_tokens:
        return False
    width = len(canonical_tokens)
    negators = {"not", "no", "never", "without", "neither"}
    for index in range(len(output_tokens) - width + 1):
        if output_tokens[index : index + width] != canonical_tokens:
            continue
        before = output_tokens[max(0, index - 3) : index]
        after = output_tokens[index + width : index + width + 3]
        contraction_before = len(before) >= 2 and before[-2:] in {
            ("isn", "t"),
            ("wasn", "t"),
            ("aren", "t"),
            ("doesn", "t"),
        }
        negated_before = any(token in negators for token in before) or contraction_before
        negated_after = (
            bool(after)
            and (
                after[0] in negators
                or (len(after) >= 2 and after[0] in {"is", "was", "are", "does"} and after[1] == "not")
            )
        )
        if not (negated_before or negated_after):
            return True
    return False


def _is_abstention(normalized_output: str) -> bool:
    padded = f" {normalized_output} "
    return any(f" {phrase} " in padded for phrase in _REFUSAL_PHRASES)


def score_reader_output(
    raw_output: object,
    *,
    correct_answer: object,
    conflicting_answer: object = "",
) -> LLMScore:
    """Score one answer without folding mixed/refusal cases into correctness."""

    raw_text = "" if raw_output is None else str(raw_output)
    normalized_output = normalize_answer(raw_text)
    normalized_correct = normalize_answer(correct_answer)
    normalized_conflicting = normalize_answer(conflicting_answer)
    if normalized_conflicting and normalized_conflicting != normalized_correct:
        validate_non_overlapping_canonicals((correct_answer, conflicting_answer))

    exact_correct = bool(normalized_correct) and normalized_output == normalized_correct
    if not normalized_output:
        return LLMScore(
            raw_output=raw_text,
            normalized_output=normalized_output,
            parsed_answer="",
            outcome_class=REFUSAL_OR_UNPARSEABLE,
            correct_contained=False,
            conflicting_contained=False,
        )
    if not exact_correct and _is_abstention(normalized_output):
        return LLMScore(
            raw_output=raw_text,
            normalized_output=normalized_output,
            parsed_answer="UNKNOWN",
            outcome_class=ABSTAIN,
            correct_contained=False,
            conflicting_contained=False,
        )

    correct_contained = exact_correct or _affirmative_canonical_mention(
        normalized_output, correct_answer
    )
    conflicting_contained = (
        bool(normalized_conflicting)
        and normalized_conflicting != normalized_correct
        and _affirmative_canonical_mention(normalized_output, conflicting_answer)
    )

    if correct_contained and conflicting_contained:
        outcome = MIXED_VERSION
        parsed = f"{correct_answer} || {conflicting_answer}"
    elif correct_contained:
        outcome = CORRECT_SIDE_CONTAINMENT
        parsed = str(correct_answer)
    elif conflicting_contained:
        outcome = WRONG_OR_OTHER
        parsed = str(conflicting_answer)
    else:
        outcome = WRONG_OR_OTHER
        parsed = normalized_output

    return LLMScore(
        raw_output=raw_text,
        normalized_output=normalized_output,
        parsed_answer=parsed,
        outcome_class=outcome,
        correct_contained=correct_contained,
        conflicting_contained=conflicting_contained,
    )
