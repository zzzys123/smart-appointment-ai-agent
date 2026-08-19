"""Administration health and privacy-safe observability APIs."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from evaluation.summarize_online_metrics import _parse_time, build_report, read_jsonl
from services.knowledge_service import get_shared_knowledge_service
from services.system_health import build_health_report


router = APIRouter(prefix="/api/system", tags=["系统运维"])


@router.get("/health")
async def system_health():
    """Readiness details without external model calls or API cost."""
    service = await get_shared_knowledge_service()
    report = await build_health_report(service)
    report["checked_at"] = datetime.now(timezone.utc).isoformat()
    return report


@router.get("/metrics")
async def online_metrics(
    since: Optional[str] = Query(default=None, description="ISO-8601 lower bound"),
):
    since_time = _parse_time(since)
    if since and since_time is None:
        raise HTTPException(status_code=400, detail="since 必须是有效的 ISO-8601 时间")
    usage_path = Path(os.getenv("MODEL_USAGE_PATH", "data/observability/model_usage.jsonl"))
    retrieval_path = Path(os.getenv("RAG_TRACE_PATH", "data/observability/retrieval_traces.jsonl"))
    usage, usage_invalid = await asyncio.to_thread(read_jsonl, usage_path, since=since_time)
    retrieval, retrieval_invalid = await asyncio.to_thread(read_jsonl, retrieval_path, since=since_time)
    report = build_report(
        usage,
        retrieval,
        usage_invalid_lines=usage_invalid,
        retrieval_invalid_lines=retrieval_invalid,
    )
    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["since"] = since
    return report
