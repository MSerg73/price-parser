from __future__ import annotations

import json
from pathlib import Path

import pytest

from price_parser.operator_review import (
    ReviewError,
    apply_confirmed_decisions,
    list_review_items,
    parse_change_assignments,
    record_decision,
    review_item_fingerprint,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _item(offer_id: str = "offer-1") -> dict:
    return {
        "id": offer_id,
        "nomenclature_key": "ЛИСТ|12X18H10T|16|1500|",
        "supplier": "SupplierAlpha",
        "source_file": "supplier_alpha.xls",
        "source_sheet": "Sheet1",
        "source_row": 615,
        "source_block": "левый блок",
        "source_reference": "supplier_alpha.xls / Sheet1 / строка 615 / левый блок",
        "original_text": "Лист 16* диам.1500мм (диск) 12х18н10т",
        "profile": "ЛИСТ",
        "grade": "12Х18Н10Т",
        "grade_key": "12X18H10T",
        "dim1": "16",
        "dim2": "1500",
        "dim3": None,
        "comment": "Требуется решение",
        "parse_status": "REVIEW",
        "requires_review": True,
        "review_reasons": ["business_rule_pending"],
        "warnings": ["Требуется решение заказчика: ЛИСТ или ДИСК"],
        "automatic_application_performed": False,
        "attributes": {},
    }


def test_fingerprint_is_stable_and_source_sensitive() -> None:
    item = _item()
    assert review_item_fingerprint(item) == review_item_fingerprint(dict(item))
    changed = dict(item)
    changed["original_text"] += " изменено"
    assert review_item_fingerprint(item) != review_item_fingerprint(changed)


def test_record_accept_is_idempotent(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    _write_jsonl(queue, [_item()])

    first, created_first = record_decision(
        queue_path=queue,
        decisions_path=decisions,
        offer_id="offer-1",
        action="ACCEPT_AS_IS",
        operator="Operator",
        comment="Оставить профиль ЛИСТ",
    )
    second, created_second = record_decision(
        queue_path=queue,
        decisions_path=decisions,
        offer_id="offer-1",
        action="ACCEPT_AS_IS",
        operator="Operator",
        comment="Оставить профиль ЛИСТ",
    )

    assert created_first is True
    assert created_second is False
    assert first["decision_id"] == second["decision_id"]
    assert len(decisions.read_text(encoding="utf-8").splitlines()) == 1


def test_update_requires_versioned_rule(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    _write_jsonl(queue, [_item()])

    with pytest.raises(ReviewError, match="rule-id"):
        record_decision(
            queue_path=queue,
            decisions_path=decisions,
            offer_id="offer-1",
            action="UPDATE_FIELDS",
            operator="Operator",
            comment="Классифицировать как диск",
            changes={"profile": "ДИСК"},
        )


def test_apply_update_clears_review_and_is_repeatable(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    items = tmp_path / "items.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    output = tmp_path / "output.jsonl"
    remaining = tmp_path / "remaining.jsonl"
    audit = tmp_path / "audit.jsonl"
    source = _item()
    _write_jsonl(queue, [source])
    _write_jsonl(items, [source])

    decision, _ = record_decision(
        queue_path=queue,
        decisions_path=decisions,
        offer_id="offer-1",
        action="UPDATE_FIELDS",
        operator="Operator",
        comment="Подтверждено: круглая заготовка классифицируется как ДИСК",
        changes={"profile": "ДИСК"},
        rule_id="PROFILE-DISK",
        rule_version="1.0",
    )

    first = apply_confirmed_decisions(
        items_path=items,
        decisions_path=decisions,
        output_path=output,
        remaining_queue_path=remaining,
        audit_path=audit,
        applied_by="Operator",
    )
    second = apply_confirmed_decisions(
        items_path=items,
        decisions_path=decisions,
        output_path=output,
        remaining_queue_path=remaining,
        audit_path=audit,
        applied_by="Operator",
    )

    result = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    assert first["applied"] == 1
    assert second["applied"] == 1
    assert result["profile"] == "ДИСК"
    assert result["display_name"] == "Диск 12Х18Н10Т"
    assert result["nomenclature_key"].startswith("ДИСК|12X18H10T|16|1500|")
    assert result["requires_review"] is False
    assert result["parse_status"] == "ACCEPTED"
    assert result["review_reasons"] == []
    assert result["warnings"] == []
    assert result["automatic_application_performed"] is False
    assert result["operator_decision_applied"] is True
    assert result["attributes"]["operator_review"]["decision_id"] == decision["decision_id"]
    assert remaining.read_text(encoding="utf-8") == ""
    assert len(audit.read_text(encoding="utf-8").splitlines()) == 1


def test_source_mismatch_is_not_applied(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    items = tmp_path / "items.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    output = tmp_path / "output.jsonl"
    remaining = tmp_path / "remaining.jsonl"
    audit = tmp_path / "audit.jsonl"
    source = _item()
    _write_jsonl(queue, [source])

    record_decision(
        queue_path=queue,
        decisions_path=decisions,
        offer_id="offer-1",
        action="ACCEPT_AS_IS",
        operator="Operator",
        comment="Подтверждено",
    )
    changed = dict(source)
    changed["original_text"] += " другое"
    _write_jsonl(items, [changed])

    summary = apply_confirmed_decisions(
        items_path=items,
        decisions_path=decisions,
        output_path=output,
        remaining_queue_path=remaining,
        audit_path=audit,
        applied_by="Operator",
    )
    result = json.loads(output.read_text(encoding="utf-8").splitlines()[0])
    audit_row = json.loads(audit.read_text(encoding="utf-8").splitlines()[0])

    assert summary["applied"] == 0
    assert summary["source_mismatch"] == 1
    assert result["requires_review"] is True
    assert audit_row["status"] == "SKIPPED_SOURCE_MISMATCH"


def test_list_hides_resolved_and_keeps_deferred(tmp_path: Path) -> None:
    queue = tmp_path / "queue.jsonl"
    decisions = tmp_path / "decisions.jsonl"
    first = _item("offer-1")
    second = _item("offer-2")
    _write_jsonl(queue, [first, second])

    record_decision(
        queue_path=queue,
        decisions_path=decisions,
        offer_id="offer-1",
        action="ACCEPT_AS_IS",
        operator="Operator",
        comment="Подтверждено",
    )
    record_decision(
        queue_path=queue,
        decisions_path=decisions,
        offer_id="offer-2",
        action="DEFER",
        operator="Operator",
        comment="Нужно решение заказчика",
    )

    payload = list_review_items(queue, decisions)
    assert payload["resolved"] == 1
    assert payload["deferred"] == 1
    assert payload["unresolved"] == 1
    assert [row["id"] for row in payload["items"]] == ["offer-2"]
    assert payload["items"][0]["review_state"] == "DEFERRED"


def test_parse_change_assignments() -> None:
    assert parse_change_assignments(["profile=ДИСК", "dim3=null"]) == {
        "profile": "ДИСК",
        "dim3": None,
    }
    with pytest.raises(ReviewError):
        parse_change_assignments(["broken"])
