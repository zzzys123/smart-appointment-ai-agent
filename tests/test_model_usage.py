"""Deterministic tests for privacy-safe model usage and cost accounting."""

import asyncio
from uuid import uuid4

import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from services.model_usage import (
    MeteredEmbeddings,
    UsageCallbackHandler,
    UsageCollector,
    activate_usage_collector,
    deactivate_usage_collector,
    usage_stage,
)
from services.model_usage_observability import ModelUsageObserver
from agents.consultant.consultation_processor import ConsultationProcessor


class _FakeEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [[float(len(text))] for text in texts]

    def embed_query(self, text):
        return [float(len(text))]


def test_chat_usage_is_grouped_by_explicit_stage_and_costed(monkeypatch):
    monkeypatch.setenv("LLM_INPUT_CNY_PER_1M_TOKENS", "2")
    monkeypatch.setenv("LLM_OUTPUT_CNY_PER_1M_TOKENS", "8")
    collector = UsageCollector()

    with usage_stage("answer"):
        collector.record_chat("answer", input_tokens=1000, output_tokens=250)

    report = collector.snapshot(case_count=5)

    assert report["stages"]["answer"]["input_tokens"] == 1000
    assert report["stages"]["answer"]["output_tokens"] == 250
    assert report["cost"]["status"] == "calculated"
    assert report["cost"]["total_cny"] == 0.004
    assert report["cost"]["cny_per_case"] == 0.0008


def test_callback_reads_provider_usage_and_infers_judge_stage():
    collector = UsageCollector()
    handler = UsageCallbackHandler()
    run_id = uuid4()
    token = activate_usage_collector(collector)
    try:
        handler.on_chat_model_start(
            {},
            [[SystemMessage(content="你是严格的 RAG 端到端质量审查员")]],
            run_id=run_id,
        )
        response = LLMResult(generations=[[
            ChatGeneration(message=AIMessage(
                content="{}",
                usage_metadata={
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "total_tokens": 150,
                },
            ))
        ]])
        handler.on_llm_end(response, run_id=run_id)
    finally:
        deactivate_usage_collector(token)

    report = collector.snapshot()
    assert report["stages"]["judge"]["logical_calls"] == 1
    assert report["stages"]["judge"]["total_tokens"] == 150


def test_embedding_usage_is_estimated_and_does_not_store_text(monkeypatch):
    monkeypatch.setenv("EMBEDDING_ESTIMATED_CHARS_PER_TOKEN", "2")
    monkeypatch.setenv("EMBEDDING_CNY_PER_1M_TOKENS", "0.5")
    collector = UsageCollector()
    metered = MeteredEmbeddings(_FakeEmbeddings())
    token = activate_usage_collector(collector)
    try:
        assert metered.embed_query("肩颈按摩") == [4.0]
        assert metered.embed_documents(["价格", "营业时间"]) == [[2.0], [4.0]]
    finally:
        deactivate_usage_collector(token)

    report = collector.snapshot(case_count=1)
    embedding = report["stages"]["embedding"]
    assert embedding["logical_calls"] == 2
    assert embedding["input_characters"] == 10
    assert embedding["estimated_input_tokens"] == 5
    assert embedding["token_source"] == "estimated_from_characters"
    assert "肩颈按摩" not in str(report)


def test_missing_prices_are_explicit_not_zero(monkeypatch):
    for name in (
        "LLM_INPUT_CNY_PER_1M_TOKENS",
        "LLM_OUTPUT_CNY_PER_1M_TOKENS",
        "EMBEDDING_CNY_PER_1M_TOKENS",
    ):
        monkeypatch.delenv(name, raising=False)
    collector = UsageCollector()
    collector.record_chat("answer", input_tokens=10, output_tokens=5)

    cost = collector.snapshot()["cost"]

    assert cost["status"] == "pricing_not_configured"
    assert cost["total_cny"] is None
    assert cost["cny_per_case"] is None
    assert cost["missing_environment_variables"] == [
        "LLM_INPUT_CNY_PER_1M_TOKENS",
        "LLM_OUTPUT_CNY_PER_1M_TOKENS",
    ]


def test_failed_embedding_call_records_failure_without_billable_estimate():
    class _BrokenEmbeddings(_FakeEmbeddings):
        def embed_query(self, text):
            raise RuntimeError("offline")

    collector = UsageCollector()
    metered = MeteredEmbeddings(_BrokenEmbeddings())
    token = activate_usage_collector(collector)
    try:
        with pytest.raises(RuntimeError, match="offline"):
            metered.embed_query("问题")
    finally:
        deactivate_usage_collector(token)

    embedding = collector.snapshot()["stages"]["embedding"]
    assert embedding["logical_calls"] == 1
    assert embedding["failed_calls"] == 1
    assert embedding["estimated_input_tokens"] == 0


def test_resume_merges_previous_usage_and_retry_attempts():
    previous = UsageCollector()
    previous.record_chat("answer", input_tokens=100, output_tokens=20)
    current = UsageCollector()
    current.merge(previous.snapshot())
    current.record_chat("answer", input_tokens=110, output_tokens=25)

    answer = current.snapshot()["stages"]["answer"]

    assert answer["logical_calls"] == 2
    assert answer["input_tokens"] == 210
    assert answer["output_tokens"] == 45


def test_success_without_provider_usage_is_unknown_not_free(monkeypatch):
    for name in (
        "LLM_INPUT_CNY_PER_1M_TOKENS",
        "LLM_OUTPUT_CNY_PER_1M_TOKENS",
    ):
        monkeypatch.setenv(name, "1")
    collector = UsageCollector()
    collector.record_chat("chat")

    cost = collector.snapshot()["cost"]

    assert cost["status"] == "usage_not_reported"
    assert cost["total_cny"] is None


def test_usage_observer_hashes_session_and_never_persists_raw_values(
    monkeypatch, tmp_path
):
    output = tmp_path / "model_usage.jsonl"
    monkeypatch.setenv("MODEL_USAGE_LOG_ENABLED", "false")
    monkeypatch.setenv("MODEL_USAGE_PERSIST_ENABLED", "true")
    monkeypatch.setenv("MODEL_USAGE_PATH", str(output))
    observer = ModelUsageObserver()
    report = {
        "totals": {"logical_calls": 2, "total_tokens": 25},
        "stages": {"answer": {"total_tokens": 25}},
        "cost": {"status": "pricing_not_configured", "total_cny": None},
    }

    payload = observer.emit(report, trace_id="trace-1", session_id="secret-session")
    persisted = output.read_text(encoding="utf-8")

    assert payload["trace_id"] == "trace-1"
    assert len(payload["session_sha256"]) == 64
    assert "secret-session" not in persisted
    assert "用户问题" not in persisted


def test_consultation_usage_observability_does_not_change_response(monkeypatch):
    class _Retriever:
        knowledge_service = None

        async def search_knowledge(self, _query, top_k):
            assert top_k == 3
            return [{"content": "证据"}]

    class _Generator:
        async def generate_response(self, _query, documents):
            assert documents == [{"content": "证据"}]
            return "正常回答"

    monkeypatch.setenv("MODEL_USAGE_LOG_ENABLED", "false")
    processor = ConsultationProcessor(_Retriever(), object(), _Generator())

    response = asyncio.run(processor.process_consultation("问题"))

    assert response == "正常回答"
    assert processor.last_usage["totals"]["failed_calls"] == 0
