# services/retriever/dense_retriever.py
"""纯向量（Dense）检索器：FAISS 内积相似度。"""

import logging
import time
from typing import Dict, List, Optional, Tuple

import faiss
import numpy as np

from services.text_embedding import embed_input

from .base import BaseRetriever
from .trace import RetrievalTrace

logger = logging.getLogger(__name__)


class DenseRetriever(BaseRetriever):
    """基于 FAISS 的稠密向量检索。"""

    def __init__(self):
        super().__init__()
        self.index = None
        self.document_ids: List[int] = []

    @property
    def name(self) -> str:
        return "dense"

    def build_index(self, documents: List[Dict]) -> None:
        self._cache_docs(documents)
        embeddings: List[List[float]] = []
        self.document_ids = []

        for doc in documents:
            emb = doc.get("embedding")
            if emb:
                embeddings.append(emb)
                self.document_ids.append(doc["id"])
            else:
                logger.warning(f"文档 {doc.get('id')} 缺少 embedding，已跳过向量索引")

        if embeddings:
            arr = np.array(embeddings).astype("float32")
            self.index = faiss.IndexFlatIP(arr.shape[1])
            self.index.add(arr)
            logger.info(f"[Dense] 向量索引构建完成，共 {len(embeddings)} 个向量")
        else:
            self.index = None
            logger.warning("[Dense] 没有可用向量，索引为空")

    def _recall(
        self, query: str, candidate_k: int, trace: Optional[RetrievalTrace] = None
    ) -> List[Tuple[int, float]]:
        if self.index is None or not self.document_ids:
            return []
        t0 = time.perf_counter()
        query_array = np.array([embed_input(query)]).astype("float32")
        n = min(max(candidate_k, 1), len(self.document_ids))
        scores, indices = self.index.search(query_array, n)
        ranked = [
            (self.document_ids[idx], float(score))
            for score, idx in zip(scores[0], indices[0])
            if 0 <= idx < len(self.document_ids)
        ]
        if trace is not None:
            trace.add_stage(
                "dense_recall", [d for d, _ in ranked], (time.perf_counter() - t0) * 1000
            )
        return ranked
