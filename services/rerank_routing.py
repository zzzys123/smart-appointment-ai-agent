"""Adaptive rerank routing and retrieval/rerank score fusion."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return float(value)


@dataclass(frozen=True)
class RerankDecision:
    """One deterministic routing decision for a retrieval request."""

    enabled: bool
    provider: Optional[str]
    reason: str
    confidence: str
    signals: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "reason": self.reason,
            "confidence": self.confidence,
            "signals": self.signals,
        }


class AdaptiveRerankPolicy:
    """Route high-confidence queries directly and rerank ambiguous queries.

    ``RAG_RERANK_MODE`` supports ``off``, ``always`` and ``adaptive``.  If it
    is omitted, the legacy ``RERANK_ENABLED`` flag maps to always/off.
    """

    _RISK_PATTERN = re.compile(
        r"(胸痛|呼吸困难|昏厥|出血|骨折|急性|受伤|红肿|感染|孕妇|怀孕|"
        r"退款|赔偿|投诉|隐私|手机号|验证码|付款|支付|保证|一定|医疗|治愈)"
    )
    _COMPLEX_PATTERN = re.compile(
        r"(是否|能否|能不能|可以吗|可不可以|哪些|怎么|之后|同时|以及|并且|如果|但是)"
    )
    _POLICY_PATTERN = re.compile(
        r"(会员|预约|占用|占住|空位|排班|改期|取消|退款|支付|付款|"
        r"避开|不想|不要|技师|隐私|验证码|规则|政策)"
    )

    def __init__(
        self,
        mode: Optional[str] = None,
        llm_enabled: Optional[bool] = None,
        cross_encoder_enabled: Optional[bool] = None,
    ) -> None:
        legacy_enabled = _env_bool("RERANK_ENABLED", False)
        configured_mode = mode or os.getenv("RAG_RERANK_MODE")
        self.mode = (configured_mode or ("always" if legacy_enabled else "off")).strip().lower()
        if self.mode not in {"off", "always", "adaptive"}:
            raise ValueError("RAG_RERANK_MODE 必须是 off、always 或 adaptive")

        self.default_provider = (
            os.getenv("RERANKER_PROVIDER", "cross-encoder") or "cross-encoder"
        ).strip().lower()
        self.adaptive_provider = (
            os.getenv("RAG_ADAPTIVE_RERANKER_PROVIDER", "cross-encoder")
            or "cross-encoder"
        ).strip().lower()
        self.llm_enabled = (
            _env_bool("RAG_ADAPTIVE_LLM_ENABLED", False)
            if llm_enabled is None
            else bool(llm_enabled)
        )
        self.cross_encoder_enabled = (
            _env_bool("RAG_ADAPTIVE_CROSS_ENCODER_ENABLED", False)
            if cross_encoder_enabled is None
            else bool(cross_encoder_enabled)
        )
        self.dense_min = _env_float("RAG_DENSE_MIN_SCORE", 0.66)
        self.bm25_min = _env_float("RAG_BM25_MIN_SCORE", 8.0)
        self.dense_high = _env_float("RAG_ADAPTIVE_DENSE_HIGH_SCORE", 0.82)
        self.bm25_high = _env_float("RAG_ADAPTIVE_BM25_HIGH_SCORE", 12.0)

    @property
    def needs_candidate_pool(self) -> bool:
        return self.mode != "off"

    def needs_candidate_pool_for(self, query: str) -> bool:
        """Expand recall only when this query can actually be reranked.

        RRF rankings depend on recall depth, so expanding every adaptive query
        would silently change the Hybrid baseline even when routing ultimately
        skips reranking.
        """
        if self.mode == "always":
            return True
        if self.mode != "adaptive":
            return False
        if self.cross_encoder_enabled:
            return True
        complex_query = len(query) >= 24 or bool(self._COMPLEX_PATTERN.search(query))
        policy_or_risk = bool(
            self._POLICY_PATTERN.search(query) or self._RISK_PATTERN.search(query)
        )
        return self.llm_enabled and complex_query and policy_or_risk

    @staticmethod
    def _number(value: Any) -> Optional[float]:
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None

    def decide(self, query: str, candidates: List[Dict[str, Any]]) -> RerankDecision:
        if self.mode == "off" or not candidates:
            return RerankDecision(False, None, "rerank_disabled", "unknown")
        if self.mode == "always":
            return RerankDecision(True, self.default_provider, "always_mode", "unknown")

        first_metadata = candidates[0].get("retrieval") or {}
        dense_score = self._number(first_metadata.get("dense_score"))
        bm25_score = self._number(first_metadata.get("bm25_score"))
        has_evidence = bool(
            (dense_score is not None and dense_score >= self.dense_min)
            or (bm25_score is not None and bm25_score >= self.bm25_min)
        )
        high_confidence = bool(
            (dense_score is not None and dense_score >= self.dense_high)
            or (bm25_score is not None and bm25_score >= self.bm25_high)
        )
        risky = bool(self._RISK_PATTERN.search(query))
        policy_sensitive = bool(self._POLICY_PATTERN.search(query))
        complex_query = len(query) >= 24 or bool(self._COMPLEX_PATTERN.search(query))
        signals = {
            "top_dense_score": dense_score,
            "top_bm25_score": bm25_score,
            "risk_terms": risky,
            "policy_terms": policy_sensitive,
            "complex_query": complex_query,
        }

        # Preserve a strong Hybrid hit instead of paying for a stochastic
        # reranker.  Safety-risk queries still receive the stricter LLM path.
        if high_confidence and not risky:
            return RerankDecision(False, None, "high_confidence_hybrid", "high", signals)

        # Complex policy/safety queries may have their decisive passage below
        # Top-3 even when the first coarse candidate is weak.  Let the LLM
        # inspect the wider pool; the downstream gate still requires a
        # calibrated >=8/10 rerank score before accepting such a passage.
        if self.llm_enabled and complex_query and (risky or policy_sensitive):
            reason = (
                "high_risk_complex_query"
                if risky
                else "policy_boundary_complex_query"
            )
            return RerankDecision(True, "llm", reason, "medium", signals)
        # Other weak queries are rejected without spending rerank latency.
        if not has_evidence:
            return RerankDecision(False, None, "below_evidence_threshold", "low", signals)
        if self.cross_encoder_enabled:
            return RerankDecision(
                True,
                self.adaptive_provider,
                "ambiguous_cross_encoder_opt_in",
                "medium",
                signals,
            )
        return RerankDecision(
            False,
            None,
            "non_policy_hybrid_default",
            "medium",
            signals,
        )


def _min_max(values: Iterable[float]) -> List[float]:
    values = list(values)
    if not values:
        return []
    low, high = min(values), max(values)
    if high == low:
        return [1.0 for _ in values]
    return [(value - low) / (high - low) for value in values]


def fuse_rerank_scores(
    original: List[Dict[str, Any]],
    reranked: List[Dict[str, Any]],
    *,
    retrieval_weight: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Blend coarse rank and rerank score without dropping candidates."""

    weight = (
        _env_float("RAG_RERANK_RETRIEVAL_WEIGHT", 0.35)
        if retrieval_weight is None
        else float(retrieval_weight)
    )
    if not 0.0 <= weight <= 1.0:
        raise ValueError("RAG_RERANK_RETRIEVAL_WEIGHT 必须在 0 到 1 之间")

    original_rank = {doc.get("id"): rank for rank, doc in enumerate(original, start=1)}
    reranked_by_id = {doc.get("id"): doc for doc in reranked}
    ordered = [reranked_by_id.get(doc.get("id"), dict(doc)) for doc in original]
    rerank_raw = [float(doc.get("rerank_score", 0.0)) for doc in ordered]
    rerank_norm = _min_max(rerank_raw)
    size = len(ordered)

    fused: List[Dict[str, Any]] = []
    for doc, rerank_component in zip(ordered, rerank_norm):
        item = dict(doc)
        coarse_rank = original_rank.get(item.get("id"), size)
        retrieval_component = 1.0 if size <= 1 else 1.0 - (coarse_rank - 1) / (size - 1)
        fused_score = weight * retrieval_component + (1.0 - weight) * rerank_component
        item["fused_score"] = float(fused_score)
        item["score_fusion"] = {
            "retrieval_weight": weight,
            "rerank_weight": 1.0 - weight,
            "retrieval_component": float(retrieval_component),
            "rerank_component": float(rerank_component),
            "original_rank": coarse_rank,
        }
        fused.append(item)

    fused.sort(key=lambda doc: doc["fused_score"], reverse=True)
    for rank, doc in enumerate(fused, start=1):
        doc["rank"] = rank
    return fused
