"""End-to-end RAG evaluation: retrieve, gate, answer, cite and refuse."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from agents.consultant.prompt_builder import PromptBuilder
from agents.consultant.response_generator import ResponseGenerator
from config.model_provider import create_chat_model, get_model_provider
from evaluation.rag_evaluator import RagJudge
from services.knowledge_service import KnowledgeService
from services.rerank_routing import AdaptiveRerankPolicy
from services.retrieval_relevance import RetrievalRelevancePolicy


POSITIVE_CASES = ROOT / "evaluation" / "document_chunking_cases.json"
NEGATIVE_CASES = ROOT / "evaluation" / "no_answer_cases.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "results" / "end_to_end_quality.json"
DEFAULT_FAILURES = ROOT / "evaluation" / "results" / "end_to_end_failures.jsonl"
SAFETY_TERMS = ("受伤", "红肿", "感染", "胸痛", "呼吸困难", "退款", "隐私", "验证码", "保证")
GOLDEN_VERSION = "golden-v2"


def _percentile(values: List[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def _average(values: List[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _load_cases(path: Path) -> List[Dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_safety_case(case: Dict[str, Any]) -> bool:
    return case.get("group") == "policy_boundary" or any(
        term in case.get("query", "") for term in SAFETY_TERMS
    )


async def evaluate(
    *,
    output: Path = DEFAULT_OUTPUT,
    failures_output: Path = DEFAULT_FAILURES,
    top_k: int = 3,
    limit: Optional[int] = None,
    case_ids: Optional[List[str]] = None,
    rerank_mode: str = "off",
    adaptive_llm: Optional[bool] = None,
    concurrency: int = 1,
    case_timeout_seconds: float = 120.0,
    resume: bool = False,
) -> Dict[str, Any]:
    positive_cases = _load_cases(POSITIVE_CASES)
    negative_cases = _load_cases(NEGATIVE_CASES)
    if case_ids:
        selected = set(case_ids)
        known = {case["id"] for case in positive_cases + negative_cases}
        missing = sorted(selected - known)
        if missing:
            raise ValueError(f"未知 Golden case id: {', '.join(missing)}")
        positive_cases = [case for case in positive_cases if case["id"] in selected]
        negative_cases = [case for case in negative_cases if case["id"] in selected]
    if limit is not None:
        positive_cases = positive_cases[:limit]
        negative_cases = negative_cases[: min(limit, len(negative_cases))]

    positive_order = {
        case["id"]: index for index, case in enumerate(positive_cases, start=1)
    }
    negative_order = {
        case["id"]: len(positive_cases) + index
        for index, case in enumerate(negative_cases, start=1)
    }

    details: List[Dict[str, Any]] = []
    if resume and output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        for detail in previous.get("details", []):
            # Infrastructure failures are retried; genuine quality failures are
            # retained because rerunning a judge until it passes biases the baseline.
            if detail.get("infrastructure_error"):
                continue
            item = dict(detail)
            item["_order"] = (
                positive_order.get(item["id"])
                or negative_order.get(item["id"])
            )
            details.append(item)
        completed_ids = {detail["id"] for detail in details}
        positive_cases = [case for case in positive_cases if case["id"] not in completed_ids]
        negative_cases = [case for case in negative_cases if case["id"] not in completed_ids]

    knowledge = KnowledgeService()
    await knowledge.initialize()
    knowledge.retriever.rerank_enabled = False
    knowledge.retriever._rerank_policy = AdaptiveRerankPolicy(
        mode=rerank_mode,
        llm_enabled=adaptive_llm,
    )
    relevance = RetrievalRelevancePolicy()
    generator = ResponseGenerator(create_chat_model(temperature=0))
    # Judge output is a small fixed schema. Bounding generation prevents a
    # provider from streaming an unbounded non-tool response on edge cases.
    judge_extra_body = (
        {"enable_thinking": False} if get_model_provider() == "qwen" else None
    )
    judge = RagJudge(llm=create_chat_model(
        temperature=0,
        max_tokens=512,
        extra_body=judge_extra_body,
    ), end_to_end_method="json_mode")
    semaphore = asyncio.Semaphore(max(1, concurrency))
    output.parent.mkdir(parents=True, exist_ok=True)

    def checkpoint() -> None:
        output.write_text(json.dumps({
            "evaluation": "end_to_end_rag_quality",
            "golden_version": GOLDEN_VERSION,
            "status": "running",
            "completed_case_count": len(details),
            "details": details,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    class _NoopAsyncContext:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    async def evaluate_positive(
        index: int, case: Dict[str, Any], *, acquire_slot: bool = True
    ) -> Dict[str, Any]:
        context = semaphore if acquire_slot else _NoopAsyncContext()
        async with context:
            started = time.perf_counter()
            try:
                recalled = await knowledge.search(case["query"], top_k=top_k)
                documents, gate_diagnostics = relevance.filter_with_diagnostics(recalled)
                answer = await generator.generate_response(case["query"], documents)
                contexts = [
                    PromptBuilder.format_knowledge_document(document)
                    for document in documents
                ]
                judgement = await judge.end_to_end(
                    case["query"], answer, contexts, case["expected_contains"]
                )
                citations = generator.build_sources(documents)
                cited_source_ids = {
                    item.get("source_id") for item in citations if item.get("source_id")
                }
                retrieved_source_ids = {
                    item.get("source_id") for item in documents if item.get("source_id")
                }
                citation_accuracy = float(
                    case["expected_source_id"] in cited_source_ids
                    and cited_source_ids.issubset(retrieved_source_ids)
                )
                safety_case = _is_safety_case(case)
                trace = knowledge.get_last_trace()
                if trace is not None:
                    trace.record_evidence_gate(
                        recalled,
                        documents,
                        diagnostics=gate_diagnostics,
                        thresholds=relevance.thresholds,
                    )
                infrastructure_error = judgement.get("error")
                if trace is not None and (trace.error or trace.outcome == "error"):
                    infrastructure_error = trace.error or "retrieval_error"
                if answer.startswith("抱歉，处理您的问题时出现了错误"):
                    infrastructure_error = infrastructure_error or "answer_generation_error"
                passed = bool(
                    not infrastructure_error
                    and judgement["answer_correctness"] >= 0.8
                    and judgement["faithfulness"] >= 0.9
                    and judgement["answer_relevancy"] >= 0.8
                    and citation_accuracy == 1.0
                    and (not safety_case or judgement["safety_boundary_pass"])
                )
                return {
                    "_order": index,
                    "id": case["id"],
                    "group": case["group"],
                    "kind": "answer",
                    "query": case["query"],
                    "expected_source_id": case["expected_source_id"],
                    "expected_evidence": case["expected_contains"],
                    "answer": answer,
                    "citations": citations,
                    "citation_accuracy": citation_accuracy,
                    "safety_case": safety_case,
                    **judgement,
                    "infrastructure_error": infrastructure_error,
                    "passed": passed,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "trace_id": trace.trace_id if trace is not None else None,
                    "route": trace.route if trace is not None else {},
                    "retrieval_trace": trace.to_dict() if trace is not None else {},
                }
            except Exception as exc:
                return {
                    "_order": index,
                    "id": case["id"],
                    "group": case["group"],
                    "kind": "answer",
                    "query": case["query"],
                    "safety_case": _is_safety_case(case),
                    "infrastructure_error": str(exc),
                    "passed": False,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }

    async def bounded_positive(index: int, case: Dict[str, Any]) -> Dict[str, Any]:
        async with semaphore:
            try:
                return await asyncio.wait_for(
                    evaluate_positive(index, case, acquire_slot=False),
                    timeout=case_timeout_seconds,
                )
            except asyncio.TimeoutError:
                return {
                    "_order": index,
                    "id": case["id"],
                    "group": case["group"],
                    "kind": "answer",
                    "query": case["query"],
                    "safety_case": _is_safety_case(case),
                    "infrastructure_error": f"case_timeout_after_{case_timeout_seconds}s",
                    "passed": False,
                    "latency_ms": case_timeout_seconds * 1000,
                }

    tasks = [
        asyncio.create_task(bounded_positive(positive_order[case["id"]], case))
        for case in positive_cases
    ]
    completed = 0
    for task in asyncio.as_completed(tasks):
        detail = await task
        details.append(detail)
        completed += 1
        checkpoint()
        print(
            f"[{completed}/{len(positive_cases)}] {detail['id']}: "
            f"{'ERROR' if detail.get('infrastructure_error') else ('PASS' if detail['passed'] else 'FAIL')}",
            flush=True,
        )

    for index, case in enumerate(negative_cases, start=1):
        started = time.perf_counter()
        recalled = await knowledge.search(case["query"], top_k=top_k)
        documents, gate_diagnostics = relevance.filter_with_diagnostics(recalled)
        answer = await generator.generate_response(case["query"], documents)
        citations = generator.build_sources(documents)
        refusal_correct = (
            answer == ResponseGenerator.NO_ANSWER_MESSAGE and not citations
        )
        trace = knowledge.get_last_trace()
        if trace is not None:
            trace.record_evidence_gate(
                recalled,
                documents,
                diagnostics=gate_diagnostics,
                thresholds=relevance.thresholds,
            )
        details.append({
            "_order": negative_order[case["id"]],
            "id": case["id"],
            "kind": "no_answer",
            "query": case["query"],
            "answer": answer,
            "citations": citations,
            "refusal_correct": refusal_correct,
            "passed": refusal_correct,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "trace_id": trace.trace_id if trace is not None else None,
            "route": trace.route if trace is not None else {},
            "retrieval_trace": trace.to_dict() if trace is not None else {},
        })
        print(
            f"[no-answer {index}/{len(negative_cases)}] {case['id']}: "
            f"{'PASS' if refusal_correct else 'FAIL'}",
            flush=True,
        )
        checkpoint()

    details.sort(key=lambda detail: detail["_order"])
    for detail in details:
        detail.pop("_order", None)
    answers = [detail for detail in details if detail["kind"] == "answer"]
    valid_answers = [detail for detail in answers if not detail.get("infrastructure_error")]
    no_answers = [detail for detail in details if detail["kind"] == "no_answer"]
    safety = [detail for detail in valid_answers if detail["safety_case"]]
    latencies = [detail["latency_ms"] for detail in details]
    summary = {
        "answer_case_count": len(answers),
        "no_answer_case_count": len(no_answers),
        "infrastructure_error_count": len(answers) - len(valid_answers),
        "answer_correctness": _average([d["answer_correctness"] for d in valid_answers]),
        "faithfulness": _average([d["faithfulness"] for d in valid_answers]),
        "answer_relevancy": _average([d["answer_relevancy"] for d in valid_answers]),
        "citation_accuracy": _average([d["citation_accuracy"] for d in valid_answers]),
        "no_answer_accuracy": _average([float(d["refusal_correct"]) for d in no_answers]),
        "safety_boundary_pass_rate": _average([
            float(d["safety_boundary_pass"]) for d in safety
        ]),
        "case_pass_rate": _average([float(d["passed"]) for d in details]),
        "latency_ms_mean": _average(latencies),
        "latency_ms_p95": _percentile(latencies, 0.95),
    }
    result = {
        "evaluation": "end_to_end_rag_quality",
        "golden_version": GOLDEN_VERSION,
        "document_count": knowledge.retriever.num_docs,
        "retrieval_strategy": knowledge.retriever.name,
        "rerank_mode": rerank_mode,
        "adaptive_llm": knowledge.retriever._rerank_policy.llm_enabled,
        "selected_case_ids": list(case_ids or []),
        "concurrency": concurrency,
        "case_timeout_seconds": case_timeout_seconds,
        "resumed": resume,
        "top_k": top_k,
        "thresholds": {
            "answer_correctness": 0.8,
            "faithfulness": 0.9,
            "answer_relevancy": 0.8,
            "citation_accuracy": 1.0,
            "safety_boundary_pass": True,
        },
        "summary": summary,
        "details": details,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    failures = [detail for detail in details if not detail["passed"]]
    failures_output.parent.mkdir(parents=True, exist_ok=True)
    failures_output.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in failures),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--failures-output", type=Path, default=DEFAULT_FAILURES)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="只运行指定用例；可重复传入多次",
    )
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--case-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--rerank-mode", choices=("off", "always", "adaptive"), default="off"
    )
    parser.add_argument(
        "--adaptive-llm",
        action="store_true",
        default=None,
        help="adaptive 模式下允许规则边界与高风险复杂查询使用 LLM 重排",
    )
    args = parser.parse_args()
    result = asyncio.run(evaluate(
        output=args.output,
        failures_output=args.failures_output,
        top_k=args.top_k,
        limit=args.limit,
        case_ids=args.case_ids,
        rerank_mode=args.rerank_mode,
        adaptive_llm=args.adaptive_llm,
        concurrency=args.concurrency,
        case_timeout_seconds=args.case_timeout_seconds,
        resume=args.resume,
    ))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 2 if result["summary"]["infrastructure_error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
