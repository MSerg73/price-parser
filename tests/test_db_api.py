from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from price_parser import __version__
from price_parser.api import create_app
from price_parser.db.commands import database_status, upgrade_database
from price_parser.db.services import (
    apply_review_decision,
    import_pilot_report,
    persistence_counts,
    record_review_decision,
)
from price_parser.db.session import create_engine_and_session


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _report_dir(tmp_path: Path) -> Path:
    report = tmp_path / "report"
    report.mkdir()
    manifest = [
        {
            "file": "supplier.xlsx",
            "size_bytes": 123,
            "sha256": "a" * 64,
            "sheets": [{"name": "Sheet1", "rows": 2, "columns": 5}],
        }
    ]
    (report / "source_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    rows = [
        {
            "id": "offer-1",
            "nomenclature_key": "ПРУТОК|12X18H10T|10||",
            "supplier": "Supplier",
            "source_file": "supplier.xlsx",
            "source_sheet": "Sheet1",
            "source_row": 1,
            "source_block": None,
            "source_reference": "supplier.xlsx / Sheet1 / строка 1",
            "original_text": "Круг 10 12Х18Н10Т",
            "profile": "ПРУТОК",
            "grade": "12Х18Н10Т",
            "grade_key": "12X18H10T",
            "dim1": "10",
            "dim2": None,
            "dim3": None,
            "dim1_unit": "MM",
            "availability": "5 кг",
            "quantity_value": "5",
            "quantity_unit": "КГ",
            "price_rub_kg": "100.00",
            "display_name": "Пруток 12Х18Н10Т",
            "comment": "",
            "parse_status": "ACCEPTED",
            "confidence": 1.0,
            "requires_review": False,
            "reference_research_required": False,
            "reference_status": None,
            "automatic_application_performed": False,
            "review_reasons": [],
            "warnings": [],
            "operator_hints": [],
            "attributes": {},
        },
        {
            "id": "offer-2",
            "nomenclature_key": "ЛИСТ|12X18H10T|16|1500|",
            "supplier": "Supplier",
            "source_file": "supplier.xlsx",
            "source_sheet": "Sheet1",
            "source_row": 2,
            "source_block": "left",
            "source_reference": "supplier.xlsx / Sheet1 / строка 2 / left",
            "original_text": "Лист 16 диам.1500 (диск) 12Х18Н10Т",
            "profile": "ЛИСТ",
            "grade": "12Х18Н10Т",
            "grade_key": "12X18H10T",
            "dim1": "16",
            "dim2": "1500",
            "dim3": None,
            "dim1_unit": "MM",
            "availability": "10 кг",
            "quantity_value": "10",
            "quantity_unit": "КГ",
            "price_rub_kg": "200.00",
            "display_name": "Лист 12Х18Н10Т",
            "comment": "нужна классификация",
            "parse_status": "REVIEW",
            "confidence": 0.8,
            "requires_review": True,
            "reference_research_required": False,
            "reference_status": None,
            "automatic_application_performed": False,
            "review_reasons": ["business_rule_pending"],
            "warnings": ["ЛИСТ или ДИСК"],
            "operator_hints": [],
            "attributes": {},
        },
    ]
    _write_jsonl(report / "normalized_items.jsonl", rows)
    _write_jsonl(report / "normalization_review_queue.jsonl", [rows[1]])
    _write_jsonl(report / "reference_research_queue.jsonl", [])
    return report


def _database_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'price_parser.db').as_posix()}"


def test_migration_and_idempotent_import(tmp_path: Path) -> None:
    url = _database_url(tmp_path)
    upgrade_database(url)
    status = database_status(url)
    assert status["revision"] == "0003_commercial_documents"
    assert "offers" in status["tables"]

    engine, factory = create_engine_and_session(url)
    try:
        with factory() as session:
            first, created = import_pilot_report(
                session, _report_dir(tmp_path), actor="pytest"
            )
            assert created is True
            assert first.status == "COMPLETED"
            assert first.counts_json["offers_created"] == 2

        with factory() as session:
            second, created = import_pilot_report(
                session, tmp_path / "report", actor="pytest"
            )
            assert created is False
            assert second.id == first.id
            counts = persistence_counts(session)
            assert counts["offers"] == 2
            assert counts["review_items"] == 1
            assert counts["audit_events"] == 1
    finally:
        engine.dispose()


