from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any
from uuid import uuid4

from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from price_parser.db.models import AuditEvent, BudgetUpdate, CommercialDocument

MONEY = Decimal("0.01")


class DocumentError(RuntimeError):
    pass


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(MONEY, rounding=ROUND_HALF_UP)
    except Exception as exc:
        raise DocumentError(f"Invalid monetary value: {value!r}") from exc


def calculate_document(payload: dict[str, Any]) -> dict[str, Any]:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise DocumentError("Document must contain at least one item")

    vat_rate = Decimal(str(payload.get("vat_rate", "20")))
    if vat_rate < 0 or vat_rate > 100:
        raise DocumentError("vat_rate must be between 0 and 100")
    prices_include_vat = bool(payload.get("prices_include_vat", False))
    delivery = _money(payload.get("delivery", 0))
    discount = _money(payload.get("discount", 0))
    if delivery < 0 or discount < 0:
        raise DocumentError("delivery and discount cannot be negative")

    calculated_items: list[dict[str, Any]] = []
    subtotal = Decimal("0.00")
    for index, raw in enumerate(items, start=1):
        name = str(raw.get("name", "")).strip()
        unit = str(raw.get("unit", "")).strip()
        quantity = _money(raw.get("quantity", 0))
        unit_price = _money(raw.get("unit_price", 0))
        markup_rate = Decimal(str(raw.get("markup_rate", 0)))
        if not name or not unit:
            raise DocumentError(f"Item {index}: name and unit are required")
        if quantity <= 0 or unit_price < 0:
            raise DocumentError(f"Item {index}: invalid quantity or unit_price")
        if markup_rate < Decimal("-100"):
            raise DocumentError(f"Item {index}: markup_rate is below -100")

        sale_unit_price = (unit_price * (Decimal("1") + markup_rate / Decimal("100"))).quantize(
            MONEY, rounding=ROUND_HALF_UP
        )
        line_total = (quantity * sale_unit_price).quantize(MONEY, rounding=ROUND_HALF_UP)
        subtotal += line_total
        calculated_items.append({
            "position": index,
            "name": name,
            "unit": unit,
            "quantity": str(quantity),
            "purchase_unit_price": str(unit_price),
            "markup_rate": str(markup_rate),
            "sale_unit_price": str(sale_unit_price),
            "line_total": str(line_total),
            "source_offer_id": raw.get("source_offer_id"),
        })

    subtotal = subtotal.quantize(MONEY, rounding=ROUND_HALF_UP)
    if discount > subtotal:
        raise DocumentError("discount cannot exceed subtotal")
    taxable_base = (subtotal - discount + delivery).quantize(MONEY, rounding=ROUND_HALF_UP)

    if prices_include_vat:
        vat_total = (
            taxable_base * vat_rate / (Decimal("100") + vat_rate)
            if vat_rate else Decimal("0")
        ).quantize(MONEY, rounding=ROUND_HALF_UP)
        grand_total = taxable_base
    else:
        vat_total = (taxable_base * vat_rate / Decimal("100")).quantize(
            MONEY, rounding=ROUND_HALF_UP
        )
        grand_total = (taxable_base + vat_total).quantize(MONEY, rounding=ROUND_HALF_UP)

    return {
        "items": calculated_items,
        "vat_rate": str(vat_rate),
        "prices_include_vat": prices_include_vat,
        "subtotal": str(subtotal),
        "discount_total": str(discount),
        "delivery_total": str(delivery),
        "taxable_base": str(taxable_base),
        "vat_total": str(vat_total),
        "grand_total": str(grand_total),
        "currency": str(payload.get("currency", "RUB")).upper(),
        "customer": payload.get("customer") or {},
        "seller": payload.get("seller") or {},
        "notes": str(payload.get("notes", "")),
    }


def _payload_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def create_document(
    session: Session,
    *,
    document_key: str,
    document_type: str,
    payload: dict[str, Any],
    created_by: str,
    deal_external_id: str | None = None,
    template_version: str = "test-v1",
) -> tuple[CommercialDocument, bool]:
    document_key = document_key.strip()
    document_type = document_type.strip().upper()
    if not document_key or document_type not in {"QUOTE", "INVOICE"}:
        raise DocumentError("document_key and valid document_type are required")

    calculation = calculate_document(payload)
    payload_hash = _payload_hash({"payload": payload, "calculation": calculation})
    existing = session.scalar(
        select(CommercialDocument)
        .where(
            CommercialDocument.document_key == document_key,
            CommercialDocument.payload_hash == payload_hash,
        )
        .order_by(CommercialDocument.version.desc())
    )
    if existing is not None:
        return existing, False

    current = session.scalar(
        select(func.max(CommercialDocument.version)).where(
            CommercialDocument.document_key == document_key
        )
    ) or 0
    document = CommercialDocument(
        id=f"doc-{uuid4().hex}",
        document_key=document_key,
        document_type=document_type,
        version=int(current) + 1,
        deal_external_id=deal_external_id,
        currency=calculation["currency"],
        status="DRAFT",
        template_version=template_version,
        payload_hash=payload_hash,
        subtotal=Decimal(calculation["subtotal"]),
        discount_total=Decimal(calculation["discount_total"]),
        delivery_total=Decimal(calculation["delivery_total"]),
        vat_total=Decimal(calculation["vat_total"]),
        grand_total=Decimal(calculation["grand_total"]),
        calculation_json={"input": payload, "result": calculation},
        created_by=created_by,
    )
    session.add(document)
    session.add(AuditEvent(
        id=f"audit-{uuid4().hex}",
        event_type="COMMERCIAL_DOCUMENT_CREATED",
        entity_type="commercial_document",
        entity_id=document.id,
        actor=created_by,
        payload_json={"document_key": document_key, "version": document.version},
    ))
    session.commit()
    session.refresh(document)
    return document, True


