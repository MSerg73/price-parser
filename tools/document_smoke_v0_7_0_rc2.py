from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from price_parser import __version__
from price_parser.db.commands import database_status, upgrade_database
from price_parser.db.session import create_engine_and_session
from price_parser.documents import create_document, mock_update_budget, render_document_pdf


def run_smoke(
    *,
    output: Path,
    database_url: str | None = None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_url = database_url or f"sqlite:///{(root / 'smoke.db').as_posix()}"
        upgrade_database(db_url)
        engine, factory = create_engine_and_session(db_url)
        try:
            payload = {
                "currency": "RUB",
                "vat_rate": "20",
                "prices_include_vat": False,
                "delivery": "1000",
                "discount": "500",
                "items": [{
                    "name": "Труба 12Х18Н10Т 5x1.5",
                    "unit": "кг",
                    "quantity": "10",
                    "unit_price": "100",
                    "markup_rate": "20",
                    "source_offer_id": "offer-smoke",
                }],
            }
            with factory() as session:
                doc, created = create_document(
                    session,
                    document_key="KP-SMOKE",
                    document_type="QUOTE",
                    payload=payload,
                    created_by="smoke",
                    deal_external_id="deal-smoke",
                )
                rendered = render_document_pdf(
                    session,
                    document_id=doc.id,
                    output_dir=root / "pdf",
                    actor="smoke",
                )
                budget, budget_created = mock_update_budget(
                    session,
                    document_id=doc.id,
                    deal_external_id="deal-smoke",
                    actor="smoke",
                )
                result: dict[str, object] = {
                    "success": True,
                    "version": __version__,
                    "database_revision": database_status(db_url)["revision"],
                    "document_created": created,
                    "document_status": rendered.status,
                    "grand_total": str(rendered.grand_total),
                    "pdf_sha256": rendered.pdf_sha256,
                    "budget_created": budget_created,
                    "budget_status": budget.status,
                    "live_amocrm_e2e": "NOT_VERIFIED",
                    "customer_template": "NOT_PROVIDED",
                }
        finally:
            # Windows keeps SQLite files locked while pooled connections remain open.
            # Dispose before TemporaryDirectory attempts to delete smoke.db.
            engine.dispose()

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args()

    result = run_smoke(
        output=Path(args.output),
        database_url=args.database_url,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
