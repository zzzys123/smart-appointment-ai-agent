"""One-chat + one-embedding smoke check for model usage metadata."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from config.model_provider import create_chat_model, create_embedding_model, get_model_provider
from services.model_usage import (
    UsageCollector,
    activate_usage_collector,
    deactivate_usage_collector,
    usage_stage,
)


async def main() -> int:
    collector = UsageCollector()
    token = activate_usage_collector(collector)
    try:
        extra_body = (
            {"enable_thinking": False} if get_model_provider() == "qwen" else None
        )
        chat = create_chat_model(
            temperature=0, max_tokens=32, extra_body=extra_body
        )
        with usage_stage("smoke_chat"):
            await chat.ainvoke("只回复 OK 两个字母，不要补充其他内容。")
        await asyncio.to_thread(
            create_embedding_model().embed_query,
            "Token 成本统计冒烟测试",
        )
    finally:
        deactivate_usage_collector(token)

    report = collector.snapshot(case_count=1)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    chat_usage = report["stages"].get("smoke_chat", {})
    embedding_usage = report["stages"].get("embedding", {})
    return 0 if (
        chat_usage.get("total_tokens", 0) > 0
        and embedding_usage.get("estimated_input_tokens", 0) > 0
    ) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
