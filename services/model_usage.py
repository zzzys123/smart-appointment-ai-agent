"""Privacy-safe model usage and configurable API cost accounting."""

from __future__ import annotations

import contextvars
import math
import os
import threading
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.embeddings import Embeddings


_ACTIVE_COLLECTOR: contextvars.ContextVar[Optional["UsageCollector"]] = (
    contextvars.ContextVar("model_usage_collector", default=None)
)
_USAGE_STAGE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "model_usage_stage", default="chat"
)


def _env_float(name: str) -> Optional[float]:
    raw = os.getenv(name)
    if raw in (None, ""):
        return None
    value = float(raw)
    if value < 0:
        raise ValueError(f"{name} 不能小于 0")
    return value


def _env_int(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if raw in (None, ""):
        return None
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} 必须大于 0")
    return value


class UsageCollector:
    """Thread-safe aggregate of token counts, logical calls and failures."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stages: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {
                "logical_calls": 0,
                "failed_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "estimated_input_tokens": 0,
                "input_characters": 0,
                "max_input_tokens_per_call": 0,
            }
        )

    def record_chat(
        self,
        stage: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: Optional[int] = None,
        failed: bool = False,
    ) -> None:
        with self._lock:
            item = self._stages[stage]
            item["logical_calls"] += 1
            item["failed_calls"] += int(failed)
            item["input_tokens"] += max(0, int(input_tokens))
            item["output_tokens"] += max(0, int(output_tokens))
            item["total_tokens"] += max(
                0,
                int(
                    total_tokens
                    if total_tokens is not None
                    else input_tokens + output_tokens
                ),
            )
            item["max_input_tokens_per_call"] = max(
                item["max_input_tokens_per_call"], max(0, int(input_tokens))
            )

    def record_embedding(self, texts: Iterable[str], *, failed: bool = False) -> None:
        values = [str(text) for text in texts]
        characters = sum(len(text) for text in values)
        ratio = float(os.getenv("EMBEDDING_ESTIMATED_CHARS_PER_TOKEN", "2.0"))
        if ratio <= 0:
            raise ValueError("EMBEDDING_ESTIMATED_CHARS_PER_TOKEN 必须大于 0")
        estimated = math.ceil(characters / ratio) if characters else 0
        with self._lock:
            item = self._stages["embedding"]
            item["logical_calls"] += 1
            item["failed_calls"] += int(failed)
            item["input_characters"] += characters
            if not failed:
                item["estimated_input_tokens"] += estimated

    def merge(self, report: Optional[Mapping[str, Any]]) -> None:
        """Merge a previous snapshot so resume includes every paid attempt."""

        if not report:
            return
        with self._lock:
            for name, previous in report.get("stages", {}).items():
                item = self._stages[name]
                for key in item:
                    previous_value = int(previous.get(key, 0) or 0)
                    if key == "max_input_tokens_per_call":
                        item[key] = max(item[key], previous_value)
                    else:
                        item[key] += previous_value

    def snapshot(self, *, case_count: Optional[int] = None) -> Dict[str, Any]:
        with self._lock:
            stages = {name: dict(values) for name, values in self._stages.items()}
        for stage in stages.values():
            stage["token_source"] = (
                "estimated_from_characters"
                if stage["estimated_input_tokens"]
                else "provider_reported"
            )
        totals = {
            key: sum(int(stage[key]) for stage in stages.values())
            for key in (
                "logical_calls",
                "failed_calls",
                "input_tokens",
                "output_tokens",
                "total_tokens",
                "estimated_input_tokens",
                "input_characters",
            )
        }
        totals["max_input_tokens_per_call"] = max(
            (
                int(stage["max_input_tokens_per_call"])
                for stage in stages.values()
            ),
            default=0,
        )
        return {
            "stages": stages,
            "totals": totals,
            "cost": self._calculate_cost(stages, case_count=case_count),
        }

    @staticmethod
    def _calculate_cost(
        stages: Mapping[str, Mapping[str, Any]], *, case_count: Optional[int]
    ) -> Dict[str, Any]:
        chat_input_rate = _env_float("LLM_INPUT_CNY_PER_1M_TOKENS")
        chat_output_rate = _env_float("LLM_OUTPUT_CNY_PER_1M_TOKENS")
        embedding_rate = _env_float("EMBEDDING_CNY_PER_1M_TOKENS")
        pricing_max_input = _env_int("MODEL_PRICING_MAX_INPUT_TOKENS")
        chat_tokens = sum(
            int(values["input_tokens"]) + int(values["output_tokens"])
            for name, values in stages.items()
            if name != "embedding"
        )
        embedding_tokens = int(
            stages.get("embedding", {}).get("estimated_input_tokens", 0)
        )
        chat_success_calls = sum(
            max(0, int(values["logical_calls"]) - int(values["failed_calls"]))
            for name, values in stages.items()
            if name != "embedding"
        )
        if chat_success_calls and chat_tokens == 0:
            return {
                "status": "usage_not_reported",
                "total_cny": None,
                "cny_per_case": None,
                "reason": "The chat provider returned no token usage metadata.",
            }
        max_input_tokens = max(
            (
                int(values.get("max_input_tokens_per_call", 0))
                for name, values in stages.items()
                if name != "embedding"
            ),
            default=0,
        )
        if pricing_max_input and max_input_tokens > pricing_max_input:
            return {
                "status": "pricing_tier_exceeded",
                "total_cny": None,
                "cny_per_case": None,
                "max_input_tokens_per_call": max_input_tokens,
                "configured_tier_max_input_tokens": pricing_max_input,
            }
        missing = []
        if chat_tokens and chat_input_rate is None:
            missing.append("LLM_INPUT_CNY_PER_1M_TOKENS")
        if chat_tokens and chat_output_rate is None:
            missing.append("LLM_OUTPUT_CNY_PER_1M_TOKENS")
        if embedding_tokens and embedding_rate is None:
            missing.append("EMBEDDING_CNY_PER_1M_TOKENS")
        if missing:
            return {
                "status": "pricing_not_configured",
                "total_cny": None,
                "cny_per_case": None,
                "missing_environment_variables": sorted(set(missing)),
            }

        chat_input = sum(
            int(values["input_tokens"])
            for name, values in stages.items()
            if name != "embedding"
        )
        chat_output = sum(
            int(values["output_tokens"])
            for name, values in stages.items()
            if name != "embedding"
        )
        total = (
            chat_input * float(chat_input_rate or 0) / 1_000_000
            + chat_output * float(chat_output_rate or 0) / 1_000_000
            + embedding_tokens * float(embedding_rate or 0) / 1_000_000
        )
        return {
            "status": "calculated",
            "total_cny": round(total, 8),
            "cny_per_case": (
                round(total / case_count, 8) if case_count and case_count > 0 else None
            ),
            "embedding_token_source": "estimated_from_characters",
            "pricing_profile": os.getenv("MODEL_PRICING_PROFILE"),
            "pricing_effective_date": os.getenv("MODEL_PRICING_EFFECTIVE_DATE"),
            "pricing_source_url": os.getenv("MODEL_PRICING_SOURCE_URL"),
            "pricing_assumption": (
                "All input tokens use the standard input rate; cache discounts "
                "and free quota are not deducted."
            ),
            "pricing_cny_per_1m_tokens": {
                "llm_input": chat_input_rate,
                "llm_output": chat_output_rate,
                "embedding": embedding_rate,
            },
        }


def activate_usage_collector(collector: UsageCollector):
    return _ACTIVE_COLLECTOR.set(collector)


def deactivate_usage_collector(token) -> None:
    _ACTIVE_COLLECTOR.reset(token)


@contextmanager
def usage_stage(stage: str) -> Iterator[None]:
    token = _USAGE_STAGE.set(stage)
    try:
        yield
    finally:
        _USAGE_STAGE.reset(token)


def _message_text(messages: Any) -> str:
    parts = []
    for batch in messages or []:
        for message in batch or []:
            content = getattr(message, "content", message)
            parts.append(str(content))
    return "\n".join(parts)


def _infer_stage(messages: Any) -> str:
    explicit = _USAGE_STAGE.get()
    if explicit != "chat":
        return explicit
    text = _message_text(messages)
    if "RAG 端到端质量审查员" in text or "RAG 质量审查员" in text:
        return "judge"
    if "检索重排器" in text:
        return "llm_rerank"
    return explicit


def _token_counts(response: Any) -> tuple[int, int, int]:
    llm_output = getattr(response, "llm_output", None) or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
    if usage:
        input_tokens = int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        output_tokens = int(
            usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
        )
        total_tokens = int(usage.get("total_tokens", input_tokens + output_tokens) or 0)
        return input_tokens, output_tokens, total_tokens

    input_tokens = output_tokens = total_tokens = 0
    for generation_batch in getattr(response, "generations", []) or []:
        for generation in generation_batch or []:
            message = getattr(generation, "message", None)
            metadata = getattr(message, "usage_metadata", None) or {}
            input_tokens += int(metadata.get("input_tokens", 0) or 0)
            output_tokens += int(metadata.get("output_tokens", 0) or 0)
            total_tokens += int(metadata.get("total_tokens", 0) or 0)
    return input_tokens, output_tokens, total_tokens or input_tokens + output_tokens


class UsageCallbackHandler(BaseCallbackHandler):
    """LangChain callback that records usage without persisting prompts."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._run_stages: Dict[str, str] = {}

    def _remember(self, run_id: UUID, stage: str) -> None:
        with self._lock:
            self._run_stages[str(run_id)] = stage

    def _pop_stage(self, run_id: UUID) -> str:
        with self._lock:
            return self._run_stages.pop(str(run_id), _USAGE_STAGE.get())

    def on_chat_model_start(
        self, serialized: Dict[str, Any], messages: Any, *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._remember(run_id, _infer_stage(messages))

    def on_llm_start(
        self, serialized: Dict[str, Any], prompts: Any, *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._remember(run_id, _USAGE_STAGE.get())

    def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        collector = _ACTIVE_COLLECTOR.get()
        stage = self._pop_stage(run_id)
        if collector is None:
            return
        input_tokens, output_tokens, total_tokens = _token_counts(response)
        collector.record_chat(
            stage,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        collector = _ACTIVE_COLLECTOR.get()
        stage = self._pop_stage(run_id)
        if collector is not None:
            collector.record_chat(stage, failed=True)


_USAGE_CALLBACK = UsageCallbackHandler()


def get_usage_callback_handler() -> UsageCallbackHandler:
    return _USAGE_CALLBACK


class MeteredEmbeddings(Embeddings):
    """Embedding proxy that records logical requests and estimated input tokens."""

    def __init__(self, inner: Embeddings):
        self.inner = inner

    def _record(self, texts: Iterable[str], *, failed: bool = False) -> None:
        collector = _ACTIVE_COLLECTOR.get()
        if collector is not None:
            collector.record_embedding(texts, failed=failed)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            result = self.inner.embed_documents(texts)
        except Exception:
            self._record(texts, failed=True)
            raise
        self._record(texts)
        return result

    def embed_query(self, text: str) -> list[float]:
        try:
            result = self.inner.embed_query(text)
        except Exception:
            self._record([text], failed=True)
            raise
        self._record([text])
        return result

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            result = await self.inner.aembed_documents(texts)
        except Exception:
            self._record(texts, failed=True)
            raise
        self._record(texts)
        return result

    async def aembed_query(self, text: str) -> list[float]:
        try:
            result = await self.inner.aembed_query(text)
        except Exception:
            self._record([text], failed=True)
            raise
        self._record([text])
        return result
