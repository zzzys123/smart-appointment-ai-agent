"""Evaluate the deterministic RAG no-answer gate against known/unknown queries."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from services.knowledge_service import KnowledgeService
from services.retrieval_relevance import RetrievalRelevancePolicy


DEFAULT_CASES = ROOT / "evaluation" / "document_chunking_cases.json"
UNKNOWN_QUERIES = ["店里有游泳池吗", "店里提供免费 WiFi 吗"]


def _scores(document):
    metadata = document.get("retrieval") or {}
    return {
        "dense": metadata.get("dense_score"),
        "bm25": metadata.get("bm25_score"),
    }


async def main(cases_path: Path) -> int:
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    service = KnowledgeService()
    await service.initialize()
    policy = RetrievalRelevancePolicy()

    rejected_valid = []
    for case in cases:
        documents = await service.search(case["query"], top_k=3)
        kept = policy.filter(documents)
        if not kept:
            rejected_valid.append({
                "id": case["id"],
                "query": case["query"],
                "scores": [_scores(document) for document in documents],
            })

    accepted_unknown = []
    for query in UNKNOWN_QUERIES:
        documents = await service.search(query, top_k=3)
        kept = policy.filter(documents)
        if kept:
            accepted_unknown.append({
                "query": query,
                "scores": [_scores(document) for document in kept],
            })

    print(
        f"Known queries kept: {len(cases) - len(rejected_valid)}/{len(cases)}; "
        f"unknown queries rejected: {len(UNKNOWN_QUERIES) - len(accepted_unknown)}/"
        f"{len(UNKNOWN_QUERIES)}"
    )
    if rejected_valid:
        print("Rejected known queries:")
        print(json.dumps(rejected_valid, ensure_ascii=False, indent=2))
    if accepted_unknown:
        print("Accepted unknown queries:")
        print(json.dumps(accepted_unknown, ensure_ascii=False, indent=2))
    return 1 if rejected_valid or accepted_unknown else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.cases)))
