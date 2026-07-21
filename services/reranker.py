# services/reranker.py
"""
重排器（Rerank）模块

在粗排召回（Hybrid / Dense）之后做精排，形成"粗排低成本泛召回 → 精排高成本精过滤"
的两段式检索架构。

- BaseReranker：抽象接口，便于后续替换（LLM / Cross-Encoder）
- LLMReranker：复用项目现有的 Qwen chat 模型，对候选文档逐一打分后重排
- CrossEncoderReranker：本地 Cross-Encoder 占位实现（第三步可插拔完成后再落地）
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from config.model_provider import create_chat_model

logger = logging.getLogger(__name__)


class BaseReranker(ABC):
    """重排器抽象接口。"""

    @abstractmethod
    async def rerank(self, query: str, documents: List[Dict], top_k: int) -> List[Dict]:
        """对候选文档按与 query 的相关性重排，返回精排后的前 top_k 个文档。"""
        raise NotImplementedError


# ---- LLM Rerank 的结构化输出 Schema（复用项目 with_structured_output 的做法）----
class _RankItem(BaseModel):
    index: int = Field(description="候选文档的编号，从 1 开始")
    score: float = Field(description="相关性分数，0-10，越高越相关（10=完全回答问题，0=完全无关）")


class _RerankResult(BaseModel):
    rankings: List[_RankItem] = Field(description="对所有候选文档的相关性打分列表")


_RERANK_SYSTEM_PROMPT = (
    "你是一个精确的检索重排器。给定用户问题和若干候选文档，"
    "请评估每个候选文档与问题的相关性。\n"
    "- 对每个候选文档给出 0-10 的相关性分数（10=完全回答了问题，0=完全无关）。\n"
    "- 只依据文档内容判断，不要臆测或编造。\n"
    "- 必须为每一个候选文档都给出分数。"
)


class LLMReranker(BaseReranker):
    """基于 LLM 的重排器：让大模型对候选文档逐一打分后按分数排序。"""

    def __init__(self, llm=None):
        self.llm = llm or create_chat_model(temperature=0)
        self.structured_llm = self.llm.with_structured_output(_RerankResult)

    async def rerank(self, query: str, documents: List[Dict], top_k: int) -> List[Dict]:
        if not documents:
            return []

        candidates_text = "\n".join(
            f"[{i}] {doc.get('content', '')}"
            for i, doc in enumerate(documents, start=1)
        )
        user_prompt = (
            f"用户问题：{query}\n\n"
            f"候选文档：\n{candidates_text}\n\n"
            f"请为每个候选文档打分。"
        )

        try:
            result: _RerankResult = await self.structured_llm.ainvoke(
                [
                    ("system", _RERANK_SYSTEM_PROMPT),
                    ("human", user_prompt),
                ]
            )
            score_map = {item.index: item.score for item in result.rankings}

            scored = []
            for i, doc in enumerate(documents, start=1):
                doc = dict(doc)  # 拷贝，避免污染召回结果
                # 未被 LLM 打分的候选给一个较低的兜底分，保持在后面
                doc["rerank_score"] = float(score_map.get(i, -1.0))
                scored.append(doc)

            scored.sort(key=lambda d: d["rerank_score"], reverse=True)
            for rank, doc in enumerate(scored, start=1):
                doc["rank"] = rank

            logger.debug(
                "[LLMRerank] query=%r 重排前=%s 重排后=%s",
                query,
                [d.get("id") for d in documents],
                [d.get("id") for d in scored[:top_k]],
            )
            return scored[:top_k]

        except Exception as e:
            logger.error(f"LLM 重排失败，回退到粗排原始顺序：{e}")
            return documents[:top_k]


class CrossEncoderReranker(BaseReranker):
    """本地 Cross-Encoder 重排器（占位）。

    需安装 sentence-transformers 并下载本地重排模型
    （如 cross-encoder/ms-marco-MiniLM-L-6-v2），第三步可插拔完成后再实现。
    优点：不消耗 API 额度、可离线；缺点：需下载模型、首次加载较慢。
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        raise NotImplementedError(
            "CrossEncoderReranker 尚未实现：需安装 sentence-transformers 并下载本地重排模型。"
        )

    async def rerank(self, query: str, documents: List[Dict], top_k: int) -> List[Dict]:
        raise NotImplementedError


def create_reranker(provider: Optional[str] = None) -> BaseReranker:
    """重排器工厂：按配置返回实例，默认 LLM 重排。

    通过环境变量 RERANKER_PROVIDER 切换：llm | cross-encoder
    """
    provider = (provider or os.getenv("RERANKER_PROVIDER", "llm") or "llm").strip().lower()

    if provider == "llm":
        return LLMReranker()
    if provider in ("cross-encoder", "cross_encoder", "crossencoder"):
        return CrossEncoderReranker()

    raise ValueError(
        f"不支持的 RERANKER_PROVIDER={provider!r}，可选：llm、cross-encoder。"
    )
