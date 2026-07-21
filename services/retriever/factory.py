# services/retriever/factory.py
"""检索器工厂：按配置返回可插拔的检索策略实例。"""

import os
from typing import Optional

from .base import BaseRetriever


def create_retriever(strategy: Optional[str] = None) -> BaseRetriever:
    """按策略创建检索器，默认 hybrid。

    通过环境变量 RETRIEVER_STRATEGY 切换：dense | hybrid
    """
    strategy = (
        strategy or os.getenv("RETRIEVER_STRATEGY", "hybrid") or "hybrid"
    ).strip().lower()

    if strategy == "dense":
        from .dense_retriever import DenseRetriever
        return DenseRetriever()
    if strategy == "hybrid":
        from .hybrid_retriever import HybridRetriever
        return HybridRetriever()

    raise ValueError(
        f"不支持的 RETRIEVER_STRATEGY={strategy!r}，可选：dense、hybrid。"
    )
