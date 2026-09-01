"""
知识检索器

负责从知识库中检索相关信息
"""

from typing import List, Dict, Any, Optional
from services.knowledge_service import KnowledgeService, get_shared_knowledge_service
from services.retrieval_relevance import RetrievalRelevancePolicy
from services.retriever.observability import emit_retrieval_trace


class KnowledgeRetriever:
    """知识检索器"""
    
    def __init__(self):
        self.knowledge_service: Optional[KnowledgeService] = None
        self.kb_initialized = False
        self.relevance_policy = RetrievalRelevancePolicy()
    
    async def initialize(self):
        """初始化知识库服务"""
        if not self.kb_initialized:
            self.knowledge_service = await get_shared_knowledge_service()
            self.kb_initialized = True
            print("✅ 咨询机器人知识库服务已初始化")
    
    async def search_knowledge(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """搜索相关知识"""
        # 确保知识库已初始化
        if not self.kb_initialized:
            await self.initialize()
        if self.knowledge_service is None:
            return []
        
        # 搜索相关知识
        recalled_docs = await self.knowledge_service.search(query, top_k=top_k)
        relevant_docs, gate_diagnostics = self.relevance_policy.filter_with_diagnostics(
            recalled_docs or []
        )
        trace = self.knowledge_service.get_last_trace()
        if trace is not None:
            trace.record_evidence_gate(
                recalled_docs or [],
                relevant_docs,
                diagnostics=gate_diagnostics,
                thresholds=self.relevance_policy.thresholds,
            )
            emit_retrieval_trace(trace)
        
        # 记录检索日志（含结构化链路追踪）
        self._log_search_results(query, relevant_docs)

        if recalled_docs and not relevant_docs:
            print(
                "⚠️ 知识库检索: 候选均低于证据阈值，"
                f"query={query!r}, dense>={self.relevance_policy.dense_min_score}, "
                f"bm25>={self.relevance_policy.bm25_min_score}"
            )
        
        return relevant_docs or []
    
    def _log_search_results(self, query: str, relevant_docs: List[Dict[str, Any]]):
        """记录搜索结果日志，并打印检索链路追踪（可观测）。"""
        # 打印结构化链路追踪：dense/sparse 召回 → RRF 融合 → rerank 各阶段
        trace = (
            self.knowledge_service.get_last_trace()
            if self.knowledge_service is not None
            else None
        )
        if trace is not None:
            print(trace.summary())

        if relevant_docs:
            print(f"🔍 知识库检索结果 (查询: '{query}'):")
            for i, doc in enumerate(relevant_docs, 1):
                score = doc.get('score', 0)
                category = doc.get('category', '未知')
                content = doc.get('content', '')[:80]
                print(f"  {i}. [相关度:{score:.3f}] [分类:{category}] {content}...")
            print(f"📊 知识库统计: 共检索到 {len(relevant_docs)} 条相关知识")
        else:
            print(f"⚠️ 知识库检索: 未找到与 '{query}' 相关的知识")
