from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from price_parser.api.app import create_app
from price_parser.db.commands import upgrade_database
from price_parser.db.session import create_engine_and_session
from price_parser.documents import (
    DocumentError,
    calculate_document,
    create_document,
    mock_update_budget,
    render_document_pdf,
)


def payload():
    return {
        "currency": "RUB",
        "vat_rate": "20",
        "prices_include_vat": False,
        "delivery": "1000",
        "discount": "500",
        "items": [
            {
                "name": "Труба 12Х18Н10Т 5x1.5",
                "unit": "кг",
                "quantity": "10",
                "unit_price": "100",
                "markup_rate": "20",
                "source_offer_id": "offer-1",
            }
        ],
    }


def test_calculation_is_deterministic():
    result = calculate_document(payload())
    assert result["subtotal"] == "1200.00"
    assert result["taxable_base"] == "1700.00"
    assert result["vat_total"] == "340.00"
    assert result["grand_total"] == "2040.00"


def test_invalid_discount_rejected():
    data = payload()
    data["discount"] = "999999"
    with pytest.raises(DocumentError):
        calculate_document(data)


def test_document_version_idempotency_pdf_and_budget(tmp_path: Path):
    db = tmp_path / "db.sqlite"
    url = f"sqlite:///{db.as_posix()}"
    upgrade_database(url)
    _, factory = create_engine_and_session(url)
    with factory() as session:
        first, created = create_document(
            session,
            document_key="KP-001",
            document_type="QUOTE",
            payload=payload(),
            created_by="tester",
            deal_external_id="deal-1",
        )
        assert created is True
        same, created = create_document(
            session,
            document_key="KP-001",
            document_type="QUOTE",
            payload=payload(),
            created_by="tester",
            deal_external_id="deal-1",
        )
        assert created is False
        assert same.id == first.id

        data2 = payload()
        data2["delivery"] = "1100"
        second, created = create_document(
            session,
            document_key="KP-001",
            document_type="QUOTE",
            payload=data2,
            created_by="tester",
            deal_external_id="deal-1",
        )
        assert created is True
        assert second.version == 2

        rendered = render_document_pdf(
            session, document_id=first.id, output_dir=tmp_path / "pdf", actor="tester"
        )
        assert rendered.status == "FINALIZED"
        assert Path(rendered.pdf_path).exists()
        assert rendered.pdf_sha256

        update, created = mock_update_budget(
            session,
            document_id=first.id,
            deal_external_id="deal-1",
            actor="tester",
        )
        assert created is True
        assert update.amount == Decimal("2040.00")
        same_update, created = mock_update_budget(
            session,
            document_id=first.id,
            deal_external_id="deal-1",
            actor="tester",
        )
        assert created is False
        assert same_update.id == update.id


def test_api_document_flow(tmp_path: Path):
    db = tmp_path / "api.sqlite"
    out = tmp_path / "pdf"
    url = f"sqlite:///{db.as_posix()}"
    upgrade_database(url)
    client = TestClient(create_app(url))
    response = client.post(
        "/documents",
        json={
            "document_key": "KP-API-1",
            "document_type": "QUOTE",
            "payload": payload(),
            "created_by": "api-test",
            "deal_external_id": "42",
        },
    )
    assert response.status_code == 200, response.text
    document = response.json()["document"]
    response = client.post(
        f'/documents/{document["id"]}/render',
        json={"output_dir": str(out), "actor": "api-test"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "FINALIZED"
    response = client.post(
        f'/documents/{document["id"]}/budget/mock',
        json={"deal_external_id": "42", "actor": "api-test"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["update"]["status"] == "SUCCESS"
