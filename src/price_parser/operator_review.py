from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from .nomenclature.normalization import normalize_grade_key

SUPPORTED_ACTIONS = frozenset({"ACCEPT_AS_IS", "UPDATE_FIELDS", "DEFER"})
CONFIRMED_ACTIONS = frozenset({"ACCEPT_AS_IS", "UPDATE_FIELDS"})
ALLOWED_CHANGE_FIELDS = frozenset(
    {
        "profile",
        "grade",
        "dim1",
        "dim2",
        "dim3",
        "display_name",
        "comment",
        "quantity_value",
        "quantity_unit",
        "dim1_display",
        "dim1_unit",
        "dim1_role",
    }
)
_DIMENSION_FIELDS = frozenset({"dim1", "dim2", "dim3", "quantity_value"})


class ReviewError(ValueError):
    """Raised when review input or a decision is invalid."""


def read_jsonl(path: str | Path, *, missing_ok: bool = False) -> list[dict[str, Any]]:
    source = Path(path)
    if missing_ok and not source.exists():
        return []
    if not source.is_file():
        raise ReviewError(f"JSONL-файл не найден: {source}")

    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReviewError(
                    f"Некорректный JSON в {source}, строка {line_number}: {exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise ReviewError(
                    f"Ожидался JSON-объект в {source}, строка {line_number}"
                )
            rows.append(row)
    return rows


