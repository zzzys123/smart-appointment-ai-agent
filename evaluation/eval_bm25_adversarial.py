"""
BM25 对抗性验证：Dense vs Sparse(BM25) vs Hybrid(RRF)

目的：现有 RAG 测试集主题区分度高、纯向量已饱和（MRR=1.0），
无法体现混合检索价值。本脚本构造一组"精确匹配"类查询——
价格数字、门牌号、地铁线路、专有名词——这类正是纯向量(Embedding)的
弱项、BM25 稀疏检索的强项，用来验证融合后的增益。

指标：first_hit_rank（第一个命中期望片段的排名，越小越好）与 MRR。
"""

import asyncio
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from services.knowledge_service import KnowledgeService
from services.retriever import create_retriever, _tokenize_zh


# 对抗性用例：(查询, 期望命中的文档内容片段列表)
# 均刻意使用精确 token（数字/门牌号/专有名词），弱化语义信号
ADVERSARIAL_TEST_CASES = [
    ("27号",                       ["中关村大街27号", "地址"]),
    ("2号线A口向北100米",          ["地铁2号线A口", "100米"]),
    ("充1000送多少",               ["1000元送150元", "会员"]),
    ("500送50",                    ["500元送50元", "会员"]),
    ("80元30分钟是哪个项目",       ["肩颈推拿（80元/30分钟）", "80元"]),
    ("90元40分钟",                 ["背部推拿（90元/40分钟）", "90元"]),
    ("100元45分钟的服务",          ["足底按摩（100元/45分钟）", "100元"]),
    ("提前2小时",                  ["提前至少2小时", "取消"]),
    ("3年以上经验",                ["3年以上的专业经验", "培训"]),
    ("海淀区",                     ["北京海淀区", "地址"]),
]


def _first_hit_rank(doc_dicts, expected_fragments):
    """返回第一个命中期望片段的文档排名（1-based）；未命中返回 0。"""
    for rank, doc in enumerate(doc_dicts, start=1):
        text = doc.get("content", "") + " " + " ".join(doc.get("keywords", []))
        if any(f in text for f in expected_fragments):
            return rank
    return 0


def _sparse_docs(hybrid_retriever, query, top_k):
    """纯 BM25 排序结果（复用 hybrid 检索器内部的 BM25 索引，用于三方对比）。"""
    tokens = _tokenize_zh(query)
    scores = hybrid_retriever.bm25.get_scores(tokens)
    order = np.argsort(scores)[::-1]
    ids = [hybrid_retriever.bm25_doc_ids[i] for i in order if scores[i] > 0][:top_k]
    return [hybrid_retriever.docs_by_id.get(doc_id) for doc_id in ids]


async def main():
    top_k = 3
    # 初始化知识库（确保 embedding 就绪）并取文档，构建两种可插拔检索器
    ks = KnowledgeService()
    await ks.initialize()
    documents = ks.get_all_documents()

    dense_retriever = create_retriever("dense")
    dense_retriever.build_index(documents)
    hybrid_retriever = create_retriever("hybrid")
    hybrid_retriever.build_index(documents)

    rows = []
    mrr = {"dense": [], "sparse": [], "hybrid": []}

    for query, expected in ADVERSARIAL_TEST_CASES:
        dense = await dense_retriever.search(query, top_k=top_k)
        sparse = _sparse_docs(hybrid_retriever, query, top_k)
        hybrid = await hybrid_retriever.search(query, top_k=top_k)

        r_dense = _first_hit_rank(dense, expected)
        r_sparse = _first_hit_rank(sparse, expected)
        r_hybrid = _first_hit_rank(hybrid, expected)

        for name, r in (("dense", r_dense), ("sparse", r_sparse), ("hybrid", r_hybrid)):
            mrr[name].append(1.0 / r if r > 0 else 0.0)

        rows.append((query, r_dense, r_sparse, r_hybrid))

    print("=" * 76)
    print(f"BM25 对抗性验证 (Top-{top_k})   首命中排名 (- 表示未命中, 越小越好)")
    print("=" * 76)
    print(f"{'查询':<22}{'Dense':>8}{'Sparse':>8}{'Hybrid':>8}")
    print("-" * 76)
    for query, rd, rs, rh in rows:
        fd = str(rd) if rd else "-"
        fs = str(rs) if rs else "-"
        fh = str(rh) if rh else "-"
        note = ""
        if rh and (not rd or rh < rd):
            note = "  ← Hybrid 优于 Dense"
        print(f"{query:<22}{fd:>8}{fs:>8}{fh:>8}{note}")
    print("-" * 76)

    n = len(ADVERSARIAL_TEST_CASES)
    def _recall(name):
        return sum(1 for x in mrr[name] if x > 0) / n * 100
    print(f"{'召回率(Hit@3)':<22}{_recall('dense'):>7.0f}%{_recall('sparse'):>7.0f}%{_recall('hybrid'):>7.0f}%")
    print(f"{'MRR':<22}{sum(mrr['dense'])/n:>8.3f}{sum(mrr['sparse'])/n:>8.3f}{sum(mrr['hybrid'])/n:>8.3f}")
    print("=" * 76)


if __name__ == "__main__":
    asyncio.run(main())
