r"""在当前数据库上比较 Dense 与 Hybrid 检索的严格证据指标。

用法：
    .venv\Scripts\python.exe evaluation\eval_retrieval_strategies.py
    .venv\Scripts\python.exe evaluation\eval_retrieval_strategies.py \
        --json-output evaluation\results\retrieval_strategies.json

评估中的“证据命中”要求候选分块同时满足：
1. source_id 与用例期望来源一致；
2. 具体分块的标题或正文包含 expected_contains。

这比仅判断来源是否出现在 Top-K 更严格，也更接近 RAG 上下文能否直接回答问题。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
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
from services.retriever import create_retriever


DEFAULT_CASES = ROOT / "evaluation" / "document_chunking_cases.json"


def _document_text(document: Dict[str, Any]) -> str:
    return "\n".join(
        part for part in (document.get("title"), document.get("content")) if part
    )


def _first_rank(results: Iterable[Dict[str, Any]], predicate) -> Optional[int]:
    for rank, document in enumerate(results, start=1):
        if predicate(document):
            return rank
    return None


def _at_k(rank: Optional[int], k: int) -> float:
    return float(rank is not None and rank <= k)


def _summarize(details: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(details)
    summary: Dict[str, Any] = {"case_count": total}
    for prefix in ("evidence", "source"):
        ranks = [item[f"{prefix}_rank"] for item in details]
        for k in (1, 3, 5, 10):
            summary[f"{prefix}_recall_at_{k}"] = (
                sum(_at_k(rank, k) for rank in ranks) / total if total else 0.0
            )
        summary[f"{prefix}_mrr_at_10"] = (
            sum(1.0 / rank if rank else 0.0 for rank in ranks) / total
            if total
            else 0.0
        )

    latencies = [item["latency_ms"] for item in details]
    summary["latency_ms_mean"] = statistics.fmean(latencies) if latencies else 0.0
    summary["latency_ms_p50"] = statistics.median(latencies) if latencies else 0.0
    summary["top3_context_chars_mean"] = (
        statistics.fmean(item["top3_context_chars"] for item in details)
        if details
        else 0.0
    )
    return summary


async def _evaluate_strategy(
    strategy: str,
    documents: List[Dict[str, Any]],
    cases: List[Dict[str, Any]],
) -> Dict[str, Any]:
    retriever = create_retriever(strategy)
    # Rerank 是独立变量，本次只比较旧 Dense 与新 Dense+BM25+RRF。
    retriever.rerank_enabled = False
    await asyncio.to_thread(retriever.build_index, documents)

    details: List[Dict[str, Any]] = []
    for case in cases:
        started = time.perf_counter()
        results = await retriever.search(case["query"], top_k=10)
        latency_ms = (time.perf_counter() - started) * 1000
        expected_source = case["expected_source_id"]
        expected_text = case["expected_contains"]

        source_rank = _first_rank(
            results, lambda doc: doc.get("source_id") == expected_source
        )
        evidence_rank = _first_rank(
            results,
            lambda doc: (
                doc.get("source_id") == expected_source
                and expected_text in _document_text(doc)
            ),
        )
        details.append(
            {
                "id": case["id"],
                "group": case["group"],
                "query": case["query"],
                "expected_source_id": expected_source,
                "expected_contains": expected_text,
                "source_rank": source_rank,
                "evidence_rank": evidence_rank,
                "latency_ms": round(latency_ms, 2),
                "top3_context_chars": sum(
                    len(_document_text(document)) for document in results[:3]
                ),
                "top3": [
                    {
                        "source_id": document.get("source_id"),
                        "chunk_index": document.get("chunk_index"),
                        "title": document.get("title"),
                    }
                    for document in results[:3]
                ],
            }
        )

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for detail in details:
        grouped[detail["group"]].append(detail)

    return {
        "strategy": strategy,
        "summary": _summarize(details),
        "groups": {name: _summarize(items) for name, items in sorted(grouped.items())},
        "details": details,
    }


def _print_summary(result: Dict[str, Any]) -> None:
    metrics = result["summary"]
    print(f"\n{result['strategy'].upper()}")
    print(
        "  Evidence R@1/R@3/R@5/R@10: "
        + "/".join(
            f"{metrics[f'evidence_recall_at_{k}'] * 100:.1f}%"
            for k in (1, 3, 5, 10)
        )
    )
    print(f"  Evidence MRR@10: {metrics['evidence_mrr_at_10']:.3f}")
    print(
        f"  Latency mean/p50: {metrics['latency_ms_mean']:.1f}/"
        f"{metrics['latency_ms_p50']:.1f} ms"
    )


async def _main(args: argparse.Namespace) -> Dict[str, Any]:
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    service = KnowledgeService(db_path=args.db_url)
    documents = await asyncio.to_thread(service.get_all_documents)
    if not documents:
        raise RuntimeError("数据库中没有有效知识，请先运行文档导入脚本。")

    source_ids = {document.get("source_id") for document in documents}
    missing_sources = sorted(
        {case["expected_source_id"] for case in cases} - source_ids
    )
    if missing_sources:
        raise RuntimeError(
            "数据库缺少评估来源，请先导入新增知识文档：" + ", ".join(missing_sources)
        )
    missing_embeddings = sum(not document.get("embedding") for document in documents)
    if missing_embeddings:
        raise RuntimeError(f"有 {missing_embeddings} 条知识缺少 embedding，无法公平比较。")

    results = []
    for strategy in ("dense", "hybrid"):
        print(f"正在评估 {strategy}（{len(cases)} 条查询）...")
        results.append(await _evaluate_strategy(strategy, documents, cases))

    payload = {
        "evaluation": "dense_vs_hybrid_strict_evidence",
        "database_url": args.db_url,
        "document_count": len(documents),
        "source_count": len(source_ids - {None}),
        "case_count": len(cases),
        "top_k": 10,
        "rerank_enabled": False,
        "strategies": results,
    }
    for result in results:
        _print_summary(result)

    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON 结果：{args.json_output}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--db-url", default="sqlite:///data/smart_appointment.db", help="知识库数据库 URL"
    )
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
