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

import asyncio
import contextvars
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from .trace import RetrievalTrace
from services.rerank_routing import (
    AdaptiveRerankPolicy,
    RerankDecision,
    fuse_rerank_scores,
)

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
        self._rerankers: Dict[str, object] = {}
        self._recall_lock = asyncio.Lock()
        self._rerank_policy = AdaptiveRerankPolicy()
        self._trace_context: contextvars.ContextVar[Optional[RetrievalTrace]] = (
            contextvars.ContextVar(f"retrieval_trace_{id(self)}", default=None)
        )
        self.last_trace: Optional[RetrievalTrace] = None  # 最近一次检索的链路追踪
        # 子类在每次 recall 时填充原始通道分数，供无答案判定使用。
        # RRF 分数只表达名次融合，不能直接充当相关度阈值。
        self._last_recall_metadata: Dict[int, Dict] = {}

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

    def _get_reranker(self, provider: Optional[str] = None):
        # Compatibility for tests and callers that inject a reranker directly.
        if self._reranker is not None:
            return self._reranker
        key = (provider or "default").strip().lower()
        if key in self._rerankers:
            return self._rerankers[key]
        from services.reranker import create_reranker
        reranker = create_reranker(provider)
        self._rerankers[key] = reranker
        return reranker

    def get_current_trace(self) -> Optional[RetrievalTrace]:
        """Return the current task's trace, safe under concurrent requests."""
        return self._trace_context.get() or self.last_trace

    def _legacy_rerank_decision(self) -> RerankDecision:
        return RerankDecision(
            True,
            os.getenv("RERANKER_PROVIDER", "llm"),
            "legacy_rerank_enabled",
            "unknown",
        )

    def _publish_trace(self, trace: RetrievalTrace) -> None:
        self.last_trace = trace
        self._trace_context.set(trace)

    def _get_reranker_legacy(self):
        """Deprecated alias retained for external extensions."""
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
            candidate_pool_on = (
                self.rerank_enabled
                or self._rerank_policy.needs_candidate_pool_for(query)
            )
            # 候选池大小：开启 rerank 时召回更多候选供精排；否则留少量过滤余量
            if category:
                # 分类过滤发生在召回之后，必须先覆盖完整语料，否则目标分类可能
                # 因全局排名靠后而被提前截断。
                recall_k = self.num_docs
            elif candidate_pool_on:
                recall_k = min(max(top_k * 4, 10), self.num_docs)
            else:
                recall_k = min(max(top_k * 2, top_k), self.num_docs)

            # Embedding clients are synchronous. Run recall off the event loop
            # so request-level timeouts and unrelated requests remain responsive.
            # The lock also protects per-call channel metadata on this shared retriever.
            async with self._recall_lock:
                ranked_ids = await asyncio.to_thread(
                    self._recall, query, recall_k, trace
                )
                recall_metadata = dict(self._last_recall_metadata)

            candidate_limit = min(
                max(top_k * 4, 10) if candidate_pool_on else top_k,
                self.num_docs,
            )

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
                doc["retrieval"] = {
                    "strategy": self.name,
                    "score": float(score),
                    **recall_metadata.get(doc_id, {}),
                }
                candidates.append(doc)
                if len(candidates) >= candidate_limit:
                    break

            if self._rerank_policy.mode != "off":
                decision = self._rerank_policy.decide(query, candidates)
            elif self.rerank_enabled and candidates:
                decision = self._legacy_rerank_decision()
            else:
                decision = RerankDecision(
                    False, None, "rerank_disabled", "unknown"
                )
            trace.route = decision.to_dict()

            # 精排重排（可选）：所有候选参与精排和融合，之后再截断 top_k。
            if decision.enabled and candidates:
                before_ids = [d.get("id") for d in candidates]
                t_rerank = time.perf_counter()
                timeout_seconds = float(os.getenv("RAG_RERANK_TIMEOUT_SECONDS", "20"))
                fallback_reason = ""
                try:
                    reranked = await asyncio.wait_for(
                        self._get_reranker(decision.provider).rerank(
                            query, candidates, len(candidates)
                        ),
                        timeout=timeout_seconds,
                    )
                    if any(doc.get("rerank_fallback") for doc in reranked):
                        fallback_reason = "provider_error"
                        ranked = candidates
                    else:
                        ranked = fuse_rerank_scores(candidates, reranked)
                except asyncio.TimeoutError:
                    fallback_reason = "timeout"
                    ranked = candidates
                except Exception as exc:
                    fallback_reason = f"error:{exc}"
                    logger.exception("精排异常，回退到 Hybrid 粗排")
                    ranked = candidates

                results = [dict(document) for document in ranked[:top_k]]
                observed_candidates = ranked
                for rank, document in enumerate(results, start=1):
                    document["rank"] = rank
                    if fallback_reason:
                        document["rerank_fallback"] = True
                        document["rerank_error"] = fallback_reason
                trace.route["fallback"] = bool(fallback_reason)
                trace.route["fallback_reason"] = fallback_reason or None
                trace.add_stage(
                    "rerank",
                    [d.get("id") for d in results],
                    (time.perf_counter() - t_rerank) * 1000,
                    before=before_ids[:10],
                    provider=decision.provider,
                    fallback=fallback_reason or None,
                )
            else:
                results = candidates[:top_k]
                observed_candidates = candidates
                for rank, doc in enumerate(results, start=1):
                    doc["rank"] = rank

            trace.final_doc_ids = [d.get("id") for d in results]
            trace.record_candidates(observed_candidates)
            trace.outcome = "retrieved"
            trace.total_ms = (time.perf_counter() - t_start) * 1000
            self._publish_trace(trace)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("\n" + trace.summary())

            return results

        except Exception as e:
            logger.error(f"检索失败: {e}")
            trace.total_ms = (time.perf_counter() - t_start) * 1000
            trace.outcome = "error"
            trace.error = str(e)
            self._publish_trace(trace)
            from .observability import emit_retrieval_trace
            emit_retrieval_trace(trace)
            return []
