from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ..normalization import grade_match_key, normalize_grade

DECISION_RULE_VERSION = "0.7.1rc14"


_FALSE_GRADE_CONFLICT_PATTERNS = (
    re.compile(r"current_grade\s+conflicts?\s+with\s+source\s+text", re.I),
    re.compile(r"\bgrade\s+conflict\b", re.I),
    re.compile(r"^\s*конфликт\s+марки\s*:", re.I),
    re.compile(
        r"Марка.*АВ[.\s_-]*(?:Т1|T1).*не\s+разделять",
        re.I,
    ),
)


def _text_key(value: Any) -> str:
    if value in (None, ""):
        return ""
    return " ".join(str(value).upper().replace("Ё", "Е").split())


def _grade_key(value: Any) -> str:
    if value in (None, ""):
        return ""
    canonical = normalize_grade(str(value))[0]
    return grade_match_key(canonical)


def _dimension_key(value: Any) -> Decimal | str | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", ".")).normalize()
    except InvalidOperation:
        return _text_key(value)


def _current_dimension(row: dict[str, Any], index: int) -> Any:
    values = row.get("current_dimensions")
    if not isinstance(values, (list, tuple)) or index >= len(values):
        return None
    return values[index]


def _canonicalize_output_grade(
    row: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    # Canonicalize model output locally and preserve T1 as metadata.
    raw_grade = row.get("grade")
    if raw_grade in (None, ""):
        return {}

    canonical, comments = normalize_grade(str(raw_grade))
    normalized_fields: dict[str, dict[str, Any]] = {}
    if canonical != raw_grade:
        row["grade"] = canonical
        normalized_fields["grade"] = {
            "before": raw_grade,
            "after": canonical,
        }

    state_comments = [
        comment
        for comment in comments
        if comment.startswith("Состояние поставки:")
    ]
    if state_comments:
        additional_info = [
            str(value)
            for value in (row.get("additional_info") or [])
            if str(value).strip()
        ]
        for comment in state_comments:
            if comment not in additional_info:
                additional_info.append(comment)
        row["additional_info"] = additional_info

    return normalized_fields


def _changed_fields(row: dict[str, Any]) -> list[str]:
    comparisons = {
        "profile": (
            _text_key(row.get("current_profile")),
            _text_key(row.get("profile")),
        ),
        "material": (
            _text_key(row.get("current_material")),
            _text_key(row.get("material")),
        ),
        "grade": (
            _grade_key(row.get("current_grade")),
            _grade_key(row.get("grade")),
        ),
        "dim1": (
            _dimension_key(_current_dimension(row, 0)),
            _dimension_key(row.get("dim1")),
        ),
        "dim2": (
            _dimension_key(_current_dimension(row, 1)),
            _dimension_key(row.get("dim2")),
        ),
        "dim3": (
            _dimension_key(_current_dimension(row, 2)),
            _dimension_key(row.get("dim3")),
        ),
    }
    return [
        field
        for field, (current, proposed) in comparisons.items()
        if current != proposed
    ]


def _is_false_grade_conflict(warning: str, grade_unchanged: bool) -> bool:
    if not grade_unchanged:
        return False
    return any(pattern.search(warning) for pattern in _FALSE_GRADE_CONFLICT_PATTERNS)


def expected_decision(row: dict[str, Any]) -> str:
    changed_fields = _changed_fields(row)
    warnings = [
        str(warning)
        for warning in row.get("warnings", [])
        if str(warning).strip()
    ]
    grade_unchanged = "grade" not in changed_fields
    remaining_warnings = [
        warning
        for warning in warnings
        if not _is_false_grade_conflict(warning, grade_unchanged)
    ]

    original_decision = str(
        row.get("model_decision") or row.get("decision") or ""
    ).upper()
    review_required = (
        bool(row.get("research_required"))
        or bool(remaining_warnings)
        or original_decision == "REVIEW"
    )
    if review_required:
        return "REVIEW"
    if changed_fields:
        return "PROPOSE_CHANGE"
    return "KEEP"


def reconcile_result(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    reconciled = deepcopy(row)
    previous_post = (
        deepcopy(reconciled.get("post_validation"))
        if isinstance(reconciled.get("post_validation"), dict)
        else {}
    )

    model_decision = str(
        reconciled.get("model_decision") or reconciled.get("decision") or ""
    ).upper()

    if isinstance(reconciled.get("model_warnings"), list):
        original_warnings = [
            str(value)
            for value in reconciled["model_warnings"]
            if str(value).strip()
        ]
    else:
        original_warnings = [
            str(value)
            for value in reconciled.get("warnings", [])
            if str(value).strip()
        ]
        for value in previous_post.get("removed_false_warnings", []):
            text = str(value)
            if text.strip() and text not in original_warnings:
                original_warnings.append(text)

    normalized_fields = dict(previous_post.get("normalized_fields") or {})
    normalized_fields.update(_canonicalize_output_grade(reconciled))

    changed_fields = _changed_fields(reconciled)
    grade_unchanged = "grade" not in changed_fields
    remaining_warnings: list[str] = []
    removed_warnings: list[str] = []

    for warning in original_warnings:
        if _is_false_grade_conflict(warning, grade_unchanged):
            removed_warnings.append(warning)
        else:
            remaining_warnings.append(warning)

    reconciled["warnings"] = remaining_warnings
    if original_warnings != remaining_warnings:
        reconciled["model_warnings"] = original_warnings
    else:
        reconciled.pop("model_warnings", None)

    review_required = (
        bool(reconciled.get("research_required"))
        or bool(remaining_warnings)
        or model_decision == "REVIEW"
    )
    if review_required:
        decision = "REVIEW"
    elif changed_fields:
        decision = "PROPOSE_CHANGE"
    else:
        decision = "KEEP"

    decision_changed = decision != model_decision
    if decision_changed:
        reconciled["model_decision"] = model_decision or None
    else:
        reconciled.pop("model_decision", None)

    reconciled["decision"] = decision
    reconciled["post_validation"] = {
        "decision_rule_version": DECISION_RULE_VERSION,
        "changed_fields": changed_fields,
        "normalized_fields": normalized_fields,
        "removed_false_warnings": removed_warnings,
        "decision_changed": decision_changed,
        "automatic_application_performed": False,
    }

    audit = {
        "case_id": reconciled.get("case_id"),
        "original_decision": model_decision or None,
        "final_decision": decision,
        "decision_changed": decision_changed,
        "changed_fields": changed_fields,
        "normalized_fields": normalized_fields,
        "removed_false_warnings": removed_warnings,
    }
    return reconciled, audit


def reconcile_results(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    reconciled_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []

    for row in rows:
        reconciled, audit = reconcile_result(row)
        reconciled_rows.append(reconciled)
        audit_rows.append(audit)

    decision_changes = sum(
        1 for audit in audit_rows if audit["decision_changed"]
    )
    removed_warnings = sum(
        len(audit["removed_false_warnings"])
        for audit in audit_rows
    )
    consistent = sum(
        1
        for row in reconciled_rows
        if str(row.get("decision") or "").upper() == expected_decision(row)
    )

    summary = {
        "rows_total": len(reconciled_rows),
        "decisions_reconciled": decision_changes,
        "false_conflict_warnings_removed": removed_warnings,
        "decision_consistency_rate": (
            round(consistent / len(reconciled_rows), 4)
            if reconciled_rows
            else None
        ),
        "automatic_application_performed": False,
        "audit": audit_rows,
    }
    return reconciled_rows, summary


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"Invalid JSONL in {path}, line {line_number}: {exc}"
                ) from exc
            if not isinstance(value, dict):
                raise ValueError(
                    f"Expected object in {path}, line {line_number}"
                )
            rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def _load_metrics(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, dict) else {}


def _load_gold(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.exists():
        return []
    return load_jsonl(path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcile LLM pilot decisions and warnings locally, "
            "without an API call."
        )
    )
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--gold", type=Path)
    parser.add_argument("--metrics", type=Path)
    parser.add_argument("--audit", type=Path)
    args = parser.parse_args()

    rows = load_jsonl(args.results)
    reconciled, summary = reconcile_results(rows)

    backup = args.results.with_suffix(args.results.suffix + ".before_v0_2_5")
    if not backup.exists():
        backup.write_text(
            args.results.read_text(encoding="utf-8-sig"),
            encoding="utf-8",
            newline="\n",
        )

    write_jsonl(args.results, reconciled)

    metrics = _load_metrics(args.metrics)
    gold = _load_gold(args.gold)
    if gold:
        from .pilot_runner import evaluate_results

        evaluation = evaluate_results(reconciled, gold)
        if "evaluation" in metrics:
            metrics["evaluation"] = evaluation
        else:
            metrics.update(evaluation)

    metrics["post_validation"] = {
        key: value
        for key, value in summary.items()
        if key != "audit"
    }
    if args.metrics is not None:
        args.metrics.write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    audit_path = args.audit or args.results.with_name(
        "pilot_reconciliation_audit.json"
    )
    audit_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "results_file": str(args.results),
                "metrics_file": str(args.metrics) if args.metrics else None,
                "audit_file": str(audit_path),
                **{
                    key: value
                    for key, value in summary.items()
                    if key != "audit"
                },
                "llm_api_calls": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
