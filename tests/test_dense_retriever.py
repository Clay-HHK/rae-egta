from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Sequence

import numpy as np

from rae_egta.rag_post_event.dense_retriever import DenseDocumentIndex
from rae_egta.rag_post_event.document_backend import Document, DocumentIndex


class FakeEncoder:
    model_id = "fake-encoder"
    dimension = 2

    def __init__(self) -> None:
        self.document_calls = 0

    def encode_documents(self, texts: Sequence[str]) -> np.ndarray:
        self.document_calls += 1
        return np.asarray([[1.0, 0.0] if "current" in text else [0.0, 1.0] for text in texts])

    def encode_queries(self, texts: Sequence[str]) -> np.ndarray:
        return np.asarray([[1.0, 0.0] if "current" in text else [0.0, 1.0] for text in texts])


def docs() -> tuple[Document, ...]:
    return (
        Document("old", 1, "old", "v1", "Old", "historical value", "old", "scope"),
        Document("new", 1, "current", "v2", "New", "current value", "new", "scope"),
    )


class DenseRetrieverTest(unittest.TestCase):
    def test_dense_and_bm25_share_chunk_ids(self) -> None:
        encoder = FakeEncoder()
        dense = DenseDocumentIndex(docs(), encoder=encoder)
        lexical = DocumentIndex(docs(), retriever="bm25")
        self.assertEqual(
            {chunk.chunk_id for chunk in dense.chunks},
            {chunk.chunk_id for chunk in lexical.chunks},
        )
        result = dense.retrieve("current answer", top_k=2)
        self.assertEqual("new", result[0].chunk.doc_id)
        self.assertEqual([1, 2], [item.rank for item in result])

    def test_embedding_cache_replay_avoids_document_encode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "dense.npz"
            first_encoder = FakeEncoder()
            DenseDocumentIndex(docs(), encoder=first_encoder, embedding_cache_path=path)
            self.assertEqual(1, first_encoder.document_calls)
            second_encoder = FakeEncoder()
            DenseDocumentIndex(docs(), encoder=second_encoder, embedding_cache_path=path)
            self.assertEqual(0, second_encoder.document_calls)

    def test_stable_chunk_id_tie_break(self) -> None:
        class TieEncoder(FakeEncoder):
            def encode_documents(self, texts: Sequence[str]) -> np.ndarray:
                return np.ones((len(texts), 2), dtype=np.float32)

            def encode_queries(self, texts: Sequence[str]) -> np.ndarray:
                return np.ones((len(texts), 2), dtype=np.float32)

        dense = DenseDocumentIndex(docs(), encoder=TieEncoder())
        result = dense.retrieve("anything", top_k=2)
        self.assertEqual(sorted(item.chunk.chunk_id for item in result), [item.chunk.chunk_id for item in result])


if __name__ == "__main__":
    unittest.main()
