"""Privacy-safe structured events for per-consultation model usage."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


logger = logging.getLogger(__name__)
_WRITE_LOCK = threading.Lock()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class ModelUsageObserver:
    def __init__(self) -> None:
        self.log_enabled = _env_bool("MODEL_USAGE_LOG_ENABLED", True)
        self.persist_enabled = _env_bool("MODEL_USAGE_PERSIST_ENABLED", False)
        self.path = Path(
            os.getenv("MODEL_USAGE_PATH", "data/observability/model_usage.jsonl")
        )

    @staticmethod
    def _session_hash(session_id: Optional[str]) -> Optional[str]:
        if not session_id:
            return None
        return hashlib.sha256(session_id.encode("utf-8")).hexdigest()

    def payload(
        self,
        report: Mapping[str, Any],
        *,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "event": "rag_model_usage",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id,
            "session_sha256": self._session_hash(session_id),
            "usage": dict(report.get("totals", {})),
            "stages": dict(report.get("stages", {})),
            "cost": dict(report.get("cost", {})),
        }

    def emit(
        self,
        report: Mapping[str, Any],
        *,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload = self.payload(report, trace_id=trace_id, session_id=session_id)
        if self.log_enabled:
            logger.info("rag_model_usage %s", json.dumps(payload, ensure_ascii=False))
        if self.persist_enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            with _WRITE_LOCK:
                with self.path.open("a", encoding="utf-8") as output:
                    output.write(line + "\n")
        return payload


_observer = ModelUsageObserver()


def emit_model_usage(
    report: Mapping[str, Any],
    *,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    return _observer.emit(report, trace_id=trace_id, session_id=session_id)
