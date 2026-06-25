from __future__ import annotations

import json
from pathlib import Path

from price_parser.llm.pilot_provider import PilotProviderResult
from price_parser.llm.pilot_runner import (
    build_prompt_payload,
    dry_run_summary,
    evaluate_pilot_acceptance,
    evaluate_results,
    load_jsonl,
    run_pilot,
)


def sample_case(case_id: str, case_type: str = "control") -> dict:
    return {
        "case_id": case_id,
        "case_type": case_type,
        "source_id": f"file.xls / Sheet1 / строка {case_id}",
        "supplier": "Test",
        "description": "Круг д010 Х12М",
        "current_profile": "ПРУТОК",
        "current_grade": "Х12М",
        "current_dimensions": ["10", None, None],
        "domain_policy": {
            "domain": "METAL_PRODUCT",
            "preferred_sources": ["ГОСТ/ТУ"],
            "forbidden_inferences": ["не выдумывать"],
        },
        "requires_review": case_type == "candidate",
        "review_reasons": ["manual_review_required"] if case_type == "candidate" else [],
    }


class FakePilotProvider:
    def __init__(self) -> None:
        self.calls = 0

    def parse(self, payload):
        self.calls += 1
        rows = []
        for row in payload["rows"]:
            rows.append(
                {
                    "source_id": row["source_id"],
                    "decision": "KEEP",
                    "is_product": True,
                    "profile": row["current_profile"],
                    "grade": row["current_grade"],
                    "dim1": row["current_dimensions"][0],
                    "dim2": row["current_dimensions"][1],
                    "dim3": row["current_dimensions"][2],
                    "additional_info": [],
                    "warnings": [],
                    "confidence": 0.95,
                    "evidence_basis": "SOURCE_TEXT",
                    "research_required": False,
                    "research_queries": [],
                }
            )
        return PilotProviderResult(
            rows=rows,
            input_tokens=100,
            output_tokens=25,
            model="gpt-5.4-mini",
            response_id=f"resp-{self.calls}",
        )


def test_prompt_does_not_leak_case_type_or_gold() -> None:
    case = sample_case("001")
    case["expected_grade"] = "SECRET"
    payload = build_prompt_payload([case])
    row = payload["rows"][0]
    assert "case_type" not in row
    assert "case_id" not in row
    assert "expected_grade" not in row
    assert "SECRET" not in json.dumps(payload, ensure_ascii=False)


def test_dry_run_has_cost_guardrails() -> None:
    cases = [sample_case("001"), sample_case("002", "candidate")]
    summary = dry_run_summary(cases, batch_size=1, max_cases=2)
    assert summary["planned_api_calls"] == 2
    assert summary["gold_not_sent_to_model"] is True
    assert summary["automatic_application"] is False


def test_runner_evaluates_controls_and_does_not_apply(tmp_path: Path) -> None:
    cases = [sample_case("001"), sample_case("002", "candidate")]
    gold = [
        {
            "case_id": "001",
            "source_id": cases[0]["source_id"],
            "profile": "ПРУТОК",
            "grade": "Х12М",
            "dim1": "10.0",
            "dim2": None,
            "dim3": None,
        }
    ]
    provider = FakePilotProvider()
    metrics = run_pilot(
        cases=cases,
        provider=provider,
        output_dir=tmp_path,
        gold=gold,
        batch_size=1,
        max_cases=2,
    )
    assert provider.calls == 2
    assert metrics["evaluation"]["fully_correct_rate"] == 1.0
    assert metrics["evaluation"]["automatic_application_performed"] is False
    assert metrics["estimated_cost"]["usd"] > 0
    assert metrics["acceptance"]["status"] == "PASSED"
    assert (tmp_path / "pilot_results.jsonl").exists()
    assert (tmp_path / "pilot_metrics.json").exists()


def test_evaluate_counts_new_candidate_values() -> None:
    results = [
        {
            "case_id": "CAND-001",
            "case_type": "candidate",
            "current_profile": "ТРУБА",
            "current_grade": None,
            "current_dimensions": [None, None, None],
            "decision": "PROPOSE_CHANGE",
            "profile": "ТРУБА",
            "grade": "12Х18Н10Т",
            "dim1": "10",
            "dim2": None,
            "dim3": None,
        }
    ]
    evaluation = evaluate_results(results, [])
    assert evaluation["candidate_new_values_requiring_human_check"] == 2


def test_load_jsonl_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("{bad}\n", encoding="utf-8")
    try:
        load_jsonl(path)
    except ValueError as exc:
        assert "некорректный JSONL" in str(exc)
    else:
        raise AssertionError("Expected ValueError")


def test_grade_evaluation_uses_project_normalization() -> None:
    from price_parser.llm.pilot_runner import _field_equal

    assert _field_equal("grade", "\u041075", "A75")
    assert _field_equal("grade", "C10200", "\u042110200")



def test_pilot_acceptance_fails_without_live_metrics() -> None:
    acceptance = evaluate_pilot_acceptance(
        evaluation={
            "controls_expected": 1,
            "controls_evaluated": 1,
            "fully_correct_rate": 1.0,
            "field_accuracy": {
                "profile": 1.0,
                "grade": 1.0,
                "dim1": 1.0,
                "dim2": 1.0,
                "dim3": 1.0,
            },
            "automatic_application_performed": False,
        },
        input_tokens=0,
        output_tokens=0,
        latency_seconds=0,
        estimated_cost={"usd": None},
    )
    assert acceptance["status"] == "FAILED"
    assert acceptance["criteria"]["tokens_recorded"] is False
    assert acceptance["live_verification_required"] is True
