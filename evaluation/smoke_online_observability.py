"""Run one non-Golden consultation and verify retrieval/usage JSONL correlation."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from agents.consultant_agent import ConsultantAgent


USAGE_PATH = ROOT / "data" / "observability" / "model_usage.jsonl"
TRACE_PATH = ROOT / "data" / "observability" / "retrieval_traces.jsonl"


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(bool(line.strip()) for line in path.read_text(encoding="utf-8").splitlines())


def _last_event(path: Path) -> dict:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(lines[-1])


async def main() -> int:
    before_usage = _line_count(USAGE_PATH)
    before_trace = _line_count(TRACE_PATH)
    agent = ConsultantAgent(session_id="observability-smoke")
    await agent.knowledge_retriever.initialize()
    await agent.consult("门店目前提供哪些主要服务项目？")

    after_usage = _line_count(USAGE_PATH)
    after_trace = _line_count(TRACE_PATH)
    if after_usage != before_usage + 1 or after_trace != before_trace + 1:
        print(json.dumps({
            "passed": False,
            "usage_events_added": after_usage - before_usage,
            "retrieval_events_added": after_trace - before_trace,
        }, ensure_ascii=False, indent=2))
        return 1

    usage = _last_event(USAGE_PATH)
    trace = _last_event(TRACE_PATH)
    passed = bool(usage.get("trace_id")) and usage.get("trace_id") == trace.get("trace_id")
    print(json.dumps({
        "passed": passed,
        "trace_id_correlated": passed,
        "usage": usage.get("usage"),
        "cost": usage.get("cost"),
        "retrieval": {
            "total_ms": trace.get("total_ms"),
            "outcome": trace.get("outcome"),
            "route": trace.get("route"),
        },
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
