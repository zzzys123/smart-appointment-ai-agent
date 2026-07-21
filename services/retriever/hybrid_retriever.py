# services/retriever/hybrid_retriever.py
"""
混合（Hybrid）检索器：Dense 向量召回 + BM25 稀疏召回 → RRF 融合。

- Dense 解决同义词/语义匹配
- BM25 解决专有名词/数字精确匹配
- RRF (Reciprocal Rank Fusion) 融合两路排名，兼顾查全与查准
"""

import logging
import re
import time
from typing import Dict, List, Optional, Tuple

import faiss
import jieba
import numpy as np
from rank_bm25 import BM25Okapi

from services.text_embedding import embed_input

from .base import BaseRetriever
from .trace import RetrievalTrace

logger = logging.getLogger(__name__)

# RRF 融合常数，经验值 60（参考 TREC RRF 论文）
RRF_K = 60


def _tokenize_zh(text: str) -> List[str]:
    """中文分词：jieba 切词 + 去除空白/纯符号 token，用于 BM25 稀疏检索。"""
    if not text:
        return []
    tokens = jieba.lcut(text)
    cleaned = []
    for tok in tokens:
        tok = tok.strip()
        if tok and not re.fullmatch(r"[\s\W_]+", tok):
            cleaned.append(tok.lower())
    return cleaned


class HybridRetriever(BaseRetriever):
    """混合检索器：向量 + BM25 + RRF 融合。"""

    def __init__(self):
        super().__init__()
        self.index = None
        self.document_ids: List[int] = []
        self.bm25 = None
        self.bm25_doc_ids: List[int] = []

    @property
    def name(self) -> str:
        return "hybrid"

    def build_index(self, documents: List[Dict]) -> None:
        self._cache_docs(documents)
        embeddings: List[List[float]] = []
        self.document_ids = []
        bm25_corpus: List[List[str]] = []
        self.bm25_doc_ids = []

        for doc in documents:
            emb = doc.get("embedding")
            if emb:
                embeddings.append(emb)
                self.document_ids.append(doc["id"])
            else:
                logger.warning(f"文档 {doc.get('id')} 缺少 embedding，已跳过向量索引")

            text_for_bm25 = f"{doc['content']} {' '.join(doc.get('keywords', []))}"
            bm25_corpus.append(_tokenize_zh(text_for_bm25))
            self.bm25_doc_ids.append(doc["id"])

        if embeddings:
            arr = np.array(embeddings).astype("float32")
            self.index = faiss.IndexFlatIP(arr.shape[1])
            self.index.add(arr)
            logger.info(f"[Hybrid] 向量索引构建完成，共 {len(embeddings)} 个向量")
        else:
            self.index = None
            logger.warning("[Hybrid] 没有可用向量，稠密索引为空")

        if bm25_corpus:
            self.bm25 = BM25Okapi(bm25_corpus)
            logger.info(f"[Hybrid] BM25 索引构建完成，共 {len(bm25_corpus)} 篇文档")
        else:
            self.bm25 = None
            logger.warning("[Hybrid] 没有可用语料，BM25 索引为空")

    def _recall(
        self, query: str, candidate_k: int, trace: Optional[RetrievalTrace] = None
    ) -> List[Tuple[int, float]]:
        candidate_n = min(max(candidate_k, 1), max(len(self.document_ids), len(self.bm25_doc_ids)))

        # 1. Dense 召回
        t0 = time.perf_counter()
        dense_ranking: List[int] = []
        if self.index is not None and self.document_ids:
            n = min(candidate_n, len(self.document_ids))
            query_array = np.array([embed_input(query)]).astype("float32")
            _, dense_indices = self.index.search(query_array, n)
            dense_ranking = [
                self.document_ids[idx]
                for idx in dense_indices[0]
                if 0 <= idx < len(self.document_ids)
            ]
        if trace is not None:
            trace.add_stage("dense_recall", dense_ranking, (time.perf_counter() - t0) * 1000)

        # 2. BM25 稀疏召回
        t1 = time.perf_counter()
        sparse_ranking: List[int] = []
        if self.bm25 is not None:
            bm25_scores = self.bm25.get_scores(_tokenize_zh(query))
            order = np.argsort(bm25_scores)[::-1][:candidate_n]
            sparse_ranking = [
                self.bm25_doc_ids[i] for i in order if bm25_scores[i] > 0
            ]
        if trace is not None:
            trace.add_stage("sparse_recall", sparse_ranking, (time.perf_counter() - t1) * 1000)

        # 3. RRF 融合：score = Σ 1 / (RRF_K + rank)，rank 从 1 开始
        t2 = time.perf_counter()
        fused: Dict[int, float] = {}
        for ranking in (dense_ranking, sparse_ranking):
            for rank, doc_id in enumerate(ranking, start=1):
                fused[doc_id] = fused.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)

        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        if trace is not None:
            trace.add_stage(
                "rrf_fusion", [d for d, _ in ranked], (time.perf_counter() - t2) * 1000
            )
        return ranked
