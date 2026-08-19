"""Aggregate privacy-safe retrieval and model-usage JSONL into an operations report."""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


def _percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _distribution(values: Iterable[float]) -> Dict[str, float]:
    numbers = [float(value) for value in values]
    if not numbers:
        return {"mean": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}
    return {
        "mean": round(statistics.fmean(numbers), 6),
        "p50": round(_percentile(numbers, 0.50), 6),
        "p95": round(_percentile(numbers, 0.95), 6),
        "p99": round(_percentile(numbers, 0.99), 6),
    }


def _parse_time(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def read_jsonl(path: Path, *, since: Optional[datetime] = None) -> Tuple[List[Dict], int]:
    if not path.exists():
        return [], 0
    events = []
    invalid = 0
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            invalid += 1
            continue
        timestamp = _parse_time(event.get("recorded_at") or event.get("started_at"))
        if since and timestamp and timestamp < since:
            continue
        events.append(event)
    return events, invalid


def summarize_retrieval(events: List[Mapping[str, Any]], invalid_lines: int = 0) -> Dict:
    total = len(events)
    route_providers = Counter()
    no_answer = errors = rerank = fallback = 0
    latencies = []
    for event in events:
        latencies.append(float(event.get("total_ms", 0) or 0))
        no_answer += event.get("outcome") == "no_answer"
        errors += bool(event.get("error")) or event.get("outcome") == "error"
        route = event.get("route") or {}
        provider = route.get("provider")
        if provider:
            rerank += 1
            route_providers[str(provider)] += 1
        fallback += bool(route.get("fallback")) or any(
            bool(candidate.get("rerank_fallback"))
            for candidate in event.get("candidates", [])
        )
    denominator = total or 1
    return {
        "event_count": total,
        "invalid_jsonl_lines": invalid_lines,
        "latency_ms": _distribution(latencies),
        "no_answer_rate": round(no_answer / denominator, 6),
        "error_rate": round(errors / denominator, 6),
        "rerank_trigger_rate": round(rerank / denominator, 6),
        "rerank_fallback_rate": round(fallback / denominator, 6),
        "rerank_provider_counts": dict(sorted(route_providers.items())),
    }


def summarize_usage(events: List[Mapping[str, Any]], invalid_lines: int = 0) -> Dict:
    total = len(events)
    total_tokens = []
    estimated_embedding_tokens = []
    logical_calls = []
    failed_calls = 0
    all_calls = 0
    numeric_costs = []
    cost_statuses = Counter()
    stage_totals: Dict[str, Dict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )
    for event in events:
        usage = event.get("usage") or {}
        total_tokens.append(float(usage.get("total_tokens", 0) or 0))
        estimated_embedding_tokens.append(
            float(usage.get("estimated_input_tokens", 0) or 0)
        )
        calls = int(usage.get("logical_calls", 0) or 0)
        failures = int(usage.get("failed_calls", 0) or 0)
        logical_calls.append(float(calls))
        all_calls += calls
        failed_calls += failures
        for stage, values in (event.get("stages") or {}).items():
            for key in (
                "logical_calls",
                "failed_calls",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "estimated_input_tokens",
            ):
                stage_totals[str(stage)][key] += int(values.get(key, 0) or 0)
        cost = event.get("cost") or {}
        status = str(cost.get("status") or "missing")
        cost_statuses[status] += 1
        if isinstance(cost.get("total_cny"), (int, float)):
            numeric_costs.append(float(cost["total_cny"]))
    denominator = all_calls or 1
    event_denominator = total or 1
    return {
        "event_count": total,
        "invalid_jsonl_lines": invalid_lines,
        "provider_token_count": _distribution(total_tokens),
        "estimated_embedding_token_count": _distribution(estimated_embedding_tokens),
        "logical_calls_per_consultation": _distribution(logical_calls),
        "failed_call_rate": round(failed_calls / denominator, 6),
        "cost_coverage_rate": round(len(numeric_costs) / event_denominator, 6),
        "cost_cny_per_consultation": _distribution(numeric_costs),
        "cost_cny_total": round(sum(numeric_costs), 8),
        "cost_status_counts": dict(sorted(cost_statuses.items())),
        "stage_totals": {
            name: dict(sorted(values.items()))
            for name, values in sorted(stage_totals.items())
        },
    }


def build_report(
    usage_events: List[Mapping[str, Any]],
    retrieval_events: List[Mapping[str, Any]],
    *,
    usage_invalid_lines: int = 0,
    retrieval_invalid_lines: int = 0,
) -> Dict[str, Any]:
    return {
        "report": "rag_online_metrics",
        "retrieval": summarize_retrieval(retrieval_events, retrieval_invalid_lines),
        "model_usage": summarize_usage(usage_events, usage_invalid_lines),
        "notes": [
            "Embedding tokens are estimates; provider billing is authoritative.",
            "Rates exclude free quota and conservatively ignore cache discounts.",
            "No raw query, prompt, answer, API key or session id is read by this report.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--usage-path",
        type=Path,
        default=Path("data/observability/model_usage.jsonl"),
    )
    parser.add_argument(
        "--retrieval-path",
        type=Path,
        default=Path("data/observability/retrieval_traces.jsonl"),
    )
    parser.add_argument("--since", help="ISO-8601 lower bound, for example 2026-08-19T00:00:00+08:00")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    since = _parse_time(args.since)
    if args.since and since is None:
        raise SystemExit("--since 必须是有效的 ISO-8601 时间")
    usage, usage_invalid = read_jsonl(args.usage_path, since=since)
    retrieval, retrieval_invalid = read_jsonl(args.retrieval_path, since=since)
    report = build_report(
        usage,
        retrieval,
        usage_invalid_lines=usage_invalid,
        retrieval_invalid_lines=retrieval_invalid,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
