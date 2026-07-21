# services/retriever/__init__.py
"""可插拔检索器模块。"""

from .base import BaseRetriever
from .dense_retriever import DenseRetriever
from .factory import create_retriever
from .hybrid_retriever import RRF_K, HybridRetriever, _tokenize_zh
from .trace import RetrievalTrace, StageTrace

__all__ = [
    "BaseRetriever",
    "DenseRetriever",
    "HybridRetriever",
    "create_retriever",
    "RRF_K",
    "_tokenize_zh",
    "RetrievalTrace",
    "StageTrace",
]
