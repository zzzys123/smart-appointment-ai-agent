r"""在同一 Golden Set 上比较召回与重排策略。

默认运行不产生 Chat API 费用的三组：Dense、Hybrid、Hybrid + Cross-Encoder。
传入 ``--include-llm`` 后额外运行 Hybrid + LLM Rerank；该模式会把候选知识
文本发送到已配置的 Chat API。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from services.knowledge_service import KnowledgeService
from services.reranker import create_reranker
from services.retriever import create_retriever


DEFAULT_CASES = ROOT / "evaluation" / "document_chunking_cases.json"
DEFAULT_OUTPUT = ROOT / "evaluation" / "results" / "reranker_strategies.json"


def _document_text(document: Dict[str, Any]) -> str:
    return "\n".join(
        part for part in (document.get("title"), document.get("content")) if part
    )


def _first_rank(results: Iterable[Dict[str, Any]], predicate) -> Optional[int]:
    for rank, document in enumerate(results, start=1):
        if predicate(document):
            return rank
    return None


def _summarize(details: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(details)
    result: Dict[str, Any] = {"case_count": total}
    ranks = [item["evidence_rank"] for item in details]
    for k in (1, 3, 5, 10):
        result[f"evidence_recall_at_{k}"] = (
            sum(rank is not None and rank <= k for rank in ranks) / total
            if total
            else 0.0
        )
    result["evidence_mrr_at_10"] = (
        sum(1.0 / rank if rank and rank <= 10 else 0.0 for rank in ranks) / total
        if total
        else 0.0
    )
    latencies = [item["latency_ms"] for item in details]
    result["latency_ms_mean"] = statistics.fmean(latencies) if latencies else 0.0
    result["latency_ms_p50"] = statistics.median(latencies) if latencies else 0.0
    result["latency_ms_p95"] = (
        sorted(latencies)[min(len(latencies) - 1, int(len(latencies) * 0.95))]
        if latencies
        else 0.0
    )
    return result


async def _evaluate(
    name: str,
    retrieval_strategy: str,
    documents: List[Dict[str, Any]],
    cases: List[Dict[str, Any]],
    reranker_provider: Optional[str] = None,
) -> Dict[str, Any]:
    retriever = create_retriever(retrieval_strategy)
    await asyncio.to_thread(retriever.build_index, documents)
    retriever.rerank_enabled = reranker_provider is not None
    if reranker_provider:
        retriever._reranker = create_reranker(reranker_provider)

    details = []
    for index, case in enumerate(cases, start=1):
        started = time.perf_counter()
        results = await retriever.search(case["query"], top_k=10)
        latency_ms = (time.perf_counter() - started) * 1000
        expected_source = case["expected_source_id"]
        expected_text = case["expected_contains"]
        evidence_rank = _first_rank(
            results,
            lambda document: (
                document.get("source_id") == expected_source
                and expected_text in _document_text(document)
            ),
        )
        details.append(
            {
                "id": case["id"],
                "group": case["group"],
                "query": case["query"],
                "expected_source_id": expected_source,
                "expected_contains": expected_text,
                "evidence_rank": evidence_rank,
                "latency_ms": round(latency_ms, 2),
                "top3": [
                    {
                        "source_id": document.get("source_id"),
                        "chunk_index": document.get("chunk_index"),
                        "title": document.get("title"),
                        "rerank_score": document.get("rerank_score"),
                    }
                    for document in results[:3]
                ],
            }
        )
        print(f"[{name}] {index}/{len(cases)} {case['id']}")

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for detail in details:
        grouped[detail["group"]].append(detail)
    return {
        "strategy": name,
        "retrieval_strategy": retrieval_strategy,
        "reranker_provider": reranker_provider,
        "summary": _summarize(details),
        "groups": {
            group: _summarize(group_details)
            for group, group_details in sorted(grouped.items())
        },
        "details": details,
    }


def _print_table(results: List[Dict[str, Any]]) -> None:
    print("\n" + "=" * 108)
    print(
        f"{'策略':<28}{'R@1':>9}{'R@3':>9}{'R@5':>9}{'R@10':>9}"
        f"{'MRR':>9}{'平均ms':>12}{'P95ms':>12}"
    )
    print("-" * 108)
    for result in results:
        summary = result["summary"]
        print(
            f"{result['strategy']:<28}"
            f"{summary['evidence_recall_at_1']:>8.1%}"
            f"{summary['evidence_recall_at_3']:>9.1%}"
            f"{summary['evidence_recall_at_5']:>9.1%}"
            f"{summary['evidence_recall_at_10']:>9.1%}"
            f"{summary['evidence_mrr_at_10']:>9.3f}"
            f"{summary['latency_ms_mean']:>12.1f}"
            f"{summary['latency_ms_p95']:>12.1f}"
        )
    print("=" * 108)


async def _main(args: argparse.Namespace) -> None:
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    service = KnowledgeService(db_path=args.db_url)
    documents = await asyncio.to_thread(service.get_all_documents)
    if len(documents) != 90:
        raise RuntimeError(f"基线要求90条活动知识，当前为 {len(documents)} 条")

    configurations = [
        ("dense", "dense", None),
        ("hybrid", "hybrid", None),
        ("hybrid+cross-encoder", "hybrid", "cross-encoder"),
    ]
    if args.include_llm:
        configurations.append(("hybrid+llm", "hybrid", "llm"))

    results = []
    for name, retrieval_strategy, reranker_provider in configurations:
        results.append(
            await _evaluate(
                name,
                retrieval_strategy,
                documents,
                cases,
                reranker_provider,
            )
        )

    payload = {
        "evaluation": "retrieval_and_reranker_strategies",
        "database_url": args.db_url,
        "document_count": len(documents),
        "case_count": len(cases),
        "top_k": 10,
        "results": results,
    }
    _print_table(results)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"JSON 结果：{args.json_output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--db-url", default="sqlite:///data/smart_appointment.db")
    parser.add_argument("--json-output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--include-llm", action="store_true")
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
