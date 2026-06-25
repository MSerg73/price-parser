from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from ..domain_routing import policy_payload
from ..models import ParseStats, ParsedItem
from ..normalization import normalize_grade, normalize_space
from .base import LLMProvider


def candidate_reasons(item: ParsedItem) -> list[str]:
    reasons = list(dict.fromkeys(item.review_reasons))
    if item.grade == "предпол." and "unconfirmed_grade" not in reasons:
        reasons.append("unconfirmed_grade")
    if (
        any(warning.startswith("Конфликт марки:") for warning in item.warnings)
        and "grade_conflict" not in reasons
    ):
        reasons.append("grade_conflict")
    if item.requires_review and not reasons:
        reasons.append("manual_review_required")
    if item.confidence < 0.9 and not reasons:
        reasons.append("low_validation_score")
    return reasons


def collect_candidates(items: list[ParsedItem]) -> list[ParsedItem]:
    return [item for item in items if candidate_reasons(item)]


def enrich_items(
    items: list[ParsedItem],
    provider: LLMProvider,
    stats: ParseStats,
    batch_size: int = 1,
    *,
    apply_changes: bool = False,
) -> list[dict[str, Any]]:
    """Collect LLM proposals; never mutate parsed items unless explicitly requested.

    CLI paths use apply_changes=False. The explicit flag exists only for controlled
    internal migration/testing and is not exposed as a command-line option.
    """
    if batch_size < 1 or batch_size > 10:
        raise ValueError("batch_size должен быть от 1 до 10")

    candidates = collect_candidates(items)
    by_source = {item.source.display(): item for item in candidates}
    proposals: list[dict[str, Any]] = []

    for start in range(0, len(candidates), batch_size):
        batch = candidates[start : start + batch_size]
        payload = {
            "rows": [
                {
                    "source_id": item.source.display(),
                    "supplier": item.supplier,
                    "description": item.raw_description,
                    "current_profile": item.profile,
                    "current_grade": None if item.grade == "предпол." else item.grade,
                    "current_dimensions": [
                        _decimal_text(item.dim1),
                        _decimal_text(item.dim2),
                        _decimal_text(item.dim3),
                    ],
                    "domain_policy": policy_payload(item.domain),
                    "requires_review": item.requires_review,
                    "review_reasons": candidate_reasons(item),
                }
                for item in batch
            ]
        }
        result = provider.parse(payload)
        stats.llm_calls += 1
        stats.llm_input_tokens += result.input_tokens
        stats.llm_output_tokens += result.output_tokens

        rows = result.data.get("rows", [])
        expected_ids = [item.source.display() for item in batch]
        returned_ids = [normalize_space(row.get("source_id")) for row in rows]
        if len(returned_ids) != len(set(returned_ids)):
            raise RuntimeError("LLM вернула дубли source_id")
        if set(returned_ids) != set(expected_ids):
            missing = sorted(set(expected_ids) - set(returned_ids))
            unexpected = sorted(set(returned_ids) - set(expected_ids))
            raise RuntimeError(
                f"Нарушено соответствие source_id: missing={missing}, "
                f"unexpected={unexpected}"
            )

        for row in rows:
            source_id = normalize_space(row.get("source_id"))
            item = by_source[source_id]
            proposal = {
                "source_id": source_id,
                "supplier": item.supplier,
                "description": item.raw_description,
                "current": {
                    "profile": item.profile,
                    "grade": item.grade,
                    "dim1": _decimal_text(item.dim1),
                    "dim2": _decimal_text(item.dim2),
                    "dim3": _decimal_text(item.dim3),
                },
                "proposed": {
                    "profile": normalize_space(row.get("profile")) or None,
                    "grade": normalize_space(row.get("grade")) or None,
                    "dim1": row.get("dim1"),
                    "dim2": row.get("dim2"),
                    "dim3": row.get("dim3"),
                },
                "confidence": row.get("confidence"),
                "warnings": list(row.get("warnings", [])),
                "additional_info": list(row.get("additional_info", [])),
                "model": result.model,
                "automatic_application_performed": False,
            }
            proposals.append(proposal)
            if apply_changes:
                _apply_row(item, row)

    return proposals


def _apply_row(item: ParsedItem, row: dict[str, Any]) -> None:
    if not bool(row.get("is_product", True)):
        item.warnings.append("LLM пометил строку как не товарную")
        item.comment = _append_comment(item.comment, "Требует проверки: возможно, служебная строка")
        return

    profile = normalize_space(row.get("profile"))
    if profile:
        item.profile = profile.upper()

    grade = normalize_space(row.get("grade"))
    if grade:
        item.grade = normalize_grade(grade)[0]

    item.dim1 = _decimal_or_existing(row.get("dim1"), item.dim1)
    item.dim2 = _decimal_or_existing(row.get("dim2"), item.dim2)
    item.dim3 = _decimal_or_existing(row.get("dim3"), item.dim3)

    for info in row.get("additional_info", []):
        item.comment = _append_comment(item.comment, str(info))
    for warning in row.get("warnings", []):
        item.warnings.append(str(warning))
        item.comment = _append_comment(item.comment, f"LLM-предупреждение: {warning}")

    model_confidence = row.get("confidence")
    if isinstance(model_confidence, (int, float)):
        # Self-reported confidence is a signal, not the source of truth.
        item.confidence = min(item.confidence, float(model_confidence))


def _decimal_or_existing(value: Any, existing: Decimal | None) -> Decimal | None:
    if value in (None, ""):
        return existing
    try:
        return Decimal(str(value).replace(",", "."))
    except InvalidOperation:
        return existing


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _append_comment(existing: str, addition: str) -> str:
    addition = normalize_space(addition)
    if not addition:
        return existing
    if not existing:
        return addition
    if addition in existing:
        return existing
    return f"{existing}; {addition}"
