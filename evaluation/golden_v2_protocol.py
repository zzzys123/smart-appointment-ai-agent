"""Golden v2 split validation and release acceptance rules.

This module does not modify the frozen Golden v2 cases or evaluator. It adds a
fixed execution protocol around them so development does not repeatedly consume
the full 60-case set.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
POSITIVE_CASES = ROOT / "evaluation" / "document_chunking_cases.json"
NEGATIVE_CASES = ROOT / "evaluation" / "no_answer_cases.json"
SPLIT_MANIFEST = ROOT / "evaluation" / "GOLDEN_V2_SPLIT.json"
ACCEPTANCE_POLICY = ROOT / "evaluation" / "GOLDEN_V2_ACCEPTANCE.json"


class ProtocolError(ValueError):
    """Raised when a Golden result or protocol file is inconsistent."""


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_case_catalog() -> Dict[str, Dict[str, Any]]:
    positive = _load_json(POSITIVE_CASES)
    negative = _load_json(NEGATIVE_CASES)
    catalog = {case["id"]: {**case, "kind": "answer"} for case in positive}
    catalog.update(
        {case["id"]: {**case, "kind": "no_answer"} for case in negative}
    )
    return catalog


def load_split_manifest(path: Path = SPLIT_MANIFEST) -> Dict[str, Any]:
    manifest = _load_json(path)
    validate_split_manifest(manifest)
    return manifest


def _counts(case_ids: Iterable[str], catalog: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    selected = [catalog[case_id] for case_id in case_ids]
    groups: Dict[str, int] = {}
    for case in selected:
        if case["kind"] == "answer":
            group = case["group"]
            groups[group] = groups.get(group, 0) + 1
    answer_count = sum(case["kind"] == "answer" for case in selected)
    return {
        "total": len(selected),
        "answer": answer_count,
        "no_answer": len(selected) - answer_count,
        "groups": groups,
    }


def validate_split_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("golden_version") != "golden-v2":
        raise ProtocolError("split manifest must target golden-v2")
    if manifest.get("status") != "frozen":
        raise ProtocolError("split manifest must be frozen")

    catalog = load_case_catalog()
    dev = list(manifest.get("dev_case_ids", []))
    holdout = list(manifest.get("holdout_case_ids", []))
    if len(dev) != len(set(dev)) or len(holdout) != len(set(holdout)):
        raise ProtocolError("duplicate case id in split manifest")
    overlap = sorted(set(dev) & set(holdout))
    if overlap:
        raise ProtocolError(f"dev/holdout overlap: {', '.join(overlap)}")
    unknown = sorted((set(dev) | set(holdout)) - set(catalog))
    if unknown:
        raise ProtocolError(f"unknown case ids: {', '.join(unknown)}")
    missing = sorted(set(catalog) - (set(dev) | set(holdout)))
    if missing:
        raise ProtocolError(f"unassigned case ids: {', '.join(missing)}")

    expected = manifest["expected_counts"]
    for name, ids in (("dev", dev), ("holdout", holdout)):
        actual_counts = _counts(ids, catalog)
        if actual_counts != expected[name]:
            raise ProtocolError(
                f"{name} count mismatch: expected={expected[name]}, actual={actual_counts}"
            )


def case_ids_for_split(split: str, manifest: Mapping[str, Any] | None = None) -> List[str]:
    manifest = dict(manifest or load_split_manifest())
    if split == "dev":
        return list(manifest["dev_case_ids"])
    if split == "holdout":
        return list(manifest["holdout_case_ids"])
    if split == "all":
        catalog = load_case_catalog()
        return list(catalog)
    raise ProtocolError(f"unsupported split: {split}")


def _result_ids(result: Mapping[str, Any]) -> List[str]:
    return [str(detail.get("id")) for detail in result.get("details", [])]


def normalize_trace_infrastructure_errors(result: Dict[str, Any]) -> int:
    """Turn swallowed retrieval failures into resumable infrastructure errors.

    KnowledgeService deliberately degrades retrieval exceptions to an empty
    result for production availability. That behavior is correct online but an
    evaluation must not count a connection failure as a successful no-answer.
    """

    details = result.get("details", [])
    for detail in details:
        trace_error = detail.get("retrieval_trace", {}).get("error")
        if trace_error and not detail.get("infrastructure_error"):
            detail["infrastructure_error"] = f"retrieval_trace_error: {trace_error}"
            detail["passed"] = False

    infrastructure_count = sum(
        bool(detail.get("infrastructure_error")) for detail in details
    )
    summary = result.get("summary")
    if isinstance(summary, dict):
        summary["infrastructure_error_count"] = infrastructure_count
        summary["case_pass_rate"] = (
            statistics.fmean(float(bool(detail.get("passed"))) for detail in details)
            if details
            else 0.0
        )
        valid_no_answers = [
            detail
            for detail in details
            if detail.get("kind") == "no_answer"
            and not detail.get("infrastructure_error")
        ]
        summary["no_answer_accuracy"] = (
            statistics.fmean(
                float(bool(detail.get("refusal_correct")))
                for detail in valid_no_answers
            )
            if valid_no_answers
            else 0.0
        )
    return infrastructure_count


def validate_complete_result(result: Mapping[str, Any], split: str) -> None:
    if result.get("golden_version") != "golden-v2":
        raise ProtocolError("result is not a Golden v2 result")
    if result.get("status") == "running":
        raise ProtocolError("result is an incomplete checkpoint")
    expected = set(case_ids_for_split(split))
    actual_list = _result_ids(result)
    actual = set(actual_list)
    if len(actual_list) != len(actual):
        raise ProtocolError("result contains duplicate case ids")
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ProtocolError(f"result cases mismatch: missing={missing}, extra={extra}")


def _gate(name: str, passed: bool, actual: Any, expected: Any) -> Dict[str, Any]:
    return {"name": name, "passed": bool(passed), "actual": actual, "expected": expected}


def _number(mapping: Mapping[str, Any], key: str) -> float:
    value = mapping.get(key)
    if not isinstance(value, (int, float)):
        raise ProtocolError(f"missing numeric field: {key}")
    return float(value)


def evaluate_acceptance(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    split: str,
    policy: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Compare one complete candidate with a same-split Golden v2 baseline."""

    validate_complete_result(baseline, split)
    validate_complete_result(candidate, split)
    policy = dict(policy or _load_json(ACCEPTANCE_POLICY))
    baseline_summary = baseline.get("summary", {})
    candidate_summary = candidate.get("summary", {})
    gates: List[Dict[str, Any]] = []

    for metric, expected in policy["critical_gates"].items():
        actual = _number(candidate_summary, metric)
        gates.append(_gate(f"critical.{metric}", actual == float(expected), actual, expected))

    quality = policy["quality_non_inferiority"]
    metrics: Sequence[str] = quality["metrics"]
    baseline_values = [_number(baseline_summary, metric) for metric in metrics]
    candidate_values = [_number(candidate_summary, metric) for metric in metrics]
    baseline_composite = statistics.fmean(baseline_values)
    candidate_composite = statistics.fmean(candidate_values)
    composite_floor = baseline_composite - float(quality["composite_max_absolute_regression"])
    gates.append(_gate(
        "quality.composite_non_inferiority",
        candidate_composite >= composite_floor,
        round(candidate_composite, 6),
        {"baseline": round(baseline_composite, 6), "minimum": round(composite_floor, 6)},
    ))
    max_metric_regression = float(quality["single_metric_max_absolute_regression"])
    for metric, baseline_value, candidate_value in zip(metrics, baseline_values, candidate_values):
        gates.append(_gate(
            f"quality.{metric}_guardrail",
            candidate_value >= baseline_value - max_metric_regression,
            candidate_value,
            {"baseline": baseline_value, "minimum": baseline_value - max_metric_regression},
        ))

    performance = policy["performance_non_inferiority"]
    for metric in ("latency_ms_mean", "latency_ms_p95"):
        baseline_value = _number(baseline_summary, metric)
        candidate_value = _number(candidate_summary, metric)
        max_ratio = float(performance[f"{metric}_max_ratio"])
        gates.append(_gate(
            f"performance.{metric}",
            candidate_value <= baseline_value * max_ratio,
            candidate_value,
            {"baseline": baseline_value, "max_ratio": max_ratio},
        ))

    cost_policy = policy["cost_non_inferiority"]
    baseline_cost = baseline.get("cost", {}).get("cny_per_case")
    candidate_cost = candidate.get("cost", {}).get("cny_per_case")
    cost_required = split in cost_policy["required_for_splits"]
    if baseline_cost is None or candidate_cost is None:
        gates.append(_gate(
            "cost.cny_per_case",
            not cost_required,
            {"baseline": baseline_cost, "candidate": candidate_cost},
            "required for formal holdout/all acceptance" if cost_required else "optional for dev",
        ))
    else:
        max_ratio = float(cost_policy["cost_per_case_max_ratio"])
        gates.append(_gate(
            "cost.cny_per_case",
            float(candidate_cost) <= float(baseline_cost) * max_ratio,
            float(candidate_cost),
            {"baseline": float(baseline_cost), "max_ratio": max_ratio},
        ))

    return {
        "evaluation": "golden_v2_acceptance",
        "golden_version": "golden-v2",
        "split": split,
        "policy_version": policy["policy_version"],
        "passed": all(gate["passed"] for gate in gates),
        "case_pass_rate": candidate_summary.get("case_pass_rate"),
        "requires_60_of_60": False,
        "gates": gates,
    }
