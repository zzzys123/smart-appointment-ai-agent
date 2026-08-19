"""Side-effect-free readiness checks for the administration dashboard."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict

from sqlalchemy import text

from config.model_provider import get_model_provider
from config.redis_config import redis_config
from services.redis_service import get_redis_service


def _database_ping(knowledge_service) -> None:
    engine = knowledge_service.db.session_manager.engine
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def _configured(value: str | None) -> bool:
    return bool(value and value.strip())


def _model_status() -> Dict[str, Any]:
    provider = get_model_provider()
    if provider == "azure":
        chat_key = _configured(os.getenv("AZURE_OPENAI_API_KEY"))
        chat_model = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    else:
        chat_key = _configured(os.getenv("LLM_API_KEY"))
        chat_model = os.getenv("LLM_MODEL")
    embedding_provider = (os.getenv("EMBEDDING_PROVIDER") or provider).lower()
    if embedding_provider == "azure":
        embedding_key = _configured(os.getenv("AZURE_OPENAI_API_KEY"))
        embedding_model = os.getenv("AZURE_OPENAI_DEPLOYMENT_EMBEDDING")
    else:
        embedding_key = _configured(os.getenv("EMBEDDING_API_KEY") or os.getenv("LLM_API_KEY"))
        embedding_model = os.getenv("EMBEDDING_MODEL")
    ready = chat_key and _configured(chat_model) and embedding_key and _configured(embedding_model)
    return {
        "status": "ok" if ready else "misconfigured",
        "chat": {"provider": provider, "model": chat_model, "key_configured": chat_key},
        "embedding": {
            "provider": embedding_provider,
            "model": embedding_model,
            "key_configured": embedding_key,
        },
        "note": "配置检查不发送外部 API 请求，也不会产生费用。",
    }


def _line_count(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _observability_status() -> Dict[str, Any]:
    usage = Path(os.getenv("MODEL_USAGE_PATH", "data/observability/model_usage.jsonl"))
    retrieval = Path(os.getenv("RAG_TRACE_PATH", "data/observability/retrieval_traces.jsonl"))
    parents = {usage.parent, retrieval.parent}
    writable = all(parent.exists() and os.access(parent, os.W_OK) for parent in parents)
    return {
        "status": "ok" if writable else "unavailable",
        "usage_events": _line_count(usage),
        "retrieval_events": _line_count(retrieval),
        "usage_persistence_enabled": os.getenv("MODEL_USAGE_PERSIST_ENABLED", "false").lower() == "true",
        "retrieval_persistence_enabled": os.getenv("RAG_TRACE_PERSIST_ENABLED", "false").lower() == "true",
    }


async def build_health_report(knowledge_service) -> Dict[str, Any]:
    checks: Dict[str, Any] = {}
    try:
        await asyncio.to_thread(_database_ping, knowledge_service)
        checks["database"] = {"status": "ok", "dialect": knowledge_service.db.session_manager.engine.dialect.name}
    except Exception as exc:
        checks["database"] = {"status": "error", "error": type(exc).__name__}

    try:
        active_count = await asyncio.to_thread(knowledge_service.db.get_documents_count)
        index_count = int(getattr(knowledge_service.retriever, "num_docs", 0))
        ready = bool(knowledge_service.initialized and active_count and index_count == active_count)
        checks["knowledge"] = {
            "status": "ok" if ready else "degraded",
            "active_chunks": active_count,
            "indexed_chunks": index_count,
            "index_generation": knowledge_service._index_generation,
        }
    except Exception as exc:
        checks["knowledge"] = {"status": "error", "error": type(exc).__name__}

    redis_service = get_redis_service()
    checks["redis"] = {
        "status": "ok" if redis_service.available else ("unavailable" if redis_config.enabled else "disabled"),
        "required": False,
        "fallback": None if redis_service.available else "process_local",
    }
    checks["models"] = _model_status()
    checks["observability"] = await asyncio.to_thread(_observability_status)
    required = ("database", "knowledge", "models")
    overall = "ok" if all(checks[name]["status"] == "ok" for name in required) else "degraded"
    return {"status": overall, "checks": checks}
