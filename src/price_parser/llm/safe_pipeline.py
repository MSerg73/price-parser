from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from ..normalization import (
    KNOWN_PROFILES,
    canonical_profile,
    extract_material_from_description,
    grade_match_key,
    normalize_grade,
    normalize_space,
)
from .pilot_provider import PilotParsedBatch, PilotProviderResult
from .result_reconciliation import reconcile_results


class SafePipelineError(RuntimeError):
    """Raised when an LLM response violates the deterministic contract."""


class PilotProvider(Protocol):
    def parse(self, payload: dict[str, Any]) -> PilotProviderResult:
        ...


@dataclass(slots=True)
class SafePipelineResult:
    rows: list[dict[str, Any]]
    audit: dict[str, Any]
    input_tokens: int
    output_tokens: int
    model: str
    response_id: str | None


class MockPilotProvider:
    """Deterministic offline provider used for development and CI."""

    def parse(self, payload: dict[str, Any]) -> PilotProviderResult:
        rows: list[dict[str, Any]] = []
        for source in payload.get("rows", []):
            dimensions = list(source.get("current_dimensions") or [None, None, None])
            while len(dimensions) < 3:
                dimensions.append(None)
            rows.append(
                {
                    "source_id": source["source_id"],
                    "decision": "REVIEW" if source.get("requires_review") else "KEEP",
                    "is_product": True,
                    "profile": source.get("current_profile"),
                    "material": source.get("current_material"),
                    "grade": source.get("current_grade"),
                    "dim1": dimensions[0],
                    "dim2": dimensions[1],
                    "dim3": dimensions[2],
                    "additional_info": [],
                    "warnings": (
                        ["Требуется решение оператора"]
                        if source.get("requires_review")
                        else []
                    ),
                    "confidence": 0.5 if source.get("requires_review") else 1.0,
                    "evidence_basis": (
                        "INSUFFICIENT"
                        if source.get("requires_review")
                        else "SOURCE_TEXT"
                    ),
                    "research_required": bool(source.get("requires_review")),
                    "research_queries": [],
                    "field_evidence": [],
                }
            )
        return PilotProviderResult(
            rows=rows,
            input_tokens=0,
            output_tokens=0,
            model="mock",
            response_id="mock",
        )


class FallbackPilotProvider:
    """Try providers in order and return the first successful response."""

    def __init__(self, *providers: PilotProvider) -> None:
        if not providers:
            raise ValueError("Нужен хотя бы один LLM-провайдер")
        self.providers = providers

    def parse(self, payload: dict[str, Any]) -> PilotProviderResult:
        errors: list[str] = []
        for provider in self.providers:
            try:
                return provider.parse(payload)
            except Exception as exc:  # provider boundary
                errors.append(f"{type(exc).__name__}: {exc}")
        raise SafePipelineError("Все LLM-провайдеры завершились ошибкой: " + " | ".join(errors))


def request_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()



def _text_key(value: Any) -> str:
    return normalize_space(value).upper().replace("Ё", "Е")


def _dimension_key(value: Any) -> Decimal | str | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ".")).normalize()
    except (InvalidOperation, ValueError):
        return _text_key(value)


def _changed_fields(row: dict[str, Any], source: dict[str, Any]) -> list[str]:
    current_dimensions = list(source.get("current_dimensions") or [None, None, None])
    while len(current_dimensions) < 3:
        current_dimensions.append(None)
    comparisons = {
        "profile": (
            _text_key(source.get("current_profile")),
            _text_key(row.get("profile")),
        ),
        "material": (
            _text_key(source.get("current_material")),
            _text_key(row.get("material")),
        ),
        "grade": (
            grade_match_key(source.get("current_grade")),
            grade_match_key(row.get("grade")),
        ),
        "dim1": (
            _dimension_key(current_dimensions[0]),
            _dimension_key(row.get("dim1")),
        ),
        "dim2": (
            _dimension_key(current_dimensions[1]),
            _dimension_key(row.get("dim2")),
        ),
        "dim3": (
            _dimension_key(current_dimensions[2]),
            _dimension_key(row.get("dim3")),
        ),
    }
    return [
        field
        for field, (before, after) in comparisons.items()
        if before != after
    ]


def _flatten_source_values(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, dict):
        result: list[str] = []
        for nested in value.values():
            result.extend(_flatten_source_values(nested))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for nested in value:
            result.extend(_flatten_source_values(nested))
        return result
    text = normalize_space(value)
    return [text] if text else []


def _evidence_present(evidence: str, source: dict[str, Any]) -> bool:
    source_values = [
        normalize_space(source.get("description")),
        *_flatten_source_values(source.get("source_columns")),
    ]
    needle = normalize_space(evidence).casefold()
    if not needle:
        return False
    return any(needle in value.casefold() for value in source_values if value)


def _evidence_matches_value(field: str, value: Any, evidence: str) -> bool:
    if value in (None, ""):
        return False

    if field == "profile":
        profile = canonical_profile(str(value))
        return (
            profile in KNOWN_PROFILES
            and canonical_profile(evidence) == profile
        )

    if field == "material":
        material, _token = extract_material_from_description(evidence)
        return material == _text_key(value)

    if field == "grade":
        proposed_key = grade_match_key(value)
        direct_key = grade_match_key(normalize_grade(evidence)[0])
        evidence_key = grade_match_key(evidence)
        return bool(
            proposed_key
            and (
                proposed_key == direct_key
                or proposed_key in evidence_key
            )
        )

    if field in {"dim1", "dim2", "dim3"}:
        proposed = _dimension_key(value)
        if not isinstance(proposed, Decimal):
            return False
        numbers = re.findall(r"\d+(?:[.,]\d+)?", evidence)
        for number in numbers:
            try:
                if Decimal(number.replace(",", ".")).normalize() == proposed:
                    return True
            except InvalidOperation:
                continue
        return False

    return False


