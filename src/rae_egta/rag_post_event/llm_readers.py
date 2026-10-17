#!/usr/bin/env python3
"""Auditable LLM reader adapters for the NaturalRev-RAG experiment.

The adapters deliberately share one prompt and one response schema.  They do
not perform retrieval, select a method, judge a gold answer, or inspect the
internal ``doc_role``/``version`` labels.  Those responsibilities stay outside
the reader so Qwen, MiniMax, and Grok remain matched reader-family controls.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from rae_egta.rag_post_event.document_backend import RetrievedChunk


QWEN_READER = "qwen3p5_9b"
MINIMAX_READER = "minimax_m3"
GROK_READER = "grok_4_5"
SUPPORTED_READERS = (QWEN_READER, MINIMAX_READER, GROK_READER)

QWEN_DEFAULT_MODEL = "unsloth/Qwen3.5-9B-GGUF:Q4_K_M"
MINIMAX_DEFAULT_MODEL = "MiniMax-M3"
GROK_DEFAULT_MODEL = "grok-4.5"
GROK_DEFAULT_API_KEY_ENV = "GROK_API_KEY"

PROMPT_POLICY_VERSION = "naturalrev_reader_v2_extract_when_present"
READER_SYSTEM_PROMPT = (
    "Answer only from the provided documents. For a query about an attribute, "
    "use the highest-ranked provided document that states a value for that "
    "attribute and return that value verbatim. If a value is stated, do not "
    "return UNKNOWN. Return UNKNOWN only if no provided document states a value "
    "for the queried attribute. Return only that shortest supported value, "
    "without explanation. Do not use outside knowledge."
)

_RESERVED_PAYLOAD_KEYS = {"model", "messages", "temperature", "max_tokens"}


@dataclass(frozen=True)
class ReaderResponse:
    """One normalized reader response and its provenance."""

    answer: str
    reader_family: str
    model_requested: str
    model_returned: str
    prompt_sha256: str
    cache_hit: bool
    latency_seconds: float
    base_url: str
    prompt_policy_version: str = PROMPT_POLICY_VERSION


class JSONLReaderCache:
    """Append-only cache suitable for per-run or per-shard experiment output."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[tuple[str, str, str], tuple[str, str, str]] = {}
        self._lock = threading.RLock()
        if not self.path.exists():
            # Preserve an explicit empty cache artifact for shards that make no
            # model call; strict replay can then distinguish zero calls from a
            # missing/unsynced shard.
            self.path.touch()
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (
                    str(row.get("reader_family", "")),
                    str(row.get("model_requested", "")),
                    str(row.get("prompt_sha256", "")),
                )
                if all(key):
                    self._records[key] = (
                        str(row.get("answer", "")),
                        str(row.get("model_returned", "")),
                        str(row.get("base_url", "")),
                    )

    def get(
        self,
        *,
        reader_family: str,
        model_requested: str,
        prompt_sha256: str,
    ) -> tuple[str, str, str] | None:
        with self._lock:
            return self._records.get((reader_family, model_requested, prompt_sha256))

    def put(self, response: ReaderResponse) -> None:
        with self._lock:
            key = (response.reader_family, response.model_requested, response.prompt_sha256)
            self._records[key] = (
                response.answer,
                response.model_returned,
                response.base_url,
            )
            record = {
                **asdict(response),
                "created_at": time.time(),
                "cache_backend": "jsonl_v1",
            }
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


