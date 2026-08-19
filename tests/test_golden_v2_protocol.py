"""Offline checks for the protected Golden v2 evaluation protocol."""

from copy import deepcopy

import pytest

from evaluation.golden_v2_protocol import (
    ProtocolError,
    case_ids_for_split,
    evaluate_acceptance,
    load_case_catalog,
    load_split_manifest,
    normalize_trace_infrastructure_errors,
    validate_complete_result,
)


def _result(split: str, *, cost: float | None = None):
    ids = case_ids_for_split(split)
    result = {
        "golden_version": "golden-v2",
        "details": [{"id": case_id} for case_id in ids],
        "summary": {
            "infrastructure_error_count": 0,
            "answer_correctness": 0.95,
            "faithfulness": 0.96,
            "answer_relevancy": 0.97,
            "citation_accuracy": 0.96,
            "no_answer_accuracy": 1.0,
            "safety_boundary_pass_rate": 1.0,
            "case_pass_rate": 0.92,
            "latency_ms_mean": 10000.0,
            "latency_ms_p95": 18000.0,
        },
    }
    if cost is not None:
        result["cost"] = {"cny_per_case": cost}
    return result


def test_frozen_split_is_stratified_and_covers_all_cases_once():
    manifest = load_split_manifest()
    dev = case_ids_for_split("dev", manifest)
    holdout = case_ids_for_split("holdout", manifest)

    assert len(dev) == 40
    assert len(holdout) == 20
    assert set(dev).isdisjoint(holdout)
    assert set(dev) | set(holdout) == set(load_case_catalog())
    assert manifest["expected_counts"]["holdout"]["groups"] == {
        "dense_semantic": 3,
        "bm25_exact": 4,
        "rerank_interference": 4,
        "chunking_cross_section": 3,
        "policy_boundary": 2,
    }


def test_acceptance_does_not_require_every_case_to_pass():
    baseline = _result("dev")
    candidate = deepcopy(baseline)
    candidate["summary"]["case_pass_rate"] = 0.85

    report = evaluate_acceptance(baseline, candidate, "dev")

    assert report["passed"] is True
    assert report["requires_60_of_60"] is False


def test_critical_safety_regression_fails_acceptance():
    baseline = _result("dev")
    candidate = deepcopy(baseline)
    candidate["summary"]["safety_boundary_pass_rate"] = 0.99

    report = evaluate_acceptance(baseline, candidate, "dev")

    assert report["passed"] is False
    failed = {gate["name"] for gate in report["gates"] if not gate["passed"]}
    assert "critical.safety_boundary_pass_rate" in failed


def test_formal_holdout_requires_cost_and_enforces_cost_ratio():
    baseline = _result("holdout")
    candidate = _result("holdout")
    assert evaluate_acceptance(baseline, candidate, "holdout")["passed"] is False

    baseline = _result("holdout", cost=0.10)
    candidate = _result("holdout", cost=0.116)
    report = evaluate_acceptance(baseline, candidate, "holdout")

    assert report["passed"] is False
    failed = {gate["name"] for gate in report["gates"] if not gate["passed"]}
    assert failed == {"cost.cny_per_case"}


def test_incomplete_or_pre_v2_result_is_rejected():
    result = _result("dev")
    result["golden_version"] = None
    with pytest.raises(ProtocolError, match="not a Golden v2"):
        validate_complete_result(result, "dev")

    result = _result("dev")
    result["details"].pop()
    with pytest.raises(ProtocolError, match="result cases mismatch"):
        validate_complete_result(result, "dev")


def test_retrieval_trace_error_cannot_pass_as_no_answer():
    result = {
        "details": [{
            "id": "unknown_wifi",
            "kind": "no_answer",
            "passed": True,
            "refusal_correct": True,
            "retrieval_trace": {"error": "Connection error."},
        }],
        "summary": {
            "infrastructure_error_count": 0,
            "case_pass_rate": 1.0,
            "no_answer_accuracy": 1.0,
        },
    }

    count = normalize_trace_infrastructure_errors(result)

    assert count == 1
    assert result["details"][0]["passed"] is False
    assert result["details"][0]["infrastructure_error"].startswith(
        "retrieval_trace_error"
    )
    assert result["summary"]["infrastructure_error_count"] == 1
    assert result["summary"]["case_pass_rate"] == 0.0
    assert result["summary"]["no_answer_accuracy"] == 0.0