def _validate_field_evidence(
    row: dict[str, Any],
    source: dict[str, Any],
) -> tuple[list[str], dict[str, Any]]:
    changed = _changed_fields(row, source)
    evidence_rows = list(row.get("field_evidence") or [])
    by_field: dict[str, list[dict[str, Any]]] = {}
    for evidence_row in evidence_rows:
        field = str(evidence_row.get("field") or "")
        by_field.setdefault(field, []).append(evidence_row)

    confirmed: list[str] = []
    rejected: dict[str, str] = {}
    for field in changed:
        candidates = by_field.get(field, [])
        if not candidates:
            rejected[field] = "FIELD_EVIDENCE_MISSING"
            continue

        accepted = False
        for candidate in candidates:
            evidence = normalize_space(candidate.get("evidence"))
            proposed_value = row.get(field)
            if not _evidence_present(evidence, source):
                continue
            if not _evidence_matches_value(field, proposed_value, evidence):
                continue
            accepted = True
            break

        if accepted:
            confirmed.append(field)
        else:
            rejected[field] = "FIELD_EVIDENCE_NOT_CONFIRMED"

    return changed, {
        "confirmed_fields": confirmed,
        "rejected_fields": rejected,
    }


def validate_and_reconcile(
    payload: dict[str, Any],
    result: PilotProviderResult,
    *,
    minimum_confidence: float = 0.80,
) -> SafePipelineResult:
    input_rows = payload.get("rows")
    if not isinstance(input_rows, list) or not input_rows:
        raise SafePipelineError("LLM payload.rows должен быть непустым списком")

    expected_ids = [str(row.get("source_id", "")) for row in input_rows]
    if any(not value for value in expected_ids):
        raise SafePipelineError("Каждая входная строка должна иметь source_id")
    if len(expected_ids) != len(set(expected_ids)):
        raise SafePipelineError("Во входном payload есть дубли source_id")

    try:
        parsed = PilotParsedBatch.model_validate({"rows": result.rows})
    except ValidationError as exc:
        raise SafePipelineError(f"Ответ LLM не соответствует JSON Schema: {exc}") from exc

    output_rows = [row.model_dump() for row in parsed.rows]
    returned_ids = [row["source_id"] for row in output_rows]
    if len(returned_ids) != len(set(returned_ids)):
        raise SafePipelineError("Ответ LLM содержит дубли source_id")
    if set(returned_ids) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(returned_ids))
        unexpected = sorted(set(returned_ids) - set(expected_ids))
        raise SafePipelineError(
            f"Нарушено соответствие строк. missing={missing}, unexpected={unexpected}"
        )

    input_by_id = {str(row["source_id"]): row for row in input_rows}
    guarded_rows: list[dict[str, Any]] = []
    guardrail_events: list[dict[str, Any]] = []

    for row in output_rows:
        source = input_by_id[row["source_id"]]
        row["current_profile"] = source.get("current_profile")
        row["current_material"] = source.get("current_material")
        row["current_grade"] = source.get("current_grade")
        row["current_dimensions"] = list(
            source.get("current_dimensions") or [None, None, None]
        )

        changed_fields, evidence_validation = _validate_field_evidence(
            row,
            source,
        )
        row["evidence_validation"] = evidence_validation

        reasons: list[str] = []
        if row["confidence"] < minimum_confidence:
            reasons.append("LOW_CONFIDENCE")
        if row["evidence_basis"] in {"MODEL_KNOWLEDGE", "INSUFFICIENT"}:
            reasons.append("UNVERIFIED_EVIDENCE")
        if row["research_required"]:
            reasons.append("RESEARCH_REQUIRED")
        if (
            row["decision"] == "PROPOSE_CHANGE"
            and changed_fields
            and not row.get("field_evidence")
        ):
            reasons.append("UNEXPLAINED_CHANGE")
        for field, code in evidence_validation["rejected_fields"].items():
            reasons.append(f"{code}:{field}")

        if reasons:
            row["decision"] = "REVIEW"
            row["warnings"] = list(dict.fromkeys([*row["warnings"], *reasons]))
            guardrail_events.append(
                {"source_id": row["source_id"], "forced_review_reasons": reasons}
            )
        guarded_rows.append(row)

    reconciled, reconciliation_summary = reconcile_results(guarded_rows)
    audit = {
        "request_fingerprint": request_fingerprint(payload),
        "input_rows": len(input_rows),
        "output_rows": len(reconciled),
        "guardrail_events": guardrail_events,
        "reconciliation": reconciliation_summary,
        "automatic_application_performed": False,
        "live_model_verified": False,
    }
    return SafePipelineResult(
        rows=reconciled,
        audit=audit,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        model=result.model,
        response_id=result.response_id,
    )


def run_safe_pipeline(
    *,
    payload: dict[str, Any],
    provider: PilotProvider,
    output_dir: str | Path,
    minimum_confidence: float = 0.80,
) -> SafePipelineResult:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    raw_result = provider.parse(payload)
    safe = validate_and_reconcile(
        payload,
        raw_result,
        minimum_confidence=minimum_confidence,
    )

    (output / "llm_safe_results.json").write_text(
        json.dumps(safe.rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = {
        **safe.audit,
        "model": safe.model,
        "response_id": safe.response_id,
        "input_tokens": safe.input_tokens,
        "output_tokens": safe.output_tokens,
    }
    (output / "llm_safe_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return safe
