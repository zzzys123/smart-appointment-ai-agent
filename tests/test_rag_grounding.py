"""Deterministic tests for no-answer gating and citation output."""

import asyncio
import json

from agents.consultant.prompt_builder import PromptBuilder
from agents.consultant.response_generator import ResponseGenerator
from evaluation.rag_evaluator import RagJudge
from services.retrieval_relevance import RetrievalRelevancePolicy
from services.retriever.hybrid_retriever import HybridRetriever, _expand_sparse_query


class _FakeResponse:
    content = "肩颈推拿的标准价格为 80 元。"


class _FakeLLM:
    def __init__(self):
        self.calls = 0

    async def ainvoke(self, _messages):
        self.calls += 1
        return _FakeResponse()


class _FakeStructuredInvoker:
    def __init__(self, schema):
        self.schema = schema

    async def ainvoke(self, _messages):
        if self.schema.__name__ == "_EndToEndResult":
            return self.schema(
                answer_correctness=1.0,
                faithfulness=1.0,
                answer_relevancy=1.0,
                safety_boundary_pass=0.5,
                reason="部分安全",
            )
        raise AssertionError("unexpected schema invocation")


class _FakeStructuredLLM:
    def with_structured_output(self, schema, **_kwargs):
        return _FakeStructuredInvoker(schema)


def test_relevance_policy_accepts_either_raw_channel_and_rejects_rrf_only():
    policy = RetrievalRelevancePolicy(
        enabled=True, dense_min_score=0.60, bm25_min_score=8.0
    )
    documents = [
        {"id": 1, "retrieval": {"strategy": "hybrid", "dense_score": 0.61, "bm25_score": 0.0}},
        {"id": 2, "retrieval": {"strategy": "hybrid", "dense_score": 0.40, "bm25_score": 8.1}},
        {"id": 3, "score": 0.033, "retrieval": {"strategy": "hybrid", "rrf_score": 0.033}},
    ]

    assert [document["id"] for document in policy.filter(documents)] == [1, 2]


def test_relevance_policy_accepts_only_calibrated_llm_rerank_scores():
    policy = RetrievalRelevancePolicy(
        enabled=True,
        dense_min_score=0.66,
        bm25_min_score=8.0,
        llm_rerank_min_score=8.0,
    )
    documents = [
        {
            "id": 1,
            "retrieval": {"strategy": "hybrid", "dense_score": 0.5, "bm25_score": 2.0},
            "rerank": {"provider": "llm", "score": 9.0},
        },
        {
            "id": 2,
            "retrieval": {"strategy": "hybrid", "dense_score": 0.5, "bm25_score": 2.0},
            "rerank": {"provider": "cross-encoder", "score": 9.0},
        },
    ]

    accepted, diagnostics = policy.filter_with_diagnostics(documents)

    assert [document["id"] for document in accepted] == [1]
    assert diagnostics[0]["accepted_by"] == ["llm_rerank"]
    assert diagnostics[1]["accepted"] is False


def test_disabled_relevance_policy_keeps_all_candidates():
    policy = RetrievalRelevancePolicy(enabled=False)
    documents = [{"id": 1}, {"id": 2}]
    assert policy.filter(documents) == documents


def test_default_dense_threshold_matches_golden_set_calibration(monkeypatch):
    monkeypatch.delenv("RAG_DENSE_MIN_SCORE", raising=False)

    assert RetrievalRelevancePolicy().dense_min_score == 0.66


def test_hybrid_retriever_exposes_raw_scores_for_grounding(monkeypatch):
    monkeypatch.setattr(
        "services.retriever.hybrid_retriever.embed_input", lambda _query: [1.0, 0.0]
    )
    retriever = HybridRetriever()
    retriever.rerank_enabled = False
    retriever.build_index([
        {"id": 1, "content": "肩颈推拿价格八十元", "keywords": ["肩颈"], "embedding": [1.0, 0.0]},
        {"id": 2, "content": "会员生日优惠", "keywords": ["会员"], "embedding": [0.0, 1.0]},
        {"id": 3, "content": "门店停车规则", "keywords": ["停车"], "embedding": [0.0, 1.0]},
    ])

    results = asyncio.run(retriever.search("肩颈多少钱", top_k=2))

    assert results[0]["retrieval"]["strategy"] == "hybrid"
    assert results[0]["retrieval"]["dense_score"] == 1.0
    assert results[0]["retrieval"]["bm25_score"] > 0
    assert results[0]["retrieval"]["rrf_score"] == results[0]["score"]


