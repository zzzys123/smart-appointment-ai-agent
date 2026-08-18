# services/reranker.py
"""
重排器（Rerank）模块

在粗排召回（Hybrid / Dense）之后做精排，形成"粗排低成本泛召回 → 精排高成本精过滤"
的两段式检索架构。

- BaseReranker：抽象接口，便于后续替换（LLM / Cross-Encoder）
- LLMReranker：复用项目现有的 Qwen chat 模型，对候选文档逐一打分后重排
- CrossEncoderReranker：本地 Cross-Encoder 批量推理，失败时回退到粗排
"""

import asyncio
import logging
import os
from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from config.model_provider import create_chat_model, get_model_provider

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
        extra_body = (
            {"enable_thinking": False}
            if get_model_provider() == "qwen"
            else None
        )
        self.llm = llm or create_chat_model(
            temperature=0,
            extra_body=extra_body,
        )
        self.structured_llm = self.llm.with_structured_output(_RerankResult)

    @staticmethod
    def _document_text(document: Dict) -> str:
        keywords = document.get("keywords") or []
        if isinstance(keywords, str):
            keywords_text = keywords
        else:
            keywords_text = " ".join(str(item) for item in keywords)
        return "\n".join(
            value
            for value in (
                f"来源编号: {document.get('source_id')}" if document.get("source_id") else "",
                f"章节: {document.get('title')}" if document.get("title") else "",
                str(document.get("content") or "").strip(),
                f"关键词: {keywords_text}" if keywords_text else "",
            )
            if value
        )

    async def rerank(self, query: str, documents: List[Dict], top_k: int) -> List[Dict]:
        if not documents:
            return []

        candidates_text = "\n".join(
            f"[{i}] {self._document_text(doc)}"
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
                doc["rerank"] = {
                    "provider": "llm",
                    "score": doc["rerank_score"],
                    "original_rank": i,
                    "fallback": False,
                }
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
            fallback = [dict(document) for document in documents[:top_k]]
            for rank, document in enumerate(fallback, start=1):
                document["rank"] = rank
                document["rerank_fallback"] = True
                document["rerank_error"] = str(e)
            return fallback


class CrossEncoderReranker(BaseReranker):
    """基于 sentence-transformers 的本地 Cross-Encoder 重排器。

    模型延迟加载，避免未开启重排时占用内存。同步的模型推理通过
    ``asyncio.to_thread`` 执行，避免阻塞 FastAPI 事件循环。模型加载或
    推理失败时保留粗排顺序，保证 RAG 主链路可用。
    """

    DEFAULT_MODEL = "BAAI/bge-reranker-base"

    def __init__(
        self,
        model_name: Optional[str] = None,
        *,
        device: Optional[str] = None,
        batch_size: Optional[int] = None,
        max_length: Optional[int] = None,
        local_files_only: Optional[bool] = None,
        model=None,
    ):
        self.model_name = model_name or os.getenv(
            "CROSS_ENCODER_MODEL", self.DEFAULT_MODEL
        )
        configured_device = device or os.getenv("CROSS_ENCODER_DEVICE", "auto")
        self.device = None if configured_device.strip().lower() == "auto" else configured_device
        self.batch_size = batch_size or int(os.getenv("CROSS_ENCODER_BATCH_SIZE", "16"))
        self.max_length = max_length or int(os.getenv("CROSS_ENCODER_MAX_LENGTH", "512"))
        if self.batch_size <= 0:
            raise ValueError("CROSS_ENCODER_BATCH_SIZE 必须大于 0")
        if self.max_length <= 0:
            raise ValueError("CROSS_ENCODER_MAX_LENGTH 必须大于 0")
        self.local_files_only = (
            local_files_only
            if local_files_only is not None
            else _env_bool("CROSS_ENCODER_LOCAL_FILES_ONLY", False)
        )
        self._model = model

    def _get_model(self):
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as exc:
                raise RuntimeError(
                    "Cross-Encoder 需要 sentence-transformers；请先安装 requirements.txt"
                ) from exc

            kwargs = {
                "max_length": self.max_length,
                "local_files_only": self.local_files_only,
                "trust_remote_code": False,
            }
            if self.device:
                kwargs["device"] = self.device
            logger.info("正在加载 Cross-Encoder 模型：%s", self.model_name)
            self._model = CrossEncoder(self.model_name, **kwargs)
        return self._model

    @staticmethod
    def _document_text(document: Dict) -> str:
        keywords = document.get("keywords") or []
        if isinstance(keywords, str):
            keywords_text = keywords
        else:
            keywords_text = " ".join(str(item) for item in keywords)
        return "\n".join(
            value
            for value in (
                str(document.get("title") or "").strip(),
                str(document.get("content") or "").strip(),
                keywords_text.strip(),
            )
            if value
        )

    def _predict(self, query: str, documents: List[Dict]) -> List[float]:
        model = self._get_model()
        pairs = [[query, self._document_text(document)] for document in documents]
        raw_scores = model.predict(
            pairs,
            batch_size=self.batch_size,
            show_progress_bar=False,
        )

        # 单标签 Cross-Encoder 通常返回 shape=(N,) 或 (N, 1)。这里兼容两者，
        # 多标签输出则取最后一个标签分数作为相关性分数。
        import numpy as np

        score_matrix = np.asarray(raw_scores, dtype="float32")
        if score_matrix.size == 0 or score_matrix.shape[0] != len(documents):
            raise ValueError("Cross-Encoder 返回的分数数量与候选文档不一致")
        score_matrix = score_matrix.reshape(len(documents), -1)
        return [float(row[-1]) for row in score_matrix]

    async def rerank(self, query: str, documents: List[Dict], top_k: int) -> List[Dict]:
        if not documents or top_k <= 0:
            return []

        try:
            scores = await asyncio.to_thread(self._predict, query, documents)
            scored = []
            for original_rank, (document, score) in enumerate(
                zip(documents, scores), start=1
            ):
                item = dict(document)
                item["rerank_score"] = score
                item["rerank"] = {
                    "provider": "cross-encoder",
                    "model": self.model_name,
                    "score": score,
                    "original_rank": original_rank,
                }
                scored.append(item)

            # Python 排序稳定；分数相同时保留粗排顺序。
            scored.sort(key=lambda item: item["rerank_score"], reverse=True)
            results = scored[:top_k]
            for rank, document in enumerate(results, start=1):
                document["rank"] = rank
            return results
        except Exception as exc:
            logger.error("Cross-Encoder 重排失败，回退到粗排原始顺序：%s", exc)
            fallback = [dict(document) for document in documents[:top_k]]
            for rank, document in enumerate(fallback, start=1):
                document["rank"] = rank
                document["rerank_fallback"] = True
                document["rerank_error"] = str(exc)
            return fallback


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
