"""Retrieval trace serialization and privacy-safe persistence tests."""

import json

from services.retriever.observability import RetrievalObserver
from services.retriever.trace import RetrievalTrace


def test_trace_records_route_scores_and_evidence_gate():
    trace = RetrievalTrace(query="肩颈多少钱", strategy="hybrid", top_k=1)
    documents = [{
        "id": 7,
        "source_id": "KB-1",
        "chunk_index": 2,
        "retrieval": {"dense_score": 0.8, "bm25_score": 9.0, "rrf_score": 0.03},
    }]
    trace.route = {"provider": None, "reason": "high_confidence_hybrid"}
    trace.record_candidates(documents)
    trace.record_evidence_gate(
        documents,
        documents,
        diagnostics=[{
            "document_id": 7,
            "accepted": True,
            "accepted_by": ["dense", "bm25"],
        }],
        thresholds={"dense_min_score": 0.66, "bm25_min_score": 8.0},
    )

    payload = trace.to_dict()

    assert len(payload["trace_id"]) == 32
    assert payload["candidates"][0]["source_id"] == "KB-1"
    assert payload["evidence_gate"]["accepted_count"] == 1
    assert payload["evidence_gate"]["documents"][0]["accepted_by"] == ["dense", "bm25"]
    assert payload["outcome"] == "answered"


def test_observer_hashes_query_and_archives_no_answer(monkeypatch, tmp_path):
    trace_path = tmp_path / "traces.jsonl"
    failure_path = tmp_path / "failures.jsonl"
    monkeypatch.setenv("RAG_TRACE_LOG_ENABLED", "false")
    monkeypatch.setenv("RAG_TRACE_PERSIST_ENABLED", "true")
    monkeypatch.setenv("RAG_FAILURE_ARCHIVE_ENABLED", "true")
    monkeypatch.setenv("RAG_TRACE_INCLUDE_QUERY", "false")
    monkeypatch.setenv("RAG_TRACE_PATH", str(trace_path))
    monkeypatch.setenv("RAG_FAILURE_ARCHIVE_PATH", str(failure_path))
    observer = RetrievalObserver()
    trace = RetrievalTrace(query="我的手机号是 13800000000", strategy="hybrid", top_k=3)
    trace.record_evidence_gate([], [])

    observer.emit(trace)

    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert "query" not in payload
    assert len(payload["query_sha256"]) == 64
    assert failure["failure_kind"] == "no_answer"
