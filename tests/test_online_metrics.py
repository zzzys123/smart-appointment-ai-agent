"""Offline aggregation tests for privacy-safe online RAG metrics."""

import json
from datetime import datetime, timezone

from evaluation.summarize_online_metrics import build_report, read_jsonl


def test_online_report_aggregates_latency_usage_cost_and_rerank():
    retrieval = [
        {
            "total_ms": 100,
            "outcome": "answered",
            "route": {"provider": None},
            "candidates": [],
        },
        {
            "total_ms": 300,
            "outcome": "no_answer",
            "route": {"provider": "llm", "fallback": True},
            "candidates": [{"rerank_fallback": True}],
        },
    ]
    usage = [
        {
            "usage": {
                "logical_calls": 2,
                "failed_calls": 0,
                "total_tokens": 100,
                "estimated_input_tokens": 10,
            },
            "stages": {"answer": {"logical_calls": 1, "total_tokens": 100}},
            "cost": {"status": "calculated", "total_cny": 0.01},
        },
        {
            "usage": {
                "logical_calls": 1,
                "failed_calls": 1,
                "total_tokens": 50,
                "estimated_input_tokens": 8,
            },
            "stages": {"embedding": {"logical_calls": 1, "failed_calls": 1}},
            "cost": {"status": "pricing_not_configured", "total_cny": None},
        },
    ]

    report = build_report(usage, retrieval)

    assert report["retrieval"]["latency_ms"]["p50"] == 100
    assert report["retrieval"]["latency_ms"]["p95"] == 300
    assert report["retrieval"]["no_answer_rate"] == 0.5
    assert report["retrieval"]["rerank_trigger_rate"] == 0.5
    assert report["retrieval"]["rerank_fallback_rate"] == 0.5
    assert report["model_usage"]["failed_call_rate"] == 0.333333
    assert report["model_usage"]["cost_coverage_rate"] == 0.5
    assert report["model_usage"]["cost_cny_total"] == 0.01


def test_jsonl_reader_filters_time_and_counts_invalid_lines(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join([
            json.dumps({"recorded_at": "2026-08-18T00:00:00+00:00", "id": 1}),
            "not-json",
            json.dumps({"recorded_at": "2026-08-20T00:00:00+00:00", "id": 2}),
        ]),
        encoding="utf-8",
    )

    events, invalid = read_jsonl(
        path, since=datetime(2026, 8, 19, tzinfo=timezone.utc)
    )

    assert [event["id"] for event in events] == [2]
    assert invalid == 1


def test_empty_report_is_explicit_and_safe():
    report = build_report([], [])

    assert report["retrieval"]["event_count"] == 0
    assert report["model_usage"]["event_count"] == 0
    assert report["model_usage"]["cost_cny_total"] == 0
    assert any("No raw query" in note for note in report["notes"])
