from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from ..models import ParsedItem
from ..normalization import (
    KNOWN_PROFILES,
    canonical_profile,
    normalize_grade,
    normalize_space,
)


_FIELD_REASON_MAP = {
    "profile": {"profile_unparsed"},
    "grade": {"unconfirmed_grade", "grade_conflict", "unverified_designation"},
    "dim1": {"dimension_unparsed", "multiple_dimension_sets"},
    "dim2": {"multiple_dimension_sets"},
    "dim3": {"multiple_dimension_sets"},
}


def apply_verified_llm_results(
    items: list[ParsedItem],
    rows: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Apply only source-confirmed PROPOSE_CHANGE rows.

    ``safe_pipeline`` has already validated schema, row identity, confidence,
    evidence basis and exact source evidence. This function remains
    conservative: REVIEW/KEEP rows are never mutated, unknown fields are
    ignored and every before/proposal/after state is returned for audit.
    """

    by_source = {item.source.display(): item for item in items}
    audit_rows: list[dict[str, Any]] = []
    applied_rows = 0
    applied_fields = 0
    review_rows = 0
    keep_rows = 0
    missing_rows = 0

    for row in rows:
        source_id = normalize_space(row.get("source_id"))
        item = by_source.get(source_id)
        if item is None:
            missing_rows += 1
            audit_rows.append(
                {
                    "source_id": source_id,
                    "decision": row.get("decision"),
                    "status": "SOURCE_ITEM_NOT_FOUND",
                }
            )
            continue

        before = _item_state(item)
        decision = str(row.get("decision") or "").upper()
        changed_fields = list(
            ((row.get("post_validation") or {}).get("changed_fields") or [])
        )
        confirmed_fields = set(
            ((row.get("evidence_validation") or {}).get("confirmed_fields") or [])
        )

        actually_applied: list[str] = []
        rejected_fields: dict[str, str] = {}

        if decision == "PROPOSE_CHANGE":
            for field in changed_fields:
                if field not in confirmed_fields:
                    rejected_fields[field] = "NOT_SOURCE_CONFIRMED"
                    continue
                if _apply_field(item, field, row.get(field)):
                    actually_applied.append(field)
                else:
                    rejected_fields[field] = "INVALID_OR_UNSUPPORTED_VALUE"

            if actually_applied:
                _remove_resolved_review_reasons(item, actually_applied)
                _append_comment(
                    item,
                    "LLM-уточнение применено после программной проверки источника: "
                    + ", ".join(actually_applied),
                )
                confidence = row.get("confidence")
                if isinstance(confidence, (int, float)):
                    item.confidence = max(
                        item.confidence,
                        min(float(confidence), 0.95),
                    )
                applied_rows += 1
                applied_fields += len(actually_applied)
        elif decision == "REVIEW":
            review_rows += 1
        else:
            keep_rows += 1

        after = _item_state(item)
        audit_rows.append(
            {
                "source_id": source_id,
                "decision": decision,
                "status": (
                    "APPLIED"
                    if actually_applied
                    else "NOT_APPLIED"
                ),
                "before": before,
                "proposal": {
                    "profile": row.get("profile"),
                    "material": row.get("material"),
                    "grade": row.get("grade"),
                    "dim1": row.get("dim1"),
                    "dim2": row.get("dim2"),
                    "dim3": row.get("dim3"),
                    "confidence": row.get("confidence"),
                    "field_evidence": row.get("field_evidence") or [],
                    "warnings": row.get("warnings") or [],
                },
                "applied_fields": actually_applied,
                "rejected_fields": rejected_fields,
                "after": after,
            }
        )

    return {
        "automatic_application_performed": applied_fields > 0,
        "applied_rows": applied_rows,
        "applied_fields": applied_fields,
        "review_rows": review_rows,
        "keep_rows": keep_rows,
        "source_items_not_found": missing_rows,
        "audit": audit_rows,
    }


def write_application_audit(
    path: str | Path,
    summary: dict[str, Any],
) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def write_source_audit(
    path: str | Path,
    items: Iterable[ParsedItem],
) -> Path:
    """Write one JSONL row per final item with the preserved source snapshot."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for item in items:
            record = {
                "source_id": item.source.display(),
                "supplier": item.supplier,
                "raw_description": item.raw_description,
                "source_columns": item.attributes.get("source_columns"),
                "final": _item_state(item),
                "confidence": item.confidence,
                "requires_review": item.requires_review,
                "review_reasons": list(item.review_reasons),
                "warnings": list(item.warnings),
                "comment": item.comment,
            }
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
    return output


def _apply_field(item: ParsedItem, field: str, value: Any) -> bool:
    if field == "profile":
        profile = canonical_profile(normalize_space(value))
        if profile not in KNOWN_PROFILES:
            return False
        item.profile = profile
        return True

    if field == "material":
        material = normalize_space(value).upper()
        if not material:
            return False
        item.attributes["material"] = material
        _append_comment(item, "Материал явно подтверждён источником: " + material)
        return True

    if field == "grade":
        grade = normalize_space(value)
        if not grade:
            return False
        item.grade = normalize_grade(grade)[0]
        return True

    if field in {"dim1", "dim2", "dim3"}:
        decimal_value = _decimal(value)
        if decimal_value is None:
            return False
        setattr(item, field, decimal_value)
        display_field = field + "_display"
        if hasattr(item, display_field):
            setattr(item, display_field, None)
        return True

    return False


def _remove_resolved_review_reasons(
    item: ParsedItem,
    applied_fields: Iterable[str],
) -> None:
    resolved: set[str] = set()
    for field in applied_fields:
        resolved.update(_FIELD_REASON_MAP.get(field, set()))
    item.review_reasons = [
        reason for reason in item.review_reasons if reason not in resolved
    ]
    item.requires_review = bool(item.review_reasons)


def _item_state(item: ParsedItem) -> dict[str, Any]:
    return {
        "profile": item.profile,
        "material": item.attributes.get("material"),
        "grade": item.grade,
        "dim1": _decimal_text(item.dim1),
        "dim2": _decimal_text(item.dim2),
        "dim3": _decimal_text(item.dim3),
        "requires_review": item.requires_review,
        "review_reasons": list(item.review_reasons),
    }


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        return None


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def _append_comment(item: ParsedItem, addition: str) -> None:
    addition = normalize_space(addition)
    if not addition:
        return
    if not item.comment:
        item.comment = addition
    elif addition not in item.comment:
        item.comment = item.comment + "; " + addition
