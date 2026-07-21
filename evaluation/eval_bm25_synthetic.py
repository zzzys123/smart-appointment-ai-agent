"""
BM25 增益的受控演示（合成高混淆知识库）

现实结论：主知识库仅 10 条、主题区分度高，纯向量已饱和，无法体现 BM25 价值。
本脚本构造一个"高混淆"合成场景来暴露纯向量的短板并验证 BM25+RRF 的增益：

  - 40 条技师简介，模板完全一致，仅"工号 + 姓名"不同 → Embedding 向量高度相似，
    纯向量对"精确编号"这类查询几乎无法区分（数字信号被淹没）。
  - BM25 稀疏检索对精确 token（工号数字）能精准命中。
  - Hybrid(RRF) 融合后应接近 BM25 的精度，同时保留语义泛化能力。

本脚本自包含、不写入主数据库，直接在内存构建 FAISS + BM25，
复用与 knowledge_service 一致的 RRF 逻辑（RRF_K）。会调用 Embedding API。
"""

import os
import sys

import faiss
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from rank_bm25 import BM25Okapi

from services.retriever import RRF_K, _tokenize_zh
from services.text_embedding import embed_input


# ---- 构造合成知识库：模板一致，仅工号/姓名不同（制造高混淆）----
NAMES = [
    "张伟", "李娜", "王芳", "刘洋", "陈静", "杨磊", "赵敏", "黄강", "周杰", "吴迪",
    "徐静", "孙浩", "马丽", "朱峰", "胡玥", "郭超", "林悦", "何鑫", "高翔", "罗琳",
    "梁爽", "宋涛", "唐宁", "冯雪", "董浩", "萧然", "程曦", "曹阳", "袁媛", "邓超",
    "许巍", "傅盈", "沈冰", "曾毅", "彭涛", "吕布", "苏婉", "卢俊", "蒋雯", "蔡明",
]


def build_corpus():
    docs = []
    for i, name in enumerate(NAMES, start=1):
        content = (
            f"技师{name}，工号{i}号，擅长全身推拿、肩颈按摩与足底按摩，"
            f"服务态度亲切，经验丰富，深受顾客好评。"
        )
        docs.append({"id": i, "content": content, "employee_no": i, "name": name})
    return docs


def rrf_fuse(dense_ids, sparse_ids):
    fused = {}
    for ranking in (dense_ids, sparse_ids):
        for rank, doc_id in enumerate(ranking, start=1):
            fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)
    return [doc_id for doc_id, _ in sorted(fused.items(), key=lambda kv: kv[1], reverse=True)]


def first_hit_rank(ranked_ids, expected_id, docs_by_id, top_k):
    for rank, doc_id in enumerate(ranked_ids[:top_k], start=1):
        if doc_id == expected_id:
            return rank
    return 0


def main():
    top_k = 3
    candidate_n = 10
    docs = build_corpus()
    docs_by_id = {d["id"]: d for d in docs}

    print(f"构建合成知识库：{len(docs)} 条技师简介（模板一致，仅工号/姓名不同）")
    print("正在生成 Embedding（调用 API）...")

    # 建 FAISS
    embeddings = np.array([embed_input(d["content"]) for d in docs]).astype("float32")
    index = faiss.IndexFlatIP(embeddings.shape[1])
    index.add(embeddings)
    doc_ids = [d["id"] for d in docs]

    # 建 BM25
    bm25 = BM25Okapi([_tokenize_zh(d["content"]) for d in docs])

    # 查询：精确工号（纯向量的弱项）
    queries = [
        ("工号7号的技师", 7),
        ("我要找17号技师", 17),
        ("23号师傅在吗", 23),
        ("预约工号31", 31),
        ("38号技师怎么样", 38),
        ("5号技师", 5),
    ]

    mrr = {"dense": [], "sparse": [], "hybrid": []}
    rows = []

    for q, expected_id in queries:
        # Dense
        q_emb = np.array([embed_input(q)]).astype("float32")
        _, idx = index.search(q_emb, candidate_n)
        dense_ids = [doc_ids[i] for i in idx[0] if 0 <= i < len(doc_ids)]
        # Sparse
        scores = bm25.get_scores(_tokenize_zh(q))
        order = np.argsort(scores)[::-1]
        sparse_ids = [doc_ids[i] for i in order if scores[i] > 0][:candidate_n]
        # Hybrid
        hybrid_ids = rrf_fuse(dense_ids, sparse_ids)

        r_d = first_hit_rank(dense_ids, expected_id, docs_by_id, top_k)
        r_s = first_hit_rank(sparse_ids, expected_id, docs_by_id, top_k)
        r_h = first_hit_rank(hybrid_ids, expected_id, docs_by_id, top_k)

        for name, r in (("dense", r_d), ("sparse", r_s), ("hybrid", r_h)):
            mrr[name].append(1.0 / r if r > 0 else 0.0)
        rows.append((q, r_d, r_s, r_h))

    print("\n" + "=" * 72)
    print(f"高混淆库检索对比 (Top-{top_k})   首命中排名 (- 未命中, 越小越好)")
    print("=" * 72)
    print(f"{'查询':<20}{'Dense':>9}{'Sparse':>9}{'Hybrid':>9}")
    print("-" * 72)
    for q, rd, rs, rh in rows:
        fd = str(rd) if rd else "-"
        fs = str(rs) if rs else "-"
        fh = str(rh) if rh else "-"
        note = "  ← Hybrid 修复了 Dense 的漏检" if rh and (not rd or rh < rd) else ""
        print(f"{q:<20}{fd:>9}{fs:>9}{fh:>9}{note}")
    print("-" * 72)
    n = len(queries)
    rec = lambda k: sum(1 for x in mrr[k] if x > 0) / n * 100
    print(f"{'召回率(Hit@3)':<20}{rec('dense'):>8.0f}%{rec('sparse'):>8.0f}%{rec('hybrid'):>8.0f}%")
    print(f"{'MRR':<20}{sum(mrr['dense'])/n:>9.3f}{sum(mrr['sparse'])/n:>9.3f}{sum(mrr['hybrid'])/n:>9.3f}")
    print("=" * 72)


if __name__ == "__main__":
    main()
