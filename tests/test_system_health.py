"""Readiness checks must be informative and side-effect free."""

import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine

from services.system_health import _model_status, build_health_report


class _FakeDatabase:
    def __init__(self, count=90):
        self.session_manager = SimpleNamespace(engine=create_engine("sqlite:///:memory:"))
        self.count = count

    def get_documents_count(self):
        return self.count


def test_model_health_reports_configuration_without_exposing_key(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "qwen")
    monkeypatch.setenv("LLM_API_KEY", "secret-value")
    monkeypatch.setenv("LLM_MODEL", "qwen3.7-plus")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "qwen")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-v3")

    status = _model_status()

    assert status["status"] == "ok"
    assert status["chat"]["key_configured"] is True
    assert "secret-value" not in str(status)


def test_health_report_treats_disabled_redis_as_optional(monkeypatch, tmp_path):
    monkeypatch.setenv("MODEL_PROVIDER", "qwen")
    monkeypatch.setenv("LLM_API_KEY", "configured")
    monkeypatch.setenv("LLM_MODEL", "qwen3.7-plus")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "qwen")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-v3")
    monkeypatch.setenv("MODEL_USAGE_PATH", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("RAG_TRACE_PATH", str(tmp_path / "trace.jsonl"))
    service = SimpleNamespace(
        db=_FakeDatabase(90),
        retriever=SimpleNamespace(num_docs=90),
        initialized=True,
        _index_generation=3,
    )

    report = asyncio.run(build_health_report(service))

    assert report["status"] == "ok"
    assert report["checks"]["database"]["status"] == "ok"
    assert report["checks"]["knowledge"]["active_chunks"] == 90
    assert report["checks"]["redis"]["required"] is False
