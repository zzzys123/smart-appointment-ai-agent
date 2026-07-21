"""
检索策略对比评估：Dense vs Hybrid(BM25 + RRF)

Hit@3 召回率在小知识库上区分度不足（两种策略都容易命中）。
本脚本用更细的指标对比两种策略的排序质量：
- first_hit_rank：第一个命中期望片段的文档排名（越小越好）
- MRR（Mean Reciprocal Rank）：1/first_hit_rank 的平均值

第三步重构后，直接通过 create_retriever(strategy) 切换策略（零代码改动），
展示可插拔检索器的能力。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from services.knowledge_service import KnowledgeService
from services.retriever import create_retriever
from evaluation.test_cases import RAG_RETRIEVAL_TEST_CASES


def _first_hit_rank(results, expected_fragments):
    """返回第一个命中期望片段的文档排名（1-based）；未命中返回 0。"""
    for rank, doc in enumerate(results, start=1):
        text = doc.get("content", "") + " " + " ".join(doc.get("keywords", []))
        if any(f in text for f in expected_fragments):
            return rank
    return 0


async def _load_documents():
    """初始化知识库（确保 embedding 就绪）并返回文档列表。"""
    ks = KnowledgeService()
    await ks.initialize()
    return ks.get_all_documents()


async def _run_strategy(strategy: str, documents, top_k: int = 3):
    """用指定策略的可插拔检索器跑一遍全部用例，返回指标与明细。"""
    retriever = create_retriever(strategy)  # 通过工厂切换策略，零代码改动
    retriever.build_index(documents)

    details = []
    hits = 0
    reciprocal_ranks = []

    for query, expected in RAG_RETRIEVAL_TEST_CASES:
        results = await retriever.search(query, top_k=top_k)
        rank = _first_hit_rank(results, expected)
        if rank > 0:
            hits += 1
            reciprocal_ranks.append(1.0 / rank)
        else:
            reciprocal_ranks.append(0.0)
        details.append({
            "query": query,
            "first_hit_rank": rank,
            "top1": results[0].get("content", "")[:40] if results else "(空)",
        })

    total = len(RAG_RETRIEVAL_TEST_CASES)
    return {
        "strategy": strategy,
        "recall": hits / total * 100 if total else 0,
        "mrr": sum(reciprocal_ranks) / total if total else 0,
        "details": details,
    }


async def main():
    top_k = 3
    documents = await _load_documents()
    dense = await _run_strategy("dense", documents, top_k)
    hybrid = await _run_strategy("hybrid", documents, top_k)

    print("=" * 78)
    print(f"检索策略对比 (Top-{top_k})   Dense vs Hybrid(BM25+RRF)")
    print("=" * 78)
    print(f"{'查询':<24} {'Dense首命中':>10} {'Hybrid首命中':>12}")
    print("-" * 78)
    for d, h in zip(dense["details"], hybrid["details"]):
        q = d["query"][:22]
        dr = d["first_hit_rank"] or "-"
        hr = h["first_hit_rank"] or "-"
        flag = ""
        if isinstance(dr, int) and isinstance(hr, int) and hr < dr:
            flag = "  ← Hybrid 排名更靠前"
        print(f"{q:<24} {str(dr):>10} {str(hr):>12}{flag}")

    print("-" * 78)
    print(f"{'召回率(Hit@3)':<24} {dense['recall']:>9.1f}% {hybrid['recall']:>11.1f}%")
    print(f"{'MRR (越高越好)':<24} {dense['mrr']:>10.3f} {hybrid['mrr']:>12.3f}")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
