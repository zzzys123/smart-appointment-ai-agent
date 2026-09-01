"""Deterministic evidence thresholds for RAG no-answer fallback."""

from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字，当前值为 {raw!r}") from exc


class RetrievalRelevancePolicy:
    """Filter candidates using raw Dense/BM25 scores rather than RRF score.

    RRF is rank-based and always gives recalled documents a positive score, so
    applying a single threshold to it cannot distinguish weak evidence.  A
    Hybrid candidate is accepted when either original channel reaches its own
    calibrated threshold.
    """

    def __init__(
        self,
        enabled: Optional[bool] = None,
        dense_min_score: Optional[float] = None,
        bm25_min_score: Optional[float] = None,
        llm_rerank_min_score: Optional[float] = None,
    ) -> None:
        self.enabled = (
            _env_bool("RAG_NO_ANSWER_ENABLED", True)
            if enabled is None
            else enabled
        )
        self.dense_min_score = (
            _env_float("RAG_DENSE_MIN_SCORE", 0.66)
            if dense_min_score is None
            else float(dense_min_score)
        )
        self.bm25_min_score = (
            _env_float("RAG_BM25_MIN_SCORE", 8.0)
            if bm25_min_score is None
            else float(bm25_min_score)
        )
        self.llm_rerank_min_score = (
            _env_float("RAG_LLM_RERANK_MIN_SCORE", 8.0)
            if llm_rerank_min_score is None
            else float(llm_rerank_min_score)
        )

    @property
    def thresholds(self) -> Dict[str, float]:
        return {
            "dense_min_score": self.dense_min_score,
            "bm25_min_score": self.bm25_min_score,
            "llm_rerank_min_score": self.llm_rerank_min_score,
        }

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def diagnose(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """Explain which calibrated evidence channel accepted a document."""
        if not self.enabled:
            return {
                "document_id": document.get("id"),
                "source_id": document.get("source_id"),
                "chunk_index": document.get("chunk_index"),
                "accepted": True,
                "accepted_by": ["gate_disabled"],
            }

        metadata = document.get("retrieval") or {}
        strategy = metadata.get("strategy")
        dense_score = self._number(metadata.get("dense_score"))
        bm25_score = self._number(metadata.get("bm25_score"))
        rerank = document.get("rerank") or {}
        rerank_provider = str(rerank.get("provider") or "").strip().lower()
        rerank_score = self._number(
            rerank.get("score", document.get("rerank_score"))
        )

        # Compatibility for Dense retrievers that only expose the public score.
        if dense_score is None and strategy == "dense":
            dense_score = self._number(document.get("score"))

        accepted_by = []
        if dense_score is not None and dense_score >= self.dense_min_score:
            accepted_by.append("dense")
        if bm25_score is not None and bm25_score >= self.bm25_min_score:
            accepted_by.append("bm25")
        # LLM scores use a stable 0-10 rubric.  A high-confidence score may
        # rescue a policy passage from the wider candidate pool, but only after
        # adaptive routing has already found some query-level evidence.  Local
        # Cross-Encoder logits are not calibrated to this threshold.
        if (
            rerank_provider == "llm"
            and rerank_score is not None
            and rerank_score >= self.llm_rerank_min_score
        ):
            accepted_by.append("llm_rerank")

        return {
            "document_id": document.get("id"),
            "source_id": document.get("source_id"),
            "chunk_index": document.get("chunk_index"),
            "accepted": bool(accepted_by),
            "accepted_by": accepted_by,
            "dense_score": dense_score,
            "bm25_score": bm25_score,
            "rerank_provider": rerank_provider or None,
            "rerank_score": rerank_score,
        }

    def is_relevant(self, document: Dict[str, Any]) -> bool:
        return bool(self.diagnose(document)["accepted"])

    def filter_with_diagnostics(
        self, documents: Iterable[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        documents = list(documents)
        diagnostics = [self.diagnose(document) for document in documents]
        accepted = [
            document
            for document, diagnostic in zip(documents, diagnostics)
            if diagnostic["accepted"]
        ]
        return accepted, diagnostics

    def filter(self, documents: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        accepted, _ = self.filter_with_diagnostics(documents)
        return accepted