def test_review_decision_and_apply_are_idempotent(tmp_path: Path) -> None:
    url = _database_url(tmp_path)
    upgrade_database(url)
    engine, factory = create_engine_and_session(url)
    try:
        with factory() as session:
            import_pilot_report(session, _report_dir(tmp_path), actor="pytest")
        with factory() as session:
            decision, created = record_review_decision(
                session,
                offer_id="offer-2",
                action="UPDATE_FIELDS",
                operator="operator",
                comment="подтверждён профиль",
                changes={"profile": "ДИСК"},
                rule_id="RULE-DISK-001",
                rule_version="1.0",
            )
            assert created is True
            again, created = record_review_decision(
                session,
                offer_id="offer-2",
                action="UPDATE_FIELDS",
                operator="operator",
                comment="подтверждён профиль",
                changes={"profile": "ДИСК"},
                rule_id="RULE-DISK-001",
                rule_version="1.0",
            )
            assert created is False
            assert again.id == decision.id

        with factory() as session:
            applied, changed = apply_review_decision(
                session, offer_id="offer-2", applied_by="operator"
            )
            assert changed is True
            assert applied.applied_at is not None
            applied_again, changed = apply_review_decision(
                session, offer_id="offer-2", applied_by="operator"
            )
            assert changed is False
            assert applied_again.id == applied.id
    finally:
        engine.dispose()


def test_api_health_import_search_and_review(tmp_path: Path) -> None:
    url = _database_url(tmp_path)
    app = create_app(url, auto_migrate=True, api_token="secret")
    client = TestClient(app)
    headers = {"X-API-Key": "secret"}

    assert client.get("/health").status_code == 401
    health = client.get("/health", headers=headers)
    assert health.status_code == 200
    assert health.json()["version"] == __version__

    imported = client.post(
        "/imports/pilot",
        headers=headers,
        json={"report_dir": str(_report_dir(tmp_path)), "actor": "api-test"},
    )
    assert imported.status_code == 200
    assert imported.json()["created"] is True

    repeated = client.post(
        "/imports/pilot",
        headers=headers,
        json={"report_dir": str(tmp_path / "report"), "actor": "api-test"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["created"] is False

    search = client.get(
        "/search",
        headers=headers,
        params={"profile": "ПРУТОК", "grade": "12Х18Н10Т", "dimensions": "10"},
    )
    assert search.status_code == 200
    assert search.json()["results"][0]["item"]["id"] == "offer-1"
    assert search.json()["automatic_application_performed"] is False

    reviews = client.get("/reviews", headers=headers)
    assert reviews.status_code == 200
    assert reviews.json()["total"] == 1


def test_api_review_decision_apply_and_offer_update(tmp_path: Path) -> None:
    url = _database_url(tmp_path)
    app = create_app(url, auto_migrate=True)
    client = TestClient(app)
    client.post(
        "/imports/pilot",
        json={"report_dir": str(_report_dir(tmp_path)), "actor": "api-test"},
    )

    decision = client.post(
        "/reviews/offer-2/decisions",
        json={
            "action": "UPDATE_FIELDS",
            "operator": "manager",
            "comment": "подтверждён профиль",
            "changes": {"profile": "ДИСК"},
            "rule_id": "RULE-DISK-001",
            "rule_version": "1.0",
        },
    )
    assert decision.status_code == 200
    assert decision.json()["created"] is True

    applied = client.post(
        "/reviews/offer-2/apply",
        json={"applied_by": "manager"},
    )
    assert applied.status_code == 200
    assert applied.json()["changed"] is True

    offer = client.get("/offers/offer-2")
    assert offer.status_code == 200
    assert offer.json()["profile"] == "ДИСК"
    assert offer.json()["requires_review"] is False
    assert offer.json()["automatic_application_performed"] is False


def test_llm_run_registration_is_idempotent_and_never_auto_applies(tmp_path: Path) -> None:
    url = _database_url(tmp_path)
    app = create_app(url, auto_migrate=True)
    client = TestClient(app)
    payload = {
        "fingerprint": "f" * 64,
        "provider": "mock",
        "status": "COMPLETED",
        "input_count": 5,
        "output_count": 5,
        "live_model_verified": False,
    }
    first = client.post("/llm-runs", json=payload)
    second = client.post("/llm-runs", json=payload)
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert first.json()["run"]["automatic_application_performed"] is False
    assert first.json()["run"]["live_model_verified"] is False
