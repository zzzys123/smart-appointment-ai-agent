"""
RAG 生成质量评估（Ragas 同款指标，自研 LLM-as-judge）

对每个问题：检索上下文 → 生成答案 → 用 LLM 裁判打两个分：
- Faithfulness（忠实度）：答案是否忠于检索上下文、有没有编造
- Answer Relevancy（答案相关性）：答案是否切题

建立"基于数据"的质量反馈回路，拒绝凭感觉调优。全程使用 Qwen。
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from config.model_provider import create_chat_model
from services.knowledge_service import KnowledgeService
from evaluation.rag_evaluator import RagJudge, generate_answer


# 评估用问题（真实咨询场景）
RAG_QUALITY_QUERIES = [
    "你们几点开门几点关门？",
    "全身推拿多少钱，多长时间？",
    "做肩颈推拿有什么好处？",
    "怎么办会员卡，有优惠吗？",
]


async def evaluate_rag_quality(sample_size: int = 4, top_k: int = 3):
    """运行 RAG 生成质量评估，返回 Faithfulness / Answer Relevancy 平均分。"""
    print("=" * 64)
    print("🧪 RAG 生成质量评估（Faithfulness / Answer Relevancy）")
    print("=" * 64)

    ks = KnowledgeService()
    await ks.initialize()
    judge = RagJudge()
    gen_llm = create_chat_model(temperature=0.3)

    queries = RAG_QUALITY_QUERIES[:sample_size]
    faith_scores, rel_scores, details = [], [], []

    for i, query in enumerate(queries, 1):
        docs = await ks.search(query, top_k=top_k)
        contexts = [d.get("content", "") for d in docs]
        answer = await generate_answer(gen_llm, query, contexts)

        faith = await judge.faithfulness(answer, contexts)
        rel = await judge.answer_relevancy(query, answer)

        faith_scores.append(faith["score"])
        rel_scores.append(rel["score"])
        details.append({"query": query, "answer": answer, "faith": faith, "rel": rel})

        print(f"\n[{i}/{len(queries)}] 问题: {query}")
        print(f"    答案: {answer[:60]}...")
        print(f"    忠实度 Faithfulness = {faith['score']:.2f} "
              f"({faith.get('supported', 0)}/{faith.get('total', 0)} 陈述被上下文支持)")
        print(f"    相关性 Answer Relevancy = {rel['score']:.2f}")

    n = len(queries)
    avg_faith = sum(faith_scores) / n if n else 0.0
    avg_rel = sum(rel_scores) / n if n else 0.0

    print("\n" + "=" * 64)
    print(f"  平均 Faithfulness（忠实度）:      {avg_faith:.2f}")
    print(f"  平均 Answer Relevancy（相关性）:  {avg_rel:.2f}")
    print("=" * 64)

    return {
        "faithfulness": avg_faith,
        "answer_relevancy": avg_rel,
        "total": n,
        "details": details,
    }


if __name__ == "__main__":
    result = asyncio.run(evaluate_rag_quality())
    # 忠实度低于 0.7 视为不达标（可能有幻觉）
    if result["faithfulness"] < 0.7:
        print(f"\n⚠️  Faithfulness {result['faithfulness']:.2f} 偏低，可能存在编造")
        sys.exit(1)
    print(f"\n✅ RAG 生成质量达标")
    sys.exit(0)
