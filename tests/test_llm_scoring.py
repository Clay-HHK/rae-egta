from __future__ import annotations

import unittest

from rae_egta.rag_post_event.llm_scoring import (
    ABSTAIN,
    CORRECT_SIDE_CONTAINMENT,
    MIXED_VERSION,
    REFUSAL_OR_UNPARSEABLE,
    WRONG_OR_OTHER,
    canonical_contained,
    normalize_answer,
    score_reader_output,
    validate_non_overlapping_canonicals,
)


# Frozen hand labels: realistic terse/verbose answers, hedges, transition
# mixtures, and refusals.  Changes to normalization or classification must
# retain at least 95% agreement with these labels.
GOLDEN_CASES = (
    ("Plus", "Plus", "Basic", CORRECT_SIDE_CONTAINMENT),
    ("The supported tier is Plus.", "Plus", "Basic", CORRECT_SIDE_CONTAINMENT),
    ("Answer: ENTITY_13_0001_NEW_STATUS", "entity_13_0001_new_status", "entity_13_0001_old_status", CORRECT_SIDE_CONTAINMENT),
    ("pLuS", "PLUS", "BASIC", CORRECT_SIDE_CONTAINMENT),
    ("Based on the documents, it appears to be Plus.", "Plus", "Basic", CORRECT_SIDE_CONTAINMENT),
    ('{"answer": "Plus"}', "Plus", "Basic", CORRECT_SIDE_CONTAINMENT),
    ("Before the update it was Basic.", "Basic", "Plus", CORRECT_SIDE_CONTAINMENT),
    ("There is no current valid answer.", "NO_CURRENT_VALID_ANSWER", "legacy_status", CORRECT_SIDE_CONTAINMENT),
    ("The old tier was Basic, but the current tier is Plus.", "Plus", "Basic", MIXED_VERSION),
    ("It may be entity_13_0001_old_status; newer notes say entity_13_0001_new_status.", "entity_13_0001_new_status", "entity_13_0001_old_status", MIXED_VERSION),
    ("Previously Bronze / now Gold", "Gold", "Bronze", MIXED_VERSION),
    ("The documents only support Basic.", "Plus", "Basic", WRONG_OR_OTHER),
    ("The supported tier is Enterprise.", "Plus", "Basic", WRONG_OR_OTHER),
    ("It might be Bronze, although the evidence is ambiguous.", "Plus", "Basic", WRONG_OR_OTHER),
    ("   ", "Plus", "Basic", REFUSAL_OR_UNPARSEABLE),
    ("UNKNOWN", "Plus", "Basic", ABSTAIN),
    ("I cannot answer from the provided documents.", "Plus", "Basic", ABSTAIN),
    ("There is insufficient information to determine this.", "Plus", "Basic", ABSTAIN),
    ("N/A", "Plus", "Basic", ABSTAIN),
    ("The passages discuss several subscription options.", "Plus", "Basic", WRONG_OR_OTHER),
    ("I cannot determine whether it is Plus.", "Plus", "Basic", ABSTAIN),
    ("The answer is not Plus.", "Plus", "Basic", WRONG_OR_OTHER),
    ("It appears, cautiously but clearly, to be Plus.", "Plus", "Basic", CORRECT_SIDE_CONTAINMENT),
    ("The current tier is Plus, not Basic.", "Plus", "Basic", CORRECT_SIDE_CONTAINMENT),
    ("Not Basic; it is Plus.", "Plus", "Basic", CORRECT_SIDE_CONTAINMENT),
)


class LLMScoringTests(unittest.TestCase):
    def test_golden_agreement_is_at_least_ninety_five_percent(self) -> None:
        matches = 0
        for raw, correct, conflicting, expected in GOLDEN_CASES:
            with self.subTest(raw=raw):
                actual = score_reader_output(
                    raw,
                    correct_answer=correct,
                    conflicting_answer=conflicting,
                ).outcome_class
                matches += int(actual == expected)
        self.assertGreaterEqual(matches / len(GOLDEN_CASES), 0.95)

    def test_all_five_outcome_classes_are_reachable(self) -> None:
        outcomes = {
            score_reader_output("new", correct_answer="new", conflicting_answer="old").outcome_class,
            score_reader_output("old", correct_answer="new", conflicting_answer="old").outcome_class,
            score_reader_output("old and new", correct_answer="new", conflicting_answer="old").outcome_class,
            score_reader_output("cannot answer", correct_answer="new", conflicting_answer="old").outcome_class,
            score_reader_output("   ", correct_answer="new", conflicting_answer="old").outcome_class,
        }
        self.assertEqual(
            {
                CORRECT_SIDE_CONTAINMENT,
                WRONG_OR_OTHER,
                MIXED_VERSION,
                ABSTAIN,
                REFUSAL_OR_UNPARSEABLE,
            },
            outcomes,
        )

    def test_normalization_uses_token_sequence_boundaries(self) -> None:
        self.assertEqual("entity 13 0001 new status", normalize_answer("Entity_13-0001_NEW_status"))
        self.assertTrue(canonical_contained("the answer is paris", "Paris"))
        self.assertFalse(canonical_contained("the answer is parisian", "Paris"))
        score = score_reader_output("UNKNOWN", correct_answer="UNKNOWN", conflicting_answer="")
        self.assertEqual(CORRECT_SIDE_CONTAINMENT, score.outcome_class)

    def test_abstention_is_distinct_from_unparseable_output(self) -> None:
        abstain = score_reader_output(
            "UNKNOWN",
            correct_answer="entity_13_0083_new_status",
            conflicting_answer="entity_13_0083_old_status",
        )
        unparseable = score_reader_output(
            "!!!",
            correct_answer="entity_13_0083_new_status",
            conflicting_answer="entity_13_0083_old_status",
        )
        self.assertEqual(ABSTAIN, abstain.outcome_class)
        self.assertEqual("UNKNOWN", abstain.parsed_answer)
        self.assertEqual(REFUSAL_OR_UNPARSEABLE, unparseable.outcome_class)
        self.assertEqual("", unparseable.parsed_answer)

    def test_distinct_overlapping_canonicals_fail_loudly(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-overlapping"):
            validate_non_overlapping_canonicals(("New York", "New York City"))
        with self.assertRaisesRegex(ValueError, "non-overlapping"):
            score_reader_output(
                "New York City",
                correct_answer="New York City",
                conflicting_answer="New York",
            )

    def test_disjoint_corpus_canonicals_pass_validation(self) -> None:
        validate_non_overlapping_canonicals(("New York City", "Boston", "UNKNOWN"))


if __name__ == "__main__":
    unittest.main()