class OpenAICompatibleReader:
    """Shared OpenAI-compatible transport used by all reader families."""

    def __init__(
        self,
        *,
        reader_family: str,
        base_urls: list[str],
        model_id: str,
        cache_path: Path,
        request_log_path: Path,
        api_key_env: str = "",
        temperature: float = 0.0,
        max_tokens: int = 64,
        timeout_seconds: float = 60.0,
        retries: int = 3,
        extra_body: dict[str, Any] | None = None,
        require_cache: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if reader_family not in SUPPORTED_READERS:
            raise ValueError(f"Unsupported reader family: {reader_family}")
        if not model_id.strip():
            raise ValueError("model_id must be non-empty")
        if temperature < 0:
            raise ValueError("temperature must be non-negative")
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        normalized_urls = [_normalize_base_url(url) for url in base_urls]
        if not normalized_urls:
            raise ValueError("At least one HTTP(S) base URL is required")

        body = dict(extra_body or {})
        reserved = sorted(_RESERVED_PAYLOAD_KEYS.intersection(body))
        if reserved:
            raise ValueError(f"extra_body cannot override reserved keys: {reserved}")

        self.reader_family = reader_family
        self.base_urls = normalized_urls
        self.model_id = model_id.strip()
        self.api_key_env = api_key_env.strip()
        self.temperature = float(temperature)
        self.max_tokens = int(max_tokens)
        self.timeout_seconds = float(timeout_seconds)
        self.retries = max(1, int(retries))
        self.extra_body = body
        self.require_cache = bool(require_cache)
        self.transport = transport
        self.cache = JSONLReaderCache(cache_path)
        self.request_log_path = request_log_path
        self.request_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.real_call_count = 0
        self.cache_hit_count = 0
        self.latencies: list[float] = []
        self._state_lock = threading.RLock()
        self._prompt_locks: dict[str, threading.Lock] = {}

    def answer(
        self,
        *,
        query: str,
        retrieved: tuple[RetrievedChunk, ...],
    ) -> ReaderResponse:
        return self.complete(messages=reader_messages(query=query, retrieved=retrieved))

    def complete(self, *, messages: list[dict[str, str]]) -> ReaderResponse:
        request_payload = stable_request_payload(
            reader_family=self.reader_family,
            model_id=self.model_id,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            extra_body=self.extra_body,
        )
        prompt_sha256 = hashlib.sha256(request_payload.encode("utf-8")).hexdigest()
        with self._prompt_lock(prompt_sha256):
            cached = self.cache.get(
                reader_family=self.reader_family,
                model_requested=self.model_id,
                prompt_sha256=prompt_sha256,
            )
            if cached is not None:
                answer, model_returned, base_url = cached
                response = ReaderResponse(
                    answer=answer,
                    reader_family=self.reader_family,
                    model_requested=self.model_id,
                    model_returned=model_returned,
                    prompt_sha256=prompt_sha256,
                    cache_hit=True,
                    latency_seconds=0.0,
                    base_url=base_url,
                )
                with self._state_lock:
                    self.cache_hit_count += 1
                self._log(response=response, messages=messages, status="cache_hit")
                return response
            if self.require_cache:
                raise RuntimeError(
                    "Reader cache-only replay missing prompt "
                    f"{prompt_sha256} for {self.reader_family}/{self.model_id}"
                )

            started = time.perf_counter()
            initial_route = stable_route_index(prompt_sha256, len(self.base_urls))
            errors: list[str] = []
            for attempt in range(self.retries):
                base_url = self.base_urls[(initial_route + attempt) % len(self.base_urls)]
                try:
                    answer, model_returned = self._post_chat_completion(
                        base_url=base_url,
                        messages=messages,
                    )
                    latency = time.perf_counter() - started
                    response = ReaderResponse(
                        answer=answer,
                        reader_family=self.reader_family,
                        model_requested=self.model_id,
                        model_returned=model_returned,
                        prompt_sha256=prompt_sha256,
                        cache_hit=False,
                        latency_seconds=latency,
                        base_url=base_url,
                    )
                    self.cache.put(response)
                    with self._state_lock:
                        self.real_call_count += 1
                        self.latencies.append(latency)
                    self._log(response=response, messages=messages, status="success")
                    return response
                except Exception as exc:  # pragma: no cover - network integration path
                    errors.append(f"{base_url}: {type(exc).__name__}: {exc}")
                    if attempt + 1 < self.retries:
                        time.sleep(min(2.0, 0.25 * (attempt + 1)))

            self._log_failure(
                prompt_sha256=prompt_sha256,
                messages=messages,
                errors=errors,
            )
            raise RuntimeError(
                f"{self.reader_family} request failed after {self.retries} attempts: "
                + " | ".join(errors)
            )

    def _prompt_lock(self, prompt_sha256: str) -> threading.Lock:
        with self._state_lock:
            return self._prompt_locks.setdefault(prompt_sha256, threading.Lock())

    def manifest(self) -> dict[str, Any]:
        """Return secret-free reader configuration for a run manifest."""

        with self._state_lock:
            return {
                "reader_family": self.reader_family,
                "model_id": self.model_id,
                "base_urls": self.base_urls,
                "api_key_env": self.api_key_env,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "timeout_seconds": self.timeout_seconds,
                "retries": self.retries,
                "extra_body": self.extra_body,
                "require_cache": self.require_cache,
                "real_call_count": self.real_call_count,
                "cache_hit_count": self.cache_hit_count,
                "prompt_policy_version": PROMPT_POLICY_VERSION,
                "system_prompt": READER_SYSTEM_PROMPT,
            }

    def _post_chat_completion(
        self,
        *,
        base_url: str,
        messages: list[dict[str, str]],
    ) -> tuple[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key_env:
            api_key = os.environ.get(self.api_key_env, "").strip()
            if not api_key:
                raise RuntimeError(f"Required API key environment variable is unset: {self.api_key_env}")
            headers["Authorization"] = f"Bearer {api_key}"

        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            **self.extra_body,
        }
        with httpx.Client(timeout=self.timeout_seconds, transport=self.transport) as client:
            result = client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
            result.raise_for_status()
            body = result.json()
        content = body["choices"][0].get("message", {}).get("content", "")
        return clean_llm_content(_content_to_text(content)), str(body.get("model", self.model_id))

    def _log(
        self,
        *,
        response: ReaderResponse,
        messages: list[dict[str, str]],
        status: str,
    ) -> None:
        record = {
            **asdict(response),
            "status": status,
            "messages": messages,
        }
        with self._state_lock:
            _append_jsonl(self.request_log_path, record)

    def _log_failure(
        self,
        *,
        prompt_sha256: str,
        messages: list[dict[str, str]],
        errors: list[str],
    ) -> None:
        with self._state_lock:
            _append_jsonl(
                self.request_log_path,
                {
                    "status": "failed",
                    "reader_family": self.reader_family,
                    "model_requested": self.model_id,
                    "prompt_sha256": prompt_sha256,
                    "prompt_policy_version": PROMPT_POLICY_VERSION,
                    "messages": messages,
                    "errors": errors,
                },
            )


class Qwen35Reader(OpenAICompatibleReader):
    """Local Qwen3.5-9B reader served through an OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        base_urls: list[str],
        output_dir: Path,
        model_id: str = QWEN_DEFAULT_MODEL,
        require_cache: bool = False,
        timeout_seconds: float = 60.0,
        retries: int = 3,
    ) -> None:
        super().__init__(
            reader_family=QWEN_READER,
            base_urls=base_urls,
            model_id=model_id,
            cache_path=output_dir / "qwen_reader_cache.jsonl",
            request_log_path=output_dir / "qwen_reader_requests.jsonl",
            temperature=0.0,
            max_tokens=64,
            timeout_seconds=timeout_seconds,
            retries=retries,
            extra_body={},
            require_cache=require_cache,
        )


class MiniMaxM3Reader(OpenAICompatibleReader):
    """MiniMax-M3 reader with reasoning disabled for matched short answers."""

    def __init__(
        self,
        *,
        base_urls: list[str],
        output_dir: Path,
        model_id: str = MINIMAX_DEFAULT_MODEL,
        api_key_env: str = "MINIMAX_API_KEY",
        require_cache: bool = False,
        timeout_seconds: float = 60.0,
        retries: int = 3,
    ) -> None:
        super().__init__(
            reader_family=MINIMAX_READER,
            base_urls=base_urls,
            model_id=model_id,
            cache_path=output_dir / "minimax_reader_cache.jsonl",
            request_log_path=output_dir / "minimax_reader_requests.jsonl",
            api_key_env=api_key_env,
            temperature=0.0,
            max_tokens=64,
            timeout_seconds=timeout_seconds,
            retries=retries,
            extra_body={"thinking": {"type": "disabled"}},
            require_cache=require_cache,
        )


class Grok45Reader(OpenAICompatibleReader):
    """Grok 4.5 reader using only OpenAI-compatible ``message.content``."""

    def __init__(
        self,
        *,
        base_urls: list[str],
        output_dir: Path,
        model_id: str = GROK_DEFAULT_MODEL,
        api_key_env: str = GROK_DEFAULT_API_KEY_ENV,
        require_cache: bool = False,
        timeout_seconds: float = 60.0,
        retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            reader_family=GROK_READER,
            base_urls=base_urls,
            model_id=model_id,
            cache_path=output_dir / "grok_reader_cache.jsonl",
            request_log_path=output_dir / "grok_reader_requests.jsonl",
            api_key_env=api_key_env,
            temperature=0.0,
            max_tokens=64,
            timeout_seconds=timeout_seconds,
            retries=retries,
            extra_body={},
            require_cache=require_cache,
            transport=transport,
        )


def build_reader(
    reader_family: str,
    *,
    base_urls: list[str],
    output_dir: Path,
    model_id: str = "",
    api_key_env: str = "",
    require_cache: bool = False,
    timeout_seconds: float = 60.0,
    retries: int = 3,
) -> OpenAICompatibleReader:
    """Build one of the frozen OpenAI-compatible reader families."""

    if reader_family == QWEN_READER:
        return Qwen35Reader(
            base_urls=base_urls,
            output_dir=output_dir,
            model_id=model_id or QWEN_DEFAULT_MODEL,
            require_cache=require_cache,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
    if reader_family == MINIMAX_READER:
        return MiniMaxM3Reader(
            base_urls=base_urls,
            output_dir=output_dir,
            model_id=model_id or MINIMAX_DEFAULT_MODEL,
            api_key_env=api_key_env or "MINIMAX_API_KEY",
            require_cache=require_cache,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
    if reader_family == GROK_READER:
        return Grok45Reader(
            base_urls=base_urls,
            output_dir=output_dir,
            model_id=model_id or GROK_DEFAULT_MODEL,
            api_key_env=api_key_env or GROK_DEFAULT_API_KEY_ENV,
            require_cache=require_cache,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
    raise ValueError(f"Unsupported reader family: {reader_family}; choose from {SUPPORTED_READERS}")


def reader_messages(
    *,
    query: str,
    retrieved: tuple[RetrievedChunk, ...],
) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": READER_SYSTEM_PROMPT},
        {"role": "user", "content": reader_user_prompt(query=query, retrieved=retrieved)},
    ]


def reader_user_prompt(
    *,
    query: str,
    retrieved: tuple[RetrievedChunk, ...],
) -> str:
    blocks = []
    for item in retrieved:
        blocks.append(
            "[doc_id={doc_id} chunk_id={chunk_id} rank={rank}]\n{text}".format(
                doc_id=item.chunk.doc_id,
                chunk_id=item.chunk.chunk_id,
                rank=item.rank,
                text=item.chunk.text,
            )
        )
    document_block = "\n\n".join(blocks) if blocks else "[NO RETRIEVED DOCUMENTS]"
    return f"Query:\n{query}\n\nDocuments:\n{document_block}\n\nAnswer:"


def stable_request_payload(
    *,
    reader_family: str,
    model_id: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    extra_body: dict[str, Any],
) -> str:
    return json.dumps(
        {
            "prompt_policy_version": PROMPT_POLICY_VERSION,
            "reader_family": reader_family,
            "model_id": model_id,
            "messages": messages,
            "generation_config": {
                "temperature": temperature,
                "max_tokens": max_tokens,
                "extra_body": extra_body,
            },
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def stable_route_index(prompt_sha256: str, base_url_count: int) -> int:
    if base_url_count <= 0:
        raise ValueError("base_url_count must be positive")
    return int(prompt_sha256[:16], 16) % base_url_count


def clean_llm_content(content: str) -> str:
    without_thinking = re.sub(r"(?is)<think>.*?</think>", "", content)
    return without_thinking.strip()


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def _normalize_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Reader base URL must be HTTP(S): {value!r}")
    return normalized


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
