#!/usr/bin/env python3
"""Dense retrieval for the frozen B2A Qwen3-Embedding configuration."""

from __future__ import annotations

import hashlib
import heapq
import json
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np

from rae_egta.rag_post_event.document_backend import (
    Chunk,
    Document,
    RetrievedChunk,
    chunks_for_document,
)


DENSE_RETRIEVER_VERSION = "dense_qwen3embed_k5_v1"
QWEN3_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
QWEN3_RETRIEVAL_INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"


class EmbeddingEncoder(Protocol):
    model_id: str
    dimension: int

    def encode_queries(self, texts: Sequence[str]) -> np.ndarray: ...

    def encode_documents(self, texts: Sequence[str]) -> np.ndarray: ...


def l2_normalize(matrix: np.ndarray) -> np.ndarray:
    values = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    return values / np.maximum(norms, 1e-12)


class Qwen3EmbeddingEncoder:
    """Lazy Transformers implementation matching the official model card."""

    model_id = QWEN3_EMBEDDING_MODEL
    dimension = 1024

    def __init__(
        self,
        *,
        model_path: Path,
        device: str = "cuda",
        batch_size: int = 8,
        max_length: int = 4096,
    ) -> None:
        if not model_path.is_dir():
            raise FileNotFoundError(f"Embedding model path does not exist: {model_path}")
        if batch_size <= 0 or max_length <= 0:
            raise ValueError("batch_size and max_length must be positive")
        import torch
        from transformers import AutoModel, AutoTokenizer

        self.model_path = model_path
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self._torch = torch
        self.tokenizer = AutoTokenizer.from_pretrained(
            str(model_path), local_files_only=True, padding_side="left"
        )
        dtype = torch.float16 if device.startswith("cuda") else torch.float32
        self.model = AutoModel.from_pretrained(
            str(model_path), local_files_only=True, torch_dtype=dtype
        ).to(device)
        self.model.eval()
        configured_dim = int(getattr(self.model.config, "hidden_size", self.dimension))
        if configured_dim != self.dimension:
            raise RuntimeError(f"Unexpected Qwen3 embedding dimension: {configured_dim}")

    def _encode(self, texts: Sequence[str]) -> np.ndarray:
        batches: list[np.ndarray] = []
        torch = self._torch
        for offset in range(0, len(texts), self.batch_size):
            batch = list(texts[offset : offset + self.batch_size])
            tokens = self.tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.max_length,
                return_tensors="pt",
            ).to(self.device)
            with torch.inference_mode():
                outputs = self.model(**tokens)
                hidden = outputs.last_hidden_state
                # Left padding places the final non-padding token at -1. This is
                # the official Qwen3-Embedding pooling rule.
                pooled = hidden[:, -1]
                pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            batches.append(pooled.float().cpu().numpy())
        if not batches:
            return np.empty((0, self.dimension), dtype=np.float32)
        return np.concatenate(batches, axis=0).astype(np.float32, copy=False)

    def encode_queries(self, texts: Sequence[str]) -> np.ndarray:
        instructed = [
            f"Instruct: {QWEN3_RETRIEVAL_INSTRUCTION}\nQuery:{text}" for text in texts
        ]
        return self._encode(instructed)

    def encode_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode(texts)


class DenseDocumentIndex:
    """Cosine/dot-product index with the same chunk IDs as lexical BM25."""

    def __init__(
        self,
        documents: tuple[Document, ...],
        *,
        encoder: EmbeddingEncoder,
        embedding_cache_path: Path | None = None,
    ) -> None:
        self.documents = documents
        self.encoder = encoder
        self.chunks = tuple(chunk for doc in documents for chunk in chunks_for_document(doc))
        self.embedding_cache_path = embedding_cache_path
        self.document_embeddings = self._load_or_encode_documents()
        if self.document_embeddings.shape != (len(self.chunks), encoder.dimension):
            raise RuntimeError(
                "Dense embedding shape mismatch: "
                f"got {self.document_embeddings.shape}, expected {(len(self.chunks), encoder.dimension)}"
            )
        self._retrieve_cache: dict[tuple[str, int], tuple[RetrievedChunk, ...]] = {}

    def _chunk_fingerprint(self) -> str:
        digest = hashlib.sha256()
        for chunk in self.chunks:
            digest.update(chunk.chunk_id.encode("utf-8"))
            digest.update(b"\0")
            digest.update(chunk.text.encode("utf-8"))
            digest.update(b"\0")
        return digest.hexdigest()

    def _load_or_encode_documents(self) -> np.ndarray:
        fingerprint = self._chunk_fingerprint()
        if self.embedding_cache_path is not None and self.embedding_cache_path.is_file():
            with np.load(self.embedding_cache_path, allow_pickle=False) as payload:
                if str(payload["model_id"].item()) != self.encoder.model_id:
                    raise RuntimeError("Dense cache model_id mismatch")
                if str(payload["chunk_fingerprint"].item()) != fingerprint:
                    raise RuntimeError("Dense cache chunk fingerprint mismatch")
                return l2_normalize(payload["embeddings"])
        embeddings = l2_normalize(self.encoder.encode_documents([chunk.text for chunk in self.chunks]))
        if self.embedding_cache_path is not None:
            self.embedding_cache_path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(
                self.embedding_cache_path,
                embeddings=embeddings,
                model_id=np.asarray(self.encoder.model_id),
                chunk_fingerprint=np.asarray(fingerprint),
                protocol_version=np.asarray(DENSE_RETRIEVER_VERSION),
            )
        return embeddings

    def retrieve(self, prompt: str, *, top_k: int) -> tuple[RetrievedChunk, ...]:
        cache_key = (prompt, top_k)
        if cache_key in self._retrieve_cache:
            return self._retrieve_cache[cache_key]
        if top_k <= 0 or not self.chunks:
            self._retrieve_cache[cache_key] = ()
            return ()
        query = l2_normalize(self.encoder.encode_queries([prompt]))[0]
        scores = self.document_embeddings @ query
        scored = [
            (float(scores[index]), chunk.chunk_id, chunk)
            for index, chunk in enumerate(self.chunks)
        ]
        ranked = heapq.nsmallest(min(top_k, len(scored)), scored, key=lambda item: (-item[0], item[1]))
        result = tuple(
            RetrievedChunk(chunk=chunk, score=score, rank=rank)
            for rank, (score, _, chunk) in enumerate(ranked, start=1)
        )
        self._retrieve_cache[cache_key] = result
        return result

    def manifest(self) -> dict[str, object]:
        return {
            "protocol_version": DENSE_RETRIEVER_VERSION,
            "model_id": self.encoder.model_id,
            "dimension": self.encoder.dimension,
            "query_instruction": QWEN3_RETRIEVAL_INSTRUCTION,
            "chunk_count": len(self.chunks),
            "chunk_fingerprint": self._chunk_fingerprint(),
            "embedding_cache_path": str(self.embedding_cache_path or ""),
        }
