"""Run a protected Golden v2 split without defaulting to all 60 cases."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.eval_end_to_end_quality import evaluate
from evaluation.golden_v2_protocol import (
    case_ids_for_split,
    load_split_manifest,
    normalize_trace_infrastructure_errors,
)


def _default_output(split: str, partial: bool = False) -> Path:
    suffix = "_focused" if partial else ""
    return ROOT / "evaluation" / "results" / f"golden_v2_{split}{suffix}_candidate.json"


def _default_failures(split: str, partial: bool = False) -> Path:
    suffix = "_focused" if partial else ""
    return ROOT / "evaluation" / "results" / f"golden_v2_{split}{suffix}_failures.jsonl"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("dev", "holdout", "all"), default="dev")
    parser.add_argument("--confirm-holdout", action="store_true")
    parser.add_argument("--confirm-full", action="store_true")
    parser.add_argument("--case-id", action="append", dest="case_ids")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--failures-output", type=Path)
    parser.add_argument("--estimated-cost-cny", type=float)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--case-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--rerank-mode", choices=("off", "always", "adaptive"), default="off")
    parser.add_argument("--adaptive-llm", action="store_true", default=None)
    return parser.parse_args()


def _selected_case_ids(split: str, requested: Optional[list[str]]) -> list[str]:
    allowed = case_ids_for_split(split)
    if not requested:
        return allowed
    unknown = sorted(set(requested) - set(allowed))
    if unknown:
        raise SystemExit(
            f"case id 不属于 {split} 集：{', '.join(unknown)}"
        )
    requested_set = set(requested)
    return [case_id for case_id in allowed if case_id in requested_set]


def main() -> int:
    args = _parse_args()
    manifest = load_split_manifest()
    if args.split == "holdout" and not args.confirm_holdout:
        raise SystemExit("Holdout 只在候选配置冻结后运行；请显式传入 --confirm-holdout。")
    if args.split == "all" and not args.confirm_full:
        raise SystemExit("完整 60 条不会默认运行；正式基线或发布候选请显式传入 --confirm-full。")

    case_ids = _selected_case_ids(args.split, args.case_ids)
    complete_split = len(case_ids) == len(case_ids_for_split(args.split))
    output = args.output or _default_output(args.split, partial=not complete_split)
    failures_output = args.failures_output or _default_failures(
        args.split, partial=not complete_split
    )
    if args.resume and output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if normalize_trace_infrastructure_errors(previous):
            output.write_text(
                json.dumps(previous, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    result = asyncio.run(evaluate(
        output=output,
        failures_output=failures_output,
        top_k=args.top_k,
        case_ids=case_ids,
        rerank_mode=args.rerank_mode,
        adaptive_llm=args.adaptive_llm,
        concurrency=args.concurrency,
        case_timeout_seconds=args.case_timeout_seconds,
        resume=args.resume,
    ))
    normalize_trace_infrastructure_errors(result)
    result["protocol"] = {
        "split_version": manifest["split_version"],
        "split": args.split,
        "complete_split": complete_split,
        "model": os.getenv("LLM_MODEL"),
        "embedding_model": os.getenv("EMBEDDING_MODEL"),
    }
    if args.estimated_cost_cny is not None:
        result["cost"] = {
            "total_cny": args.estimated_cost_cny,
            "cny_per_case": args.estimated_cost_cny / len(case_ids),
            "source": "operator_supplied",
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "split": args.split,
        "case_count": len(case_ids),
        "output": str(output),
        "summary": result["summary"],
    }, ensure_ascii=False, indent=2))
    return 2 if result["summary"]["infrastructure_error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
