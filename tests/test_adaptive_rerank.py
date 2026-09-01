"""Deterministic tests for adaptive routing, score fusion and fallback."""

import asyncio

from services.rerank_routing import AdaptiveRerankPolicy, fuse_rerank_scores
from services.retriever.base import BaseRetriever


def _candidate(doc_id, dense, bm25, score=0.03):
    return {
        "id": doc_id,
        "content": str(doc_id),
        "score": score,
        "retrieval": {
            "strategy": "hybrid",
            "dense_score": dense,
            "bm25_score": bm25,
            "rrf_score": score,
        },
    }


def test_adaptive_policy_skips_high_confidence_and_weak_evidence(monkeypatch):
    monkeypatch.setenv("RAG_ADAPTIVE_LLM_ENABLED", "false")
    policy = AdaptiveRerankPolicy(mode="adaptive")

    high = policy.decide("肩颈推拿价格", [_candidate(1, 0.86, 3.0)])
    weak = policy.decide("有没有游泳池", [_candidate(1, 0.40, 0.0)])

    assert not high.enabled and high.reason == "high_confidence_hybrid"
    assert not weak.enabled and weak.reason == "below_evidence_threshold"


def test_adaptive_policy_routes_ambiguous_and_risky_queries(monkeypatch):
    monkeypatch.setenv("RAG_ADAPTIVE_LLM_ENABLED", "true")
    monkeypatch.setenv("RAG_ADAPTIVE_CROSS_ENCODER_ENABLED", "true")
    policy = AdaptiveRerankPolicy(mode="adaptive")

    ambiguous = policy.decide("这个项目是否适合我", [_candidate(1, 0.70, 3.0)])
    risky = policy.decide(
        "肩膀受伤红肿之后是否可以马上按摩，以及需要注意哪些问题？",
        [_candidate(1, 0.75, 9.0)],
    )

    assert ambiguous.provider == "cross-encoder"
    assert risky.provider == "llm"


def test_adaptive_policy_keeps_non_policy_semantic_queries_on_hybrid(monkeypatch):
    monkeypatch.setenv("RAG_ADAPTIVE_CROSS_ENCODER_ENABLED", "false")
    policy = AdaptiveRerankPolicy(mode="adaptive", llm_enabled=True)

    decision = policy.decide(
        "腰背都很累，只做背部够不够？",
        [_candidate(1, 0.74, 7.8)],
    )

    assert decision.enabled is False
    assert decision.reason == "non_policy_hybrid_default"
    assert policy.needs_candidate_pool_for("腰背都很累，只做背部够不够？") is False


def test_adaptive_policy_expands_pool_only_for_rerankable_queries(monkeypatch):
    monkeypatch.setenv("RAG_ADAPTIVE_CROSS_ENCODER_ENABLED", "false")
    policy = AdaptiveRerankPolicy(mode="adaptive", llm_enabled=True)

    assert policy.needs_candidate_pool_for("晚上十点还能开始一小时项目吗？") is False
    assert policy.needs_candidate_pool_for(
        "我是会员，能不能把别人已经订好的时段让给我？"
    ) is True


def test_adaptive_policy_routes_complex_policy_boundaries_to_llm(monkeypatch):
    policy = AdaptiveRerankPolicy(mode="adaptive", llm_enabled=True)

    cases = [
        "想全身放松，但脚不想让技师碰，可以吗？",
        "我是会员，能不能把别人已经订好的时段让给我？",
        "只问了一下有没有空位，系统是否已经替我占住时间？",
    ]

    for query in cases:
        decision = policy.decide(query, [_candidate(1, 0.72, 9.0)])
        assert decision.provider == "llm"
        assert decision.reason == "policy_boundary_complex_query"
        assert decision.signals["policy_terms"] is True


def test_adaptive_policy_checks_weak_policy_pool_but_skips_weak_unrelated_query():
    policy = AdaptiveRerankPolicy(mode="adaptive", llm_enabled=True)

    policy_query = policy.decide(
        "我是会员，能不能占用别人已确认的排班？",
        [_candidate(1, 0.40, 0.0)],
    )
    unrelated = policy.decide(
        "店里有没有游泳池",
        [_candidate(1, 0.40, 0.0)],
    )

    assert policy_query.provider == "llm"
    assert unrelated.enabled is False
    assert unrelated.reason == "below_evidence_threshold"


def test_score_fusion_preserves_retrieval_signal():
    original = [_candidate(1, 0.8, 5.0), _candidate(2, 0.7, 4.0)]
    reranked = [
        {**original[1], "rerank_score": 1.0},
        {**original[0], "rerank_score": 0.9},
    ]

    fused = fuse_rerank_scores(original, reranked, retrieval_weight=0.8)

    assert [document["id"] for document in fused] == [1, 2]
    assert fused[0]["score_fusion"]["retrieval_weight"] == 0.8


class _Retriever(BaseRetriever):
    @property
    def name(self):
        return "test"

    def build_index(self, documents):
        self._cache_docs(documents)

    def _recall(self, query, candidate_k, trace=None):
        self._last_recall_metadata = {
            index: {"dense_score": 0.7, "bm25_score": 9.0}
            for index in range(1, candidate_k + 1)
        }
        return [(index, 1.0 / index) for index in range(1, candidate_k + 1)]


def test_rerank_timeout_falls_back_and_traces(monkeypatch):
    class SlowReranker:
        async def rerank(self, query, documents, top_k):
            await asyncio.sleep(0.05)
            return documents

    monkeypatch.setenv("RAG_RERANK_TIMEOUT_SECONDS", "0.001")
    monkeypatch.setenv("RAG_RERANK_MODE", "off")
    retriever = _Retriever()
    retriever.rerank_enabled = True
    retriever._reranker = SlowReranker()
    retriever.build_index([
        {"id": index, "content": str(index)} for index in range(1, 11)
    ])

    results = asyncio.run(retriever.search("问题", top_k=2))
    trace = retriever.get_current_trace()

    assert [document["id"] for document in results] == [1, 2]
    assert all(document["rerank_fallback"] for document in results)
    assert trace.route["fallback_reason"] == "timeout"
    assert trace.candidates[0]["dense_score"] == 0.7
