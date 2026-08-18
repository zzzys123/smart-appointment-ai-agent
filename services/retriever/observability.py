"""Structured retrieval observability with optional privacy-safe JSONL output."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict

from .trace import RetrievalTrace

logger = logging.getLogger(__name__)
_WRITE_LOCK = threading.Lock()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class RetrievalObserver:
    """Emit one structured event and optionally archive failure cases.

    Raw queries are excluded by default. Operators can explicitly enable them
    in a trusted environment when case-level debugging is required.
    """

    def __init__(self) -> None:
        self.log_enabled = _env_bool("RAG_TRACE_LOG_ENABLED", True)
        self.persist_enabled = _env_bool("RAG_TRACE_PERSIST_ENABLED", False)
        self.failure_archive_enabled = _env_bool(
            "RAG_FAILURE_ARCHIVE_ENABLED", False
        )
        self.include_query = _env_bool("RAG_TRACE_INCLUDE_QUERY", False)
        self.trace_path = Path(
            os.getenv("RAG_TRACE_PATH", "data/observability/retrieval_traces.jsonl")
        )
        self.failure_path = Path(
            os.getenv("RAG_FAILURE_ARCHIVE_PATH", "data/observability/failure_cases.jsonl")
        )

    def _payload(self, trace: RetrievalTrace) -> Dict[str, Any]:
        payload = trace.to_dict()
        query = payload.pop("query", "")
        payload["query_sha256"] = hashlib.sha256(query.encode("utf-8")).hexdigest()
        if self.include_query:
            payload["query"] = query
        return payload

    @staticmethod
    def _append(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with _WRITE_LOCK:
            with path.open("a", encoding="utf-8") as output:
                output.write(line + "\n")

    def emit(self, trace: RetrievalTrace) -> None:
        payload = self._payload(trace)
        if self.log_enabled:
            logger.info("rag_retrieval_trace %s", json.dumps(payload, ensure_ascii=False))
        if self.persist_enabled:
            self._append(self.trace_path, payload)

        fallback = bool(trace.route.get("fallback")) or any(
            candidate.get("rerank_fallback") for candidate in trace.candidates
        )
        if self.failure_archive_enabled and (
            trace.outcome in {"no_answer", "error"} or fallback
        ):
            payload["failure_kind"] = (
                "rerank_fallback" if fallback else trace.outcome
            )
            self._append(self.failure_path, payload)


_observer = RetrievalObserver()


def emit_retrieval_trace(trace: RetrievalTrace) -> None:
    _observer.emit(trace)