def render_document_pdf(
    session: Session,
    *,
    document_id: str,
    output_dir: str | Path,
    actor: str,
) -> CommercialDocument:
    document = session.get(CommercialDocument, document_id)
    if document is None:
        raise DocumentError("Document not found")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    pdf_path = output / f"{document.document_key}_v{document.version}.pdf"

    result = document.calculation_json["result"]
    c = canvas.Canvas(str(pdf_path), pagesize=A4)
    width, height = A4
    y = height - 50
    c.setFont("Helvetica-Bold", 14)
    c.drawString(45, y, f"{document.document_type} {document.document_key} v{document.version}")
    y -= 30
    c.setFont("Helvetica", 9)
    for item in result["items"]:
        line = (
            f'{item["position"]}. {item["name"]} | {item["quantity"]} {item["unit"]} '
            f'x {item["sale_unit_price"]} = {item["line_total"]} {result["currency"]}'
        )
        while len(line) > 105:
            part, line = line[:105], line[105:]
            c.drawString(45, y, part)
            y -= 13
        c.drawString(45, y, line)
        y -= 16
        if y < 90:
            c.showPage()
            c.setFont("Helvetica", 9)
            y = height - 50
    y -= 10
    c.drawString(45, y, f'Subtotal: {result["subtotal"]} {result["currency"]}')
    y -= 14
    c.drawString(45, y, f'VAT: {result["vat_total"]} {result["currency"]}')
    y -= 14
    c.setFont("Helvetica-Bold", 10)
    c.drawString(45, y, f'Grand total: {result["grand_total"]} {result["currency"]}')
    c.save()

    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    document.pdf_path = str(pdf_path)
    document.pdf_sha256 = digest
    document.status = "FINALIZED"
    document.finalized_at = datetime.now(timezone.utc)
    session.add(AuditEvent(
        id=f"audit-{uuid4().hex}",
        event_type="COMMERCIAL_DOCUMENT_RENDERED",
        entity_type="commercial_document",
        entity_id=document.id,
        actor=actor,
        payload_json={"pdf_path": str(pdf_path), "pdf_sha256": digest},
    ))
    session.commit()
    session.refresh(document)
    return document


def mock_update_budget(
    session: Session,
    *,
    document_id: str,
    deal_external_id: str,
    actor: str,
) -> tuple[BudgetUpdate, bool]:
    document = session.get(CommercialDocument, document_id)
    if document is None:
        raise DocumentError("Document not found")
    if document.status != "FINALIZED":
        raise DocumentError("Only finalized documents can update budget")
    key_raw = f"{document.id}|{document.version}|{deal_external_id}|{document.grand_total}"
    key = hashlib.sha256(key_raw.encode()).hexdigest()
    existing = session.scalar(select(BudgetUpdate).where(BudgetUpdate.idempotency_key == key))
    if existing is not None:
        return existing, False

    update = BudgetUpdate(
        id=f"budget-{uuid4().hex}",
        document_id=document.id,
        deal_external_id=deal_external_id,
        amount=document.grand_total,
        currency=document.currency,
        provider="MOCK",
        status="SUCCESS",
        idempotency_key=key,
        response_json={"mock": True, "budget": str(document.grand_total)},
        completed_at=datetime.now(timezone.utc),
    )
    session.add(update)
    session.add(AuditEvent(
        id=f"audit-{uuid4().hex}",
        event_type="MOCK_AMOCRM_BUDGET_UPDATED",
        entity_type="commercial_document",
        entity_id=document.id,
        actor=actor,
        idempotency_key=f"audit-{key}",
        payload_json={"deal_external_id": deal_external_id, "amount": str(document.grand_total)},
    ))
    session.commit()
    session.refresh(update)
    return update, True


def document_to_dict(document: CommercialDocument) -> dict[str, Any]:
    return {
        "id": document.id,
        "document_key": document.document_key,
        "document_type": document.document_type,
        "version": document.version,
        "deal_external_id": document.deal_external_id,
        "currency": document.currency,
        "status": document.status,
        "template_version": document.template_version,
        "subtotal": str(document.subtotal),
        "discount_total": str(document.discount_total),
        "delivery_total": str(document.delivery_total),
        "vat_total": str(document.vat_total),
        "grand_total": str(document.grand_total),
        "pdf_path": document.pdf_path,
        "pdf_sha256": document.pdf_sha256,
        "created_by": document.created_by,
        "created_at": document.created_at.isoformat(),
        "finalized_at": document.finalized_at.isoformat() if document.finalized_at else None,
    }