def test_sparse_query_expansion_covers_business_wording_gaps():
    booking, booking_terms = _expand_sparse_query(
        "只问了一下有没有空位，系统是否已经替我占住时间？"
    )
    body, body_terms = _expand_sparse_query(
        "想全身放松，但脚不想让技师碰，可以吗？"
    )
    member, member_terms = _expand_sparse_query(
        "我是会员，能不能把别人已经订好的时段让给我？"
    )

    assert {"空闲", "完成预约", "预约成功"}.issubset(set(booking_terms))
    assert {"足部", "避开"}.issubset(set(body_terms))
    assert {"已确认", "排班", "占用"}.issubset(set(member_terms))
    assert booking.startswith("只问了一下")
    assert body.startswith("想全身放松")
    assert member.startswith("我是会员")


def test_no_answer_fallback_skips_llm_and_has_no_fake_sources():
    llm = _FakeLLM()
    generator = ResponseGenerator(llm)

    response = asyncio.run(generator.generate_response("店里有游泳池吗", []))

    async def collect():
        return "".join([
            token async for token in generator.generate_response_stream("店里有游泳池吗", [])
        ])

    streamed = asyncio.run(collect())
    assert response == ResponseGenerator.NO_ANSWER_MESSAGE
    assert streamed == f"[REPLY][咨询机器人]{ResponseGenerator.NO_ANSWER_MESSAGE}"
    assert "[SOURCES]" not in streamed
    assert llm.calls == 0


def test_successful_stream_appends_machine_readable_sources():
    llm = _FakeLLM()
    generator = ResponseGenerator(llm)
    documents = [{
        "id": 12,
        "content": "肩颈推拿价格为 80 元。",
        "source_id": "KB-SERVICE-001",
        "source_name": "服务价格.md",
        "title": "肩颈推拿",
        "category": "服务项目",
        "chunk_index": 2,
        "chunk_count": 8,
    }]

    async def collect():
        return "".join([
            token async for token in generator.generate_response_stream("多少钱", documents)
        ])

    streamed = asyncio.run(collect())
    marker = "[SOURCES][咨询机器人]"
    payload = json.loads(streamed.split(marker, 1)[1])
    assert streamed.startswith("[REPLY][咨询机器人]肩颈推拿")
    assert payload == [{
        "source_id": "KB-SERVICE-001",
        "source_name": "服务价格.md",
        "title": "肩颈推拿",
        "category": "服务项目",
        "chunk_number": 3,
        "chunk_count": 8,
        "document_id": 12,
    }]
    assert llm.calls == 1


def test_prompt_forbids_general_knowledge_backfill():
    context = PromptBuilder().build_consultation_prompt("有游泳池吗", [])
    assert "不要推测或补充答案" in context
    assert "一般了解来补充" not in context


def test_prompt_and_judge_evidence_formatter_include_the_same_metadata():
    document = {
        "source_id": "KB-TECH-006",
        "source_name": "06_technician_skill_directory.md",
        "title": "工号 JS-010：何静",
        "chunk_index": 6,
        "chunk_count": 10,
        "content": "何静，女技师。技能方向为淋巴引流、面部护理。",
    }

    formatted = PromptBuilder.format_knowledge_document(document)
    prompt = PromptBuilder().build_consultation_prompt("JS-010 擅长什么？", [document])

    assert "来源编号: KB-TECH-006" in formatted
    assert "章节: 工号 JS-010：何静" in formatted
    assert formatted in prompt
    assert "尽量沿用证据原文" in prompt


def test_end_to_end_judge_accepts_fractional_safety_score():
    judge = RagJudge(llm=_FakeStructuredLLM(), end_to_end_method="json_mode")

    result = asyncio.run(judge.end_to_end("问题", "答案", ["证据"], "参考事实"))

    assert result["safety_boundary_score"] == 0.5
    assert result["safety_boundary_pass"] is False
