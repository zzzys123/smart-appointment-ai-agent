"""
RAG 检索召回率评估

评估知识库向量检索的质量：
- 对每个查询，检查 top-K 结果是否包含期望的相关文档内容
- 输出整体召回率和每条查询的命中情况
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from services.knowledge_service import KnowledgeService
from evaluation.test_cases import RAG_RETRIEVAL_TEST_CASES


async def evaluate_rag_retrieval(top_k: int = 3):
    """运行 RAG 检索召回率评估"""
    print("=" * 60)
    print("📊 RAG 检索召回率评估")
    print(f"   检索 Top-K = {top_k}")
    print("=" * 60)
    
    # 初始化知识库服务
    print("\n🔄 正在初始化知识库服务...")
    knowledge_service = KnowledgeService()
    await knowledge_service.initialize()
    print("✅ 知识库初始化完成\n")
    
    # 统计数据
    total = len(RAG_RETRIEVAL_TEST_CASES)
    hits = 0
    results_detail = []
    
    print(f"共 {total} 条检索测试用例，开始评估...\n")
    
    for i, (query, expected_fragments) in enumerate(RAG_RETRIEVAL_TEST_CASES, 1):
        try:
            # 执行检索
            search_results = await knowledge_service.search(query, top_k=top_k)
            
            # 将检索到的文档内容合并成一个字符串，方便匹配
            retrieved_text = " ".join(
                doc.get("content", "") + " " + " ".join(doc.get("keywords", []))
                for doc in search_results
            )
            
            # 检查是否命中任意一个期望片段
            hit_fragments = [f for f in expected_fragments if f in retrieved_text]
            is_hit = len(hit_fragments) > 0
            
            if is_hit:
                hits += 1
                status = "✅"
            else:
                status = "❌"
            
            # 计算命中比例（命中了多少期望片段）
            hit_ratio = len(hit_fragments) / len(expected_fragments) * 100
            
            print(f"  [{i:02d}/{total}] {status} 查询: '{query[:35]}...'")
            print(f"           命中片段: {len(hit_fragments)}/{len(expected_fragments)} "
                  f"({hit_ratio:.0f}%) | 检索到 {len(search_results)} 条文档")
            
            results_detail.append({
                "query": query,
                "expected_fragments": expected_fragments,
                "hit_fragments": hit_fragments,
                "missed_fragments": [f for f in expected_fragments if f not in retrieved_text],
                "is_hit": is_hit,
                "hit_ratio": hit_ratio,
                "retrieved_count": len(search_results),
                "retrieved_contents": [doc.get("content", "")[:60] for doc in search_results]
            })
            
        except Exception as e:
            print(f"  [{i:02d}/{total}] ⚠️  查询: '{query[:35]}...' | 异常: {e}")
            results_detail.append({
                "query": query,
                "is_hit": False,
                "error": str(e)
            })
    
    # 输出汇总结果
    print("\n" + "=" * 60)
    print("📈 评估结果汇总")
    print("=" * 60)
    
    recall_rate = hits / total * 100 if total > 0 else 0
    print(f"\n  Top-{top_k} 召回率: {hits}/{total} = {recall_rate:.1f}%")
    
    # 计算平均片段命中率
    avg_hit_ratio = sum(
        r.get("hit_ratio", 0) for r in results_detail
    ) / total if total > 0 else 0
    print(f"  平均片段命中率: {avg_hit_ratio:.1f}%")
    
    # 输出未命中的查询
    missed = [r for r in results_detail if not r["is_hit"]]
    if missed:
        print(f"\n  ❌ 未命中的查询 ({len(missed)} 条):")
        for r in missed:
            print(f"    - 查询: '{r['query']}'")
            if "missed_fragments" in r:
                print(f"      缺失片段: {r['missed_fragments']}")
            if "retrieved_contents" in r:
                print(f"      实际检索到: {r['retrieved_contents']}")
    
    print("\n" + "=" * 60)
    
    return {
        "total": total,
        "hits": hits,
        "recall_rate": recall_rate,
        "avg_hit_ratio": avg_hit_ratio,
        "details": results_detail
    }


if __name__ == "__main__":
    result = asyncio.run(evaluate_rag_retrieval(top_k=3))
    
    if result["recall_rate"] < 70:
        print(f"\n⚠️  召回率 {result['recall_rate']:.1f}% 低于 70% 阈值")
        sys.exit(1)
    else:
        print(f"\n✅ 召回率 {result['recall_rate']:.1f}% 达标")
        sys.exit(0)
