from __future__ import annotations

from pathlib import Path

import pytest

from price_parser.llm.pilot_provider import PilotProviderResult
from price_parser.llm.safe_pipeline import (
    FallbackPilotProvider,
    MockPilotProvider,
    SafePipelineError,
    run_safe_pipeline,
    validate_and_reconcile,
)


def payload(review: bool = False) -> dict:
    return {
        "rows": [
            {
                "source_id": "file.xls / Sheet1 / строка 1",
                "supplier": "Test",
                "description": "Круг 10 Х12М",
                "current_profile": "ПРУТОК",
                "current_grade": "Х12М",
                "current_dimensions": ["10", None, None],
                "domain_policy": {
                    "domain": "METAL_PRODUCT",
                    "preferred_sources": ["ГОСТ/ТУ"],
                    "forbidden_inferences": ["не выдумывать"],
                },
                "requires_review": review,
                "review_reasons": ["manual_review_required"] if review else [],
            }
        ]
    }


def valid_result(**overrides) -> PilotProviderResult:
    row = {
        "source_id": "file.xls / Sheet1 / строка 1",
        "decision": "KEEP",
        "is_product": True,
        "profile": "ПРУТОК",
        "grade": "Х12М",
        "dim1": "10",
        "dim2": None,
        "dim3": None,
        "additional_info": [],
        "warnings": [],
        "confidence": 0.95,
        "evidence_basis": "SOURCE_TEXT",
        "research_required": False,
        "research_queries": [],
    }
    row.update(overrides)
    return PilotProviderResult(
        rows=[row],
        input_tokens=10,
        output_tokens=5,
        model="fake",
        response_id="resp-1",
    )


def test_mock_provider_is_offline_and_never_applies(tmp_path: Path) -> None:
    result = run_safe_pipeline(
        payload=payload(review=True),
        provider=MockPilotProvider(),
        output_dir=tmp_path,
    )
    assert result.rows[0]["decision"] == "REVIEW"
    assert result.audit["automatic_application_performed"] is False
    assert result.audit["live_model_verified"] is False
    assert (tmp_path / "llm_safe_results.json").exists()
    assert (tmp_path / "llm_safe_audit.json").exists()


def test_missing_source_id_is_rejected() -> None:
    bad = valid_result()
    bad.rows[0]["source_id"] = "invented"
    with pytest.raises(SafePipelineError, match="Нарушено соответствие строк"):
        validate_and_reconcile(payload(), bad)


def test_low_confidence_forces_review() -> None:
    safe = validate_and_reconcile(payload(), valid_result(confidence=0.4))
    assert safe.rows[0]["decision"] == "REVIEW"
    assert "LOW_CONFIDENCE" in safe.rows[0]["warnings"]


def test_model_knowledge_forces_review() -> None:
    safe = validate_and_reconcile(
        payload(),
        valid_result(evidence_basis="MODEL_KNOWLEDGE"),
    )
    assert safe.rows[0]["decision"] == "REVIEW"
    assert "UNVERIFIED_EVIDENCE" in safe.rows[0]["warnings"]


def test_unexplained_change_forces_review() -> None:
    safe = validate_and_reconcile(
        payload(),
        valid_result(decision="PROPOSE_CHANGE", grade="АISI 304", warnings=[]),
    )
    assert safe.rows[0]["decision"] == "REVIEW"
    assert "UNEXPLAINED_CHANGE" in safe.rows[0]["warnings"]


def test_schema_violation_is_rejected() -> None:
    bad = valid_result()
    del bad.rows[0]["confidence"]
    with pytest.raises(SafePipelineError, match="JSON Schema"):
        validate_and_reconcile(payload(), bad)


def test_fallback_uses_second_provider() -> None:
    class Broken:
        def parse(self, payload):
            raise RuntimeError("offline")

    provider = FallbackPilotProvider(Broken(), MockPilotProvider())
    result = provider.parse(payload())
    assert result.model == "mock"