def write_jsonl_atomic(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(_canonical_json(row) + "\n")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def review_item_fingerprint(item: dict[str, Any]) -> str:
    payload = {
        "id": item.get("id"),
        "source_file": item.get("source_file"),
        "source_sheet": item.get("source_sheet"),
        "source_row": item.get("source_row"),
        "source_block": item.get("source_block"),
        "original_text": item.get("original_text"),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def list_review_items(
    queue_path: str | Path,
    decisions_path: str | Path | None = None,
    *,
    include_resolved: bool = False,
) -> dict[str, Any]:
    queue = _load_unique_rows(queue_path, key="id", label="очереди REVIEW")
    decisions = (
        _load_decisions(decisions_path)
        if decisions_path is not None
        else []
    )
    latest = _latest_decisions(decisions)

    items: list[dict[str, Any]] = []
    resolved_count = 0
    deferred_count = 0
    for item in queue:
        offer_id = _required_text(item, "id", "строке очереди REVIEW")
        decision = latest.get(offer_id)
        state = "UNRESOLVED"
        if decision:
            action = decision["action"]
            if action in CONFIRMED_ACTIONS:
                state = "RESOLVED"
                resolved_count += 1
            elif action == "DEFER":
                state = "DEFERRED"
                deferred_count += 1

        if include_resolved or state != "RESOLVED":
            rendered = dict(item)
            rendered["review_state"] = state
            rendered["source_fingerprint"] = review_item_fingerprint(item)
            rendered["latest_decision"] = decision
            items.append(rendered)

    return {
        "queue_items": len(queue),
        "resolved": resolved_count,
        "deferred": deferred_count,
        "unresolved": len(queue) - resolved_count,
        "items": items,
    }


def record_decision(
    *,
    queue_path: str | Path,
    decisions_path: str | Path,
    offer_id: str,
    action: str,
    operator: str,
    comment: str,
    changes: dict[str, Any] | None = None,
    rule_id: str | None = None,
    rule_version: str | None = None,
) -> tuple[dict[str, Any], bool]:
    action = action.strip().upper()
    if action not in SUPPORTED_ACTIONS:
        raise ReviewError(
            f"Неизвестное действие {action!r}; допустимо: "
            + ", ".join(sorted(SUPPORTED_ACTIONS))
        )
    operator = operator.strip()
    comment = comment.strip()
    if not operator:
        raise ReviewError("Оператор не указан")
    if not comment:
        raise ReviewError("Комментарий решения обязателен")

    queue = _load_unique_rows(queue_path, key="id", label="очереди REVIEW")
    matching = [row for row in queue if row.get("id") == offer_id]
    if not matching:
        raise ReviewError(f"Позиция {offer_id!r} отсутствует в очереди REVIEW")
    item = matching[0]

    normalized_changes = _validate_changes(action, changes or {})
    rule_id = (rule_id or "").strip() or None
    rule_version = (rule_version or "").strip() or None
    if action == "UPDATE_FIELDS" and (not rule_id or not rule_version):
        raise ReviewError(
            "Для UPDATE_FIELDS обязательны --rule-id и --rule-version"
        )

    decisions_target = Path(decisions_path)
    decisions_target.parent.mkdir(parents=True, exist_ok=True)
    with _exclusive_file_lock(decisions_target):
        decisions = _load_decisions(decisions_target)
        latest = _latest_decisions(decisions).get(offer_id)
        idempotency_payload = {
            "offer_id": offer_id,
            "source_fingerprint": review_item_fingerprint(item),
            "action": action,
            "operator": operator,
            "comment": comment,
            "changes": normalized_changes,
            "rule_id": rule_id,
            "rule_version": rule_version,
        }
        idempotency_key = hashlib.sha256(
            _canonical_json(idempotency_payload).encode("utf-8")
        ).hexdigest()

        for existing in decisions:
            if existing.get("idempotency_key") == idempotency_key:
                return existing, False

        decision_id = f"decision-{idempotency_key[:20]}"
        record = {
            "schema_version": "1.0",
            "decision_id": decision_id,
            "idempotency_key": idempotency_key,
            "offer_id": offer_id,
            "source_fingerprint": idempotency_payload["source_fingerprint"],
            "source_reference": item.get("source_reference"),
            "original_text": item.get("original_text"),
            "decision_status": (
                "CONFIRMED" if action in CONFIRMED_ACTIONS else "DEFERRED"
            ),
            "action": action,
            "changes": normalized_changes,
            "resolved_review_reasons": list(item.get("review_reasons") or []),
            "resolved_warnings": list(item.get("warnings") or []),
            "operator": operator,
            "comment": comment,
            "rule_id": rule_id,
            "rule_version": rule_version,
            "previous_decision_id": (
                latest.get("decision_id") if latest else None
            ),
            "decided_at": _utc_now(),
        }
        with decisions_target.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(_canonical_json(record) + "\n")
        return record, True


def apply_confirmed_decisions(
    *,
    items_path: str | Path,
    decisions_path: str | Path,
    output_path: str | Path,
    remaining_queue_path: str | Path,
    audit_path: str | Path,
    applied_by: str,
) -> dict[str, Any]:
    applied_by = applied_by.strip()
    if not applied_by:
        raise ReviewError("Не указан исполнитель применения решений")

    items = _load_unique_rows(items_path, key="id", label="нормализованных позиций")
    decisions = _load_decisions(decisions_path)
    latest = _latest_decisions(decisions)
    existing_audit = read_jsonl(audit_path, missing_ok=True)
    audit_ids = {
        row.get("application_id")
        for row in existing_audit
        if row.get("application_id")
    }

    output_rows: list[dict[str, Any]] = []
    audit_rows = list(existing_audit)
    applied = 0
    source_mismatch = 0

    for source_item in items:
        item = dict(source_item)
        offer_id = _required_text(item, "id", "нормализованной позиции")
        decision = latest.get(offer_id)
        if decision and decision.get("decision_status") == "CONFIRMED":
            status = "APPLIED"
            current_fingerprint = review_item_fingerprint(item)
            if current_fingerprint != decision.get("source_fingerprint"):
                status = "SKIPPED_SOURCE_MISMATCH"
                source_mismatch += 1
            else:
                item = _apply_decision_to_item(item, decision)
                applied += 1

            application_id = _application_id(decision, current_fingerprint, status)
            if application_id not in audit_ids:
                audit_rows.append(
                    {
                        "schema_version": "1.0",
                        "application_id": application_id,
                        "decision_id": decision["decision_id"],
                        "offer_id": offer_id,
                        "status": status,
                        "source_fingerprint": current_fingerprint,
                        "applied_by": applied_by,
                        "applied_at": _utc_now(),
                    }
                )
                audit_ids.add(application_id)
        output_rows.append(item)

    known_ids = {row["id"] for row in items}
    missing_decisions = 0
    for offer_id, decision in latest.items():
        if (
            decision.get("decision_status") != "CONFIRMED"
            or offer_id in known_ids
        ):
            continue
        missing_decisions += 1
        application_id = _application_id(decision, "MISSING", "SKIPPED_ITEM_NOT_FOUND")
        if application_id not in audit_ids:
            audit_rows.append(
                {
                    "schema_version": "1.0",
                    "application_id": application_id,
                    "decision_id": decision["decision_id"],
                    "offer_id": offer_id,
                    "status": "SKIPPED_ITEM_NOT_FOUND",
                    "source_fingerprint": None,
                    "applied_by": applied_by,
                    "applied_at": _utc_now(),
                }
            )
            audit_ids.add(application_id)

    remaining = [row for row in output_rows if bool(row.get("requires_review"))]
    write_jsonl_atomic(output_path, output_rows)
    write_jsonl_atomic(remaining_queue_path, remaining)
    write_jsonl_atomic(audit_path, audit_rows)

    return {
        "input_items": len(items),
        "decisions": len(decisions),
        "latest_decisions": len(latest),
        "applied": applied,
        "source_mismatch": source_mismatch,
        "missing_items": missing_decisions,
        "remaining_review": len(remaining),
        "output": str(Path(output_path).resolve()),
        "remaining_queue": str(Path(remaining_queue_path).resolve()),
        "audit": str(Path(audit_path).resolve()),
    }


def parse_change_assignments(values: list[str] | None) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for raw in values or []:
        if "=" not in raw:
            raise ReviewError(f"Изменение должно иметь вид поле=значение: {raw!r}")
        field, value = raw.split("=", 1)
        field = field.strip()
        if not field:
            raise ReviewError(f"Пустое имя поля в изменении: {raw!r}")
        if value.strip().lower() in {"null", "none"}:
            parsed: Any = None
        else:
            parsed = value.strip()
        if field in changes and changes[field] != parsed:
            raise ReviewError(f"Поле {field!r} указано несколько раз")
        changes[field] = parsed
    return changes


def _apply_decision_to_item(
    item: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    updated = dict(item)
    changes = decision.get("changes") or {}
    if decision["action"] == "UPDATE_FIELDS":
        updated.update(changes)
        if (
            ("profile" in changes or "grade" in changes)
            and "display_name" not in changes
        ):
            profile = str(updated.get("profile") or "").strip().capitalize()
            grade = str(updated.get("grade") or "").strip()
            updated["display_name"] = " ".join(part for part in (profile, grade) if part)

    resolved_reasons = set(decision.get("resolved_review_reasons") or [])
    resolved_warnings = set(decision.get("resolved_warnings") or [])
    updated["review_reasons"] = [
        reason
        for reason in list(updated.get("review_reasons") or [])
        if reason not in resolved_reasons
    ]
    updated["warnings"] = [
        warning
        for warning in list(updated.get("warnings") or [])
        if warning not in resolved_warnings
    ]
    updated["requires_review"] = bool(updated["review_reasons"])
    updated["parse_status"] = (
        "REVIEW" if updated["requires_review"] else "ACCEPTED"
    )
    updated["automatic_application_performed"] = False
    updated["operator_decision_applied"] = True

    attributes = dict(updated.get("attributes") or {})
    attributes["operator_review"] = {
        "decision_id": decision["decision_id"],
        "action": decision["action"],
        "operator": decision["operator"],
        "decided_at": decision["decided_at"],
        "rule_id": decision.get("rule_id"),
        "rule_version": decision.get("rule_version"),
    }
    updated["attributes"] = attributes
    _refresh_search_keys(updated)
    return updated


def _refresh_search_keys(item: dict[str, Any]) -> None:
    grade = str(item.get("grade") or "").strip()
    item["grade_key"] = normalize_grade_key(grade) if grade else ""
    dimensions = [
        "" if item.get(field) in (None, "") else str(item[field])
        for field in ("dim1", "dim2", "dim3")
    ]
    item["nomenclature_key"] = "|".join(
        [str(item.get("profile") or "").strip(), item["grade_key"], *dimensions]
    )


def _validate_changes(action: str, changes: dict[str, Any]) -> dict[str, Any]:
    if action != "UPDATE_FIELDS":
        if changes:
            raise ReviewError(f"Действие {action} не допускает изменения полей")
        return {}

    if not changes:
        raise ReviewError("Для UPDATE_FIELDS требуется хотя бы одно --set")
    unknown = sorted(set(changes) - ALLOWED_CHANGE_FIELDS)
    if unknown:
        raise ReviewError(
            "Изменение полей запрещено: " + ", ".join(unknown)
        )

    normalized: dict[str, Any] = {}
    for field, value in changes.items():
        if field in _DIMENSION_FIELDS:
            if value is None:
                normalized[field] = None
                continue
            text = str(value).strip().replace(",", ".")
            try:
                number = Decimal(text)
            except InvalidOperation as exc:
                raise ReviewError(f"Поле {field}: некорректное число {value!r}") from exc
            if not number.is_finite() or number <= 0:
                raise ReviewError(f"Поле {field} должно быть конечным числом больше нуля")
            normalized[field] = format(number.normalize(), "f")
        elif value is None:
            normalized[field] = None
        else:
            text = str(value).strip()
            if not text:
                raise ReviewError(f"Поле {field} не может быть пустым")
            normalized[field] = text
    return normalized


def _load_unique_rows(
    path: str | Path,
    *,
    key: str,
    label: str,
) -> list[dict[str, Any]]:
    rows = read_jsonl(path)
    seen: set[str] = set()
    for row in rows:
        value = _required_text(row, key, label)
        if value in seen:
            raise ReviewError(f"Дубликат {key}={value!r} в {label}")
        seen.add(value)
    return rows


def _load_decisions(path: str | Path) -> list[dict[str, Any]]:
    decisions = read_jsonl(path, missing_ok=True)
    seen_ids: set[str] = set()
    for row in decisions:
        decision_id = _required_text(row, "decision_id", "журнале решений")
        offer_id = _required_text(row, "offer_id", "журнале решений")
        action = _required_text(row, "action", "журнале решений")
        if decision_id in seen_ids:
            raise ReviewError(f"Дубликат decision_id={decision_id!r}")
        if action not in SUPPORTED_ACTIONS:
            raise ReviewError(
                f"Решение {decision_id} для {offer_id}: неизвестное действие {action!r}"
            )
        seen_ids.add(decision_id)
    return decisions


def _latest_decisions(
    decisions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for decision in decisions:
        latest[str(decision["offer_id"])] = decision
    return latest


def _required_text(row: dict[str, Any], key: str, label: str) -> str:
    value = str(row.get(key) or "").strip()
    if not value:
        raise ReviewError(f"Поле {key!r} отсутствует в {label}")
    return value


def _application_id(
    decision: dict[str, Any],
    current_fingerprint: str,
    status: str,
) -> str:
    payload = {
        "decision_id": decision["decision_id"],
        "current_fingerprint": current_fingerprint,
        "status": status,
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"application-{digest[:20]}"


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _exclusive_file_lock(target: Path, timeout_seconds: float = 5.0):
    lock = target.with_name(f".{target.name}.lock")
    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(
                lock,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            )
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ReviewError(f"Журнал решений занят другим процессом: {target}")
            time.sleep(0.1)
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        descriptor = None
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        lock.unlink(missing_ok=True)
