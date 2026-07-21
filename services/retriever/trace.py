# services/retriever/trace.py
"""
检索链路追踪（Retrieval Trace）

针对 RAG "黑盒"问题，记录检索链路每一个中间状态，让"系统为什么选这几篇文档"
以及"rerank 起了什么作用"透明可见，便于定位坏 case。

覆盖阶段：dense 召回 / sparse 召回 / RRF 融合 / rerank 前后排名变化，各阶段含耗时。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List


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
    stages: List[StageTrace] = field(default_factory=list)
    final_doc_ids: List[int] = field(default_factory=list)
    total_ms: float = 0.0

    def add_stage(self, name: str, doc_ids: List[int], duration_ms: float, **extra) -> None:
        self.stages.append(StageTrace(name, list(doc_ids), float(duration_ms), extra))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "strategy": self.strategy,
            "top_k": self.top_k,
            "total_ms": round(self.total_ms, 1),
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
        }

    def summary(self) -> str:
        """生成可读的链路摘要，替代零散的 print。"""
        lines = [
            f"🔎 检索链路 [{self.strategy}] query={self.query!r} "
            f"top_k={self.top_k} 总耗时={self.total_ms:.0f}ms"
        ]
        for s in self.stages:
            line = f"   └─ {s.name:<12} {s.duration_ms:>6.0f}ms  ids={s.doc_ids}"
            if s.extra:
                # 例如 rerank 阶段展示重排前排名
                extra_str = " ".join(f"{k}={v}" for k, v in s.extra.items())
                line += f"  ({extra_str})"
            lines.append(line)
        lines.append(f"   ✅ 最终返回: {self.final_doc_ids}")
        return "\n".join(lines)
