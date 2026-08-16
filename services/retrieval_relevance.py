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
    ) -> None:
        self.enabled = (
            _env_bool("RAG_NO_ANSWER_ENABLED", True)
            if enabled is None
            else enabled
        )
        self.dense_min_score = (
            _env_float("RAG_DENSE_MIN_SCORE", 0.60)
            if dense_min_score is None
            else float(dense_min_score)
        )
        self.bm25_min_score = (
            _env_float("RAG_BM25_MIN_SCORE", 8.0)
            if bm25_min_score is None
            else float(bm25_min_score)
        )

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def is_relevant(self, document: Dict[str, Any]) -> bool:
        if not self.enabled:
            return True

        metadata = document.get("retrieval") or {}
        strategy = metadata.get("strategy")
        dense_score = self._number(metadata.get("dense_score"))
        bm25_score = self._number(metadata.get("bm25_score"))

        # Compatibility for Dense retrievers that only expose the public score.
        if dense_score is None and strategy == "dense":
            dense_score = self._number(document.get("score"))

        return bool(
            (dense_score is not None and dense_score >= self.dense_min_score)
            or (bm25_score is not None and bm25_score >= self.bm25_min_score)
        )

    def filter(self, documents: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [document for document in documents if self.is_relevant(document)]
