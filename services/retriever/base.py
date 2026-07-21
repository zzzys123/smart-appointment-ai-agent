# services/retriever/base.py
"""
检索器抽象基类

将"检索"从 KnowledgeService（负责数据管理）中解耦出来，做成可插拔组件。
子类只需实现两件事：
- build_index(documents)：根据文档构建自己的索引
- _recall(query, candidate_k)：粗排召回，返回 [(doc_id, score), ...]

通用的检索流程（候选池大小、分类过滤、可选精排 rerank、取 top_k）
在本基类的 search() 模板方法中统一实现，dense / hybrid 复用。
"""

import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from .trace import RetrievalTrace

logger = logging.getLogger(__name__)


class BaseRetriever(ABC):
    """检索器抽象接口。"""

    def __init__(self):
        # 文档缓存：doc_id -> 文档 dict（含 content/keywords/category 等）
        self.docs_by_id: Dict[int, Dict] = {}
        # 精排开关（默认关闭，避免增加实时查询延迟/额度）
        self.rerank_enabled = (
            os.getenv("RERANK_ENABLED", "false") or "false"
        ).strip().lower() in ("1", "true", "yes", "on")
        self._reranker = None  # 延迟初始化
        self.last_trace: Optional[RetrievalTrace] = None  # 最近一次检索的链路追踪

    # ---- 子类需实现 ----
    @property
    @abstractmethod
    def name(self) -> str:
        """检索策略名称，如 dense / hybrid。"""
        raise NotImplementedError

    @abstractmethod
    def build_index(self, documents: List[Dict]) -> None:
        """根据文档列表构建索引。documents 中每条应包含 id/content/keywords/embedding。"""
        raise NotImplementedError

    @abstractmethod
    def _recall(
        self, query: str, candidate_k: int, trace: Optional[RetrievalTrace] = None
    ) -> List[Tuple[int, float]]:
        """粗排召回，返回按分数降序的 [(doc_id, score), ...]。

        若传入 trace，子类应记录各召回子阶段（dense/sparse/fusion）到 trace 中。
        """
        raise NotImplementedError

    # ---- 通用能力 ----
    @property
    def num_docs(self) -> int:
        return len(self.docs_by_id)

    def _cache_docs(self, documents: List[Dict]) -> None:
        self.docs_by_id = {doc["id"]: doc for doc in documents}

    def _get_reranker(self):
        if self._reranker is None:
            from services.reranker import create_reranker
            self._reranker = create_reranker()
        return self._reranker

    async def search(self, query: str, top_k: int = 3, category: Optional[str] = None) -> List[Dict]:
        """检索模板方法：粗排召回 → 可选精排 rerank → 返回 top_k。

        全过程记录到 self.last_trace，便于链路可观测与坏 case 定位。
        """
        if not self.docs_by_id:
            logger.warning("检索器索引为空，请先调用 build_index()")
            return []

        trace = RetrievalTrace(query=query, strategy=self.name, top_k=top_k)
        t_start = time.perf_counter()
        try:
            rerank_on = self.rerank_enabled
            # 候选池大小：开启 rerank 时召回更多候选供精排；否则留少量过滤余量
            if rerank_on:
                recall_k = min(max(top_k * 4, 10), self.num_docs)
            else:
                recall_k = min(max(top_k * 2, top_k), self.num_docs)

            ranked_ids = self._recall(query, recall_k, trace)

            # 取回候选文档（拷贝，避免污染缓存），应用分类过滤
            candidates: List[Dict] = []
            for doc_id, score in ranked_ids:
                doc = self.docs_by_id.get(doc_id)
                if not doc:
                    continue
                if category and doc.get("category") != category:
                    continue
                doc = dict(doc)
                doc["score"] = float(score)
                candidates.append(doc)
                if len(candidates) >= recall_k:
                    break

            # 精排重排（可选）
            if rerank_on and candidates:
                before_ids = [d.get("id") for d in candidates]
                t_rerank = time.perf_counter()
                results = await self._get_reranker().rerank(query, candidates, top_k)
                trace.add_stage(
                    "rerank",
                    [d.get("id") for d in results],
                    (time.perf_counter() - t_rerank) * 1000,
                    before=before_ids[:10],
                )
            else:
                results = candidates[:top_k]
                for rank, doc in enumerate(results, start=1):
                    doc["rank"] = rank

            trace.final_doc_ids = [d.get("id") for d in results]
            trace.total_ms = (time.perf_counter() - t_start) * 1000
            self.last_trace = trace
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("\n" + trace.summary())

            return results

        except Exception as e:
            logger.error(f"检索失败: {e}")
            trace.total_ms = (time.perf_counter() - t_start) * 1000
            self.last_trace = trace
            return []
