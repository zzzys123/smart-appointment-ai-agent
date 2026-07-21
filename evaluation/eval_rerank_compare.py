"""
Rerank 精排对比评估：Hybrid vs Hybrid + LLM Rerank

在"粗排召回"之后加一层 LLM 精排，验证重排对 top-K 精度的影响。
用例侧重"话题相关但意图不同"的查询（如问价格 vs 问效果），
这类最考验重排器对用户意图的语义理解。

指标：first_hit_rank（第一个命中期望片段的排名，越小越好）与 MRR。
会调用 Embedding + Chat（Qwen）API。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from services.knowledge_service import KnowledgeService
from services.retriever import create_retriever


# (查询, 期望命中片段) —— 片段刻意选唯一定位正确文档的精确内容
RERANK_TEST_CASES = [
    ("全身推拿多少钱",        ["120元"]),   # 正确=服务项目doc(含价格)，干扰=全身推拿效果doc
    ("肩颈推拿怎么收费",      ["80元"]),
    ("足底按摩价格贵不贵",    ["100元"]),
    ("背部推拿多少钱一次",    ["90元"]),
    ("做全身推拿有什么好处",  ["缓解压力"]),  # 正确=效果doc，反向校验rerank不乱排
    ("肩颈按摩能缓解颈椎痛吗", ["颈椎"]),
]


def _first_hit_rank(results, expected_fragments):
    for rank, doc in enumerate(results, start=1):
        text = doc.get("content", "") + " " + " ".join(doc.get("keywords", []))
        if any(f in text for f in expected_fragments):
            return rank
    return 0


async def _run(documents, rerank_enabled: bool, top_k: int = 3):
    # 构建 hybrid 检索器，切换 rerank 开关（可插拔重排）
    retriever = create_retriever("hybrid")
    retriever.build_index(documents)
    retriever.rerank_enabled = rerank_enabled

    details, rr = [], []
    for query, expected in RERANK_TEST_CASES:
        results = await retriever.search(query, top_k=top_k)
        rank = _first_hit_rank(results, expected)
        rr.append(1.0 / rank if rank > 0 else 0.0)
        details.append((query, rank, results[0].get("content", "")[:28] if results else "(空)"))
    n = len(RERANK_TEST_CASES)
    return {"mrr": sum(rr) / n, "recall": sum(1 for x in rr if x > 0) / n * 100, "details": details}


async def main():
    top_k = 3
    ks = KnowledgeService()
    await ks.initialize()
    documents = ks.get_all_documents()

    print("运行粗排基线 (Hybrid)...")
    base = await _run(documents, False, top_k)
    print("运行精排 (Hybrid + LLM Rerank)，调用 Qwen 中...")
    rerank = await _run(documents, True, top_k)

    print("\n" + "=" * 80)
    print(f"Rerank 精排对比 (Top-{top_k})   首命中排名 (- 未命中, 越小越好)")
    print("=" * 80)
    print(f"{'查询':<24}{'Hybrid':>8}{'+Rerank':>9}   {'Rerank后Top-1文档'}")
    print("-" * 80)
    for (q, rb, t1b), (_, rr_, t1r) in zip(base["details"], rerank["details"]):
        fb = str(rb) if rb else "-"
        fr = str(rr_) if rr_ else "-"
        flag = "  ←提升" if rr_ and (not rb or rr_ < rb) else ("  ←下降" if rb and (not rr_ or rr_ > rb) else "")
        print(f"{q:<24}{fb:>8}{fr:>9}   {t1r}{flag}")
    print("-" * 80)
    print(f"{'召回率(Hit@3)':<24}{base['recall']:>7.0f}%{rerank['recall']:>8.0f}%")
    print(f"{'MRR':<24}{base['mrr']:>8.3f}{rerank['mrr']:>9.3f}")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
