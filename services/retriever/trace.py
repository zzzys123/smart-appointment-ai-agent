# services/retriever/trace.py
"""
检索链路追踪（Retrieval Trace）

针对 RAG "黑盒"问题，记录检索链路每一个中间状态，让"系统为什么选这几篇文档"
以及"rerank 起了什么作用"透明可见，便于定位坏 case。

覆盖阶段：dense 召回 / sparse 召回 / RRF 融合 / rerank 前后排名变化，各阶段含耗时。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List
from uuid import uuid4


@dataclass
class StageTrace:
    """单个检索阶段的追踪信息。"""

    name: str                       # 阶段名：dense_recall / sparse_recall / rrf_fusion / rerank
    doc_ids: List[int]              # 该阶段输出的文档 id 排序
    duration_ms: float              # 该阶段耗时（毫秒）
    extra: Dict[str, Any] = field(default_factory=dict)  # 额外信息（如 rerank 的重排前排名）


@dataclass
class RetrievalTrace:
    """一次完整检索的链路追踪。"""

    query: str
    strategy: str                   # dense / hybrid
    top_k: int
    trace_id: str = field(default_factory=lambda: uuid4().hex)
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    stages: List[StageTrace] = field(default_factory=list)
    final_doc_ids: List[int] = field(default_factory=list)
    candidates: List[Dict[str, Any]] = field(default_factory=list)
    route: Dict[str, Any] = field(default_factory=dict)
    evidence_gate: Dict[str, Any] = field(default_factory=dict)
    outcome: str = "started"
    error: str = ""
    total_ms: float = 0.0

    def add_stage(self, name: str, doc_ids: List[int], duration_ms: float, **extra) -> None:
        self.stages.append(StageTrace(name, list(doc_ids), float(duration_ms), extra))

    def record_candidates(self, documents: List[Dict[str, Any]]) -> None:
        """Keep compact scores and identities; never duplicate document content."""
        self.candidates = []
        for rank, document in enumerate(documents, start=1):
            retrieval = document.get("retrieval") or {}
            self.candidates.append({
                "rank": rank,
                "document_id": document.get("id"),
                "source_id": document.get("source_id"),
                "chunk_index": document.get("chunk_index"),
                "dense_score": retrieval.get("dense_score"),
                "bm25_score": retrieval.get("bm25_score"),
                "rrf_score": retrieval.get("rrf_score"),
                "rerank_score": document.get("rerank_score"),
                "fused_score": document.get("fused_score"),
                "rerank_fallback": bool(document.get("rerank_fallback")),
            })

    def record_evidence_gate(
        self,
        recalled: List[Dict],
        accepted: List[Dict],
        *,
        diagnostics: List[Dict[str, Any]] | None = None,
        thresholds: Dict[str, float] | None = None,
    ) -> None:
        accepted_ids = [document.get("id") for document in accepted]
        accepted_set = set(accepted_ids)
        self.evidence_gate = {
            "recalled_count": len(recalled),
            "accepted_count": len(accepted),
            "accepted_doc_ids": accepted_ids,
            "rejected_doc_ids": [
                document.get("id")
                for document in recalled
                if document.get("id") not in accepted_set
            ],
            "thresholds": dict(thresholds or {}),
            "documents": list(diagnostics or []),
        }
        self.outcome = "answered" if accepted else "no_answer"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "started_at": self.started_at,
            "query": self.query,
            "strategy": self.strategy,
            "top_k": self.top_k,
            "total_ms": round(self.total_ms, 1),
            "outcome": self.outcome,
            "error": self.error or None,
            "route": self.route,
            "evidence_gate": self.evidence_gate,
            "stages": [
                {
                    "name": s.name,
                    "doc_ids": s.doc_ids,
                    "duration_ms": round(s.duration_ms, 1),
                    **s.extra,
                }
                for s in self.stages
            ],
            "final_doc_ids": self.final_doc_ids,
            "candidates": self.candidates,
        }

    def summary(self) -> str:
        """生成可读的链路摘要，替代零散的 print。"""
        lines = [
            f"🔎 检索链路 [{self.strategy}] trace={self.trace_id} query={self.query!r} "
            f"top_k={self.top_k} 总耗时={self.total_ms:.0f}ms"
        ]
        if self.route:
            lines.append(
                "   ├─ route        "
                f"provider={self.route.get('provider')} "
                f"reason={self.route.get('reason')}"
            )
        for s in self.stages:
            line = f"   └─ {s.name:<12} {s.duration_ms:>6.0f}ms  ids={s.doc_ids}"
            if s.extra:
                # 例如 rerank 阶段展示重排前排名
                extra_str = " ".join(f"{k}={v}" for k, v in s.extra.items())
                line += f"  ({extra_str})"
            lines.append(line)
        lines.append(f"   ✅ 最终返回: {self.final_doc_ids}")
        return "\n".join(lines)
