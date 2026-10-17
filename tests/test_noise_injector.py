from __future__ import annotations

import unittest

from rae_egta.rag_post_event.data import (
    ADD,
    CURRENT_VALID_STABLE,
    HISTORICAL,
    OBSOLETE_UNQUALIFIED,
    REVISE,
    RETIRE,
    TARGET,
    KnowledgeEvent,
    Query,
)
from rae_egta.rag_post_event.document_backend import (
    INTENT_CURRENT_DEFAULT,
    INTENT_CURRENT_EXPLICIT,
    INTENT_HISTORICAL,
    INTENT_STABLE_CURRENT,
    Document,
    DocumentCorpus,
    chunks_for_document,
)
from rae_egta.rag_post_event.noise import (
    INTENT_ERROR,
    METADATA_CORRUPTED,
    METADATA_MISSING,
    UNKNOWN_METADATA,
    NoiseConfig,
    build_noise_view,
)


SEED = 13


def toy_fixture() -> tuple[dict[int, DocumentCorpus], dict[tuple[int, str], str]]:
    event_types = (ADD, REVISE, RETIRE, REVISE, RETIRE)
    query_types = (TARGET, OBSOLETE_UNQUALIFIED, HISTORICAL, CURRENT_VALID_STABLE)
    intent_labels = (
        INTENT_CURRENT_EXPLICIT,
        INTENT_CURRENT_DEFAULT,
        INTENT_HISTORICAL,
        INTENT_STABLE_CURRENT,
    )
    events = []
    old_documents = []
    postevent_documents = []
    queries = []
    clean_intents: dict[tuple[int, str], str] = {}
    for event_id in range(10):
        event = KnowledgeEvent(
            event_id=event_id,
            event_type=event_types[event_id % len(event_types)],
            subject=f"subject_{event_id}",
            attribute="status",
            old_answer=f"old_{event_id}",
            new_answer=f"new_{event_id}",
            stable_facts=(),
        )
        events.append(event)
        old_documents.append(
            Document(
                doc_id=f"event_{event_id}_old",
                event_id=event_id,
                doc_role="old",
                version="old",
                title="Old",
                text=f"Canonical answer: old_{event_id}.",
                answer=f"old_{event_id}",
                query_scope="old",
            )
        )
        postevent_documents.append(
            Document(
                doc_id=f"event_{event_id}_current",
                event_id=event_id,
                doc_role="current",
                version="postevent",
                title="Current",
                text=f"Canonical answer: new_{event_id}.",
                answer=f"new_{event_id}",
                query_scope="current",
            )
        )
        for query_offset in range(2):
            query_index = event_id * 2 + query_offset
            query_id = f"query_{query_index}"
            query_type = query_types[query_index % len(query_types)]
            queries.append(
                Query(
                    event_id=event_id,
                    query_id=query_id,
                    query_type=query_type,
                    prompt=f"Prompt {query_index}",
                    expected_old=f"old_{event_id}",
                    expected_postevent=f"new_{event_id}",
                    expected_current=f"new_{event_id}",
                    obsolete_answer=f"old_{event_id}",
                )
            )
            clean_intents[(SEED, query_id)] = intent_labels[query_index % len(intent_labels)]

    corpus = DocumentCorpus(
        events=tuple(events),
        queries=tuple(queries),
        old_documents=tuple(old_documents),
        postevent_documents=tuple(postevent_documents),
    )
    return {SEED: corpus}, clean_intents


class NoiseInjectorTest(unittest.TestCase):
    def test_exact_realized_rates_for_all_three_families(self) -> None:
        corpora, clean_intents = toy_fixture()
        for factor in (METADATA_MISSING, METADATA_CORRUPTED, INTENT_ERROR):
            for rate, expected_count in ((0.05, 1), (0.10, 2), (0.20, 4)):
                with self.subTest(factor=factor, rate=rate):
                    view = build_noise_view(corpora, clean_intents, NoiseConfig(factor=factor, rate=rate))
                    self.assertEqual(view.assignment.eligible_count, 20)
                    self.assertEqual(view.assignment.affected_count, expected_count)
                    self.assertEqual(view.assignment.realized_rate, rate)

    def test_nested_masks_for_document_and_query_units(self) -> None:
        corpora, clean_intents = toy_fixture()
        for factor in (METADATA_MISSING, INTENT_ERROR):
            masks = {}
            for rate in (0.05, 0.10, 0.20):
                view = build_noise_view(corpora, clean_intents, NoiseConfig(factor=factor, rate=rate))
                masks[rate] = set(view.assignment.affected_ids)
            with self.subTest(factor=factor):
                self.assertLessEqual(masks[0.05], masks[0.10])
                self.assertLessEqual(masks[0.10], masks[0.20])

    def test_metadata_view_preserves_clean_corpus_and_changes_bundle_together(self) -> None:
        corpora, clean_intents = toy_fixture()
        clean_corpus = corpora[SEED]
        snapshot = clean_corpus
        view = build_noise_view(
            corpora,
            clean_intents,
            NoiseConfig(factor=METADATA_MISSING, rate=0.20),
        )

        self.assertEqual(clean_corpus, snapshot)
        clean_by_id = {doc.doc_id: doc for doc in clean_corpus.documents}
        changed = []
        for doc in view.corpora_by_seed[SEED].documents:
            clean_doc = clean_by_id[doc.doc_id]
            if doc != clean_doc:
                changed.append(doc)
                self.assertEqual(doc.version, UNKNOWN_METADATA)
                self.assertEqual(doc.doc_role, UNKNOWN_METADATA)
                chunks = chunks_for_document(doc)
                self.assertTrue(chunks)
                self.assertTrue(
                    all(
                        chunk.version == UNKNOWN_METADATA and chunk.doc_role == UNKNOWN_METADATA
                        for chunk in chunks
                    )
                )
        self.assertEqual(len(changed), 4)
        self.assertTrue(all(doc.version != UNKNOWN_METADATA for doc in clean_corpus.documents))

    def test_corrupted_metadata_uses_legal_same_event_bundle(self) -> None:
        corpora, clean_intents = toy_fixture()
        corpus = corpora[SEED]
        legal = {}
        for doc in corpus.documents:
            legal.setdefault(doc.event_id, set()).add((doc.version, doc.doc_role))

        view = build_noise_view(
            corpora,
            clean_intents,
            NoiseConfig(factor=METADATA_CORRUPTED, rate=0.20),
        )
        for change in view.assignment.changes:
            before = change.to_json()["before"]
            after = change.to_json()["after"]
            self.assertNotEqual(
                (before["version"], before["doc_role"]),
                (after["version"], after["doc_role"]),
            )
            self.assertIn((after["version"], after["doc_role"]), legal[change.event_id])

    def test_assignment_hash_is_stable_for_same_seed(self) -> None:
        corpora, clean_intents = toy_fixture()
        config = NoiseConfig(factor=INTENT_ERROR, rate=0.20)
        first = build_noise_view(corpora, clean_intents, config).assignment
        second = build_noise_view(corpora, clean_intents, config).assignment

        self.assertEqual(first.assignment_hash, second.assignment_hash)
        self.assertEqual(first.to_json(), second.to_json())
        self.assertIn("realized_confusion_matrix", first.to_json())


if __name__ == "__main__":
    unittest.main()
