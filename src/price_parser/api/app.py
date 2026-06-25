from __future__ import annotations

import hmac
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from price_parser import __version__
from price_parser.db.commands import database_status, upgrade_database
from price_parser.db.models import Offer
from price_parser.mail import (
    MailProcessingError,
    create_mail_request,
    get_mail_request,
    list_mail_attachments,
    list_mail_messages,
    replay_eml,
)
from price_parser.mail.service import mail_message_to_dict, mail_request_to_dict
from price_parser.documents import (
    DocumentError,
    create_document,
    document_to_dict,
    mock_update_budget,
    render_document_pdf,
)
from price_parser.db.models import CommercialDocument
from price_parser.db.services import (
    PersistenceError,
    apply_review_decision,
    decision_to_dict,
    get_import_job,
    import_job_to_dict,
    import_pilot_report,
    list_reviews,
    llm_run_to_dict,
    persistence_counts,
    record_llm_run,
    record_review_decision,
    search_offers,
)
from price_parser.db.session import create_engine_and_session

from .schemas import (
    LLMRunRequest,
    MailReplayRequest,
    MailRequestCreate,
    PilotImportRequest,
    ReviewApplyRequest,
    ReviewDecisionRequest,
    CommercialDocumentCreate,
    CommercialDocumentRender,
    BudgetMockUpdate,
)


def create_app(
    database_url: str | None = None,
    *,
    auto_migrate: bool = False,
    api_token: str | None = None,
) -> FastAPI:
    if auto_migrate:
        upgrade_database(database_url)
    engine, session_factory = create_engine_and_session(database_url)

    app = FastAPI(
        title="Price Parser Backend API",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.database_url = database_url
    app.state.api_token = (api_token or "").strip() or None

    def get_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
        expected = app.state.api_token
        if expected and (
            x_api_key is None or not hmac.compare_digest(expected, x_api_key)
        ):
            raise HTTPException(status_code=401, detail="Invalid API key")

    dependencies = [Depends(require_api_key)]

    @app.get("/health", dependencies=dependencies)
    def health(session: Session = Depends(get_session)) -> dict[str, Any]:
        session.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "version": __version__,
            "database": database_status(database_url),
            "live_llm_e2e": "NOT_VERIFIED",
            "live_mail_e2e": "NOT_VERIFIED",
        }

    @app.get("/stats", dependencies=dependencies)
    def stats(session: Session = Depends(get_session)) -> dict[str, int]:
        return persistence_counts(session)

    @app.post("/imports/pilot", dependencies=dependencies)
    def create_pilot_import(
        request: PilotImportRequest,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            job, created = import_pilot_report(
                session,
                request.report_dir,
                actor=request.actor,
                idempotency_key=request.idempotency_key,
            )
        except PersistenceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"created": created, "job": import_job_to_dict(job)}

    @app.get("/imports/{job_id}", dependencies=dependencies)
    def read_import(
        job_id: str,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        job = get_import_job(session, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Import job not found")
        return import_job_to_dict(job)

    @app.get("/offers/{offer_id}", dependencies=dependencies)
    def read_offer(
        offer_id: str,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        offer = session.get(Offer, offer_id)
        if offer is None:
            raise HTTPException(status_code=404, detail="Offer not found")
        return _offer_to_dict(offer)

    @app.get("/search", dependencies=dependencies)
    def search(
        profile: str = Query(min_length=1),
        grade: str = Query(min_length=1),
        dimensions: str | None = None,
        limit: int = Query(default=20, ge=1, le=200),
        fuzzy_threshold: float = Query(default=0.82, ge=0.0, le=1.0),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            return search_offers(
                session,
                profile=profile,
                grade=grade,
                dimensions=dimensions,
                limit=limit,
                fuzzy_threshold=fuzzy_threshold,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/reviews", dependencies=dependencies)
    def reviews(
        status: str | None = Query(default="OPEN"),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return list_reviews(
            session,
            status=None if status in (None, "", "ALL") else status,
            limit=limit,
            offset=offset,
        )

    @app.post("/reviews/{offer_id}/decisions", dependencies=dependencies)
    def create_review_decision(
        offer_id: str,
        request: ReviewDecisionRequest,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            decision, created = record_review_decision(
                session,
                offer_id=offer_id,
                action=request.action,
                operator=request.operator,
                comment=request.comment,
                changes=request.changes,
                rule_id=request.rule_id,
                rule_version=request.rule_version,
            )
        except PersistenceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"created": created, "decision": decision_to_dict(decision)}

    @app.post("/reviews/{offer_id}/apply", dependencies=dependencies)
    def apply_decision(
        offer_id: str,
        request: ReviewApplyRequest,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            decision, changed = apply_review_decision(
                session,
                offer_id=offer_id,
                applied_by=request.applied_by,
            )
        except PersistenceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"changed": changed, "decision": decision_to_dict(decision)}

    @app.post("/llm-runs", dependencies=dependencies)
    def create_llm_run(
        request: LLMRunRequest,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            run, created = record_llm_run(
                session,
                fingerprint=request.fingerprint,
                provider=request.provider,
                status=request.status,
                model=request.model,
                input_count=request.input_count,
                output_count=request.output_count,
                request_hash=request.request_hash,
                response_hash=request.response_hash,
                live_model_verified=request.live_model_verified,
                metadata=request.metadata,
                error_text=request.error_text,
            )
        except PersistenceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"created": created, "run": llm_run_to_dict(run)}


    @app.post("/mail/requests", dependencies=dependencies)
    def create_outbound_mail_request(
        request: MailRequestCreate,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            item, created = create_mail_request(
                session,
                request_key=request.request_key,
                recipient_email=request.recipient_email,
                sender_email=request.sender_email,
                subject=request.subject,
                body_text=request.body_text,
                supplier_id=request.supplier_id,
                deal_external_id=request.deal_external_id,
                metadata=request.metadata,
                actor=request.actor,
            )
        except MailProcessingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"created": created, "request": mail_request_to_dict(item)}

    @app.get("/mail/requests/{request_id}", dependencies=dependencies)
    def read_mail_request(
        request_id: str,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        item = get_mail_request(session, request_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Mail request not found")
        return mail_request_to_dict(item)

    @app.post("/mail/replay", dependencies=dependencies)
    def replay_mail_message(
        request: MailReplayRequest,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            item, created = replay_eml(
                session,
                request.eml_path,
                storage_root=request.storage_root,
                actor=request.actor,
                max_attachment_bytes=request.max_attachment_bytes,
            )
        except MailProcessingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"created": created, "message": mail_message_to_dict(item)}

    @app.get("/mail/messages", dependencies=dependencies)
    def mail_messages(
        status: str | None = Query(default=None),
        request_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return list_mail_messages(
            session,
            status=status,
            request_id=request_id,
            limit=limit,
            offset=offset,
        )

    @app.get("/mail/attachments", dependencies=dependencies)
    def mail_attachments(
        validation_status: str | None = Query(default=None),
        parser_status: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return list_mail_attachments(
            session,
            validation_status=validation_status,
            parser_status=parser_status,
            limit=limit,
            offset=offset,
        )


    @app.post("/documents", dependencies=dependencies)
    def create_commercial_document(
        request: CommercialDocumentCreate,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            document, created = create_document(
                session,
                document_key=request.document_key,
                document_type=request.document_type,
                payload=request.payload,
                created_by=request.created_by,
                deal_external_id=request.deal_external_id,
                template_version=request.template_version,
            )
        except DocumentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"created": created, "document": document_to_dict(document)}

    @app.get("/documents/{document_id}", dependencies=dependencies)
    def read_commercial_document(
        document_id: str,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        document = session.get(CommercialDocument, document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")
        return document_to_dict(document)

    @app.post("/documents/{document_id}/render", dependencies=dependencies)
    def render_commercial_document(
        document_id: str,
        request: CommercialDocumentRender,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            document = render_document_pdf(
                session,
                document_id=document_id,
                output_dir=request.output_dir,
                actor=request.actor,
            )
        except DocumentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return document_to_dict(document)

    @app.post("/documents/{document_id}/budget/mock", dependencies=dependencies)
    def update_budget_mock(
        document_id: str,
        request: BudgetMockUpdate,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            update, created = mock_update_budget(
                session,
                document_id=document_id,
                deal_external_id=request.deal_external_id,
                actor=request.actor,
            )
        except DocumentError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "created": created,
            "update": {
                "id": update.id,
                "document_id": update.document_id,
                "deal_external_id": update.deal_external_id,
                "amount": str(update.amount),
                "currency": update.currency,
                "provider": update.provider,
                "status": update.status,
            },
        }

    return app


def _offer_to_dict(offer: Offer) -> dict[str, Any]:
    return {
        "id": offer.id,
        "supplier": offer.supplier.name,
        "nomenclature_key": offer.nomenclature_key,
        "profile": offer.profile,
        "grade": offer.grade,
        "dimensions": [
            _decimal(offer.dim1),
            _decimal(offer.dim2),
            _decimal(offer.dim3),
        ],
        "dimension_units": [offer.dim1_unit, None, None],
        "availability": offer.availability,
        "quantity_value": _decimal(offer.quantity_value),
        "quantity_unit": offer.quantity_unit,
        "price_rub_kg": _decimal(offer.price_rub_kg),
        "parse_status": offer.parse_status,
        "requires_review": offer.requires_review,
        "reference_research_required": offer.reference_research_required,
        "automatic_application_performed": offer.automatic_application_performed,
        "source": {
            "file": offer.source_row.document.filename,
            "sheet": offer.source_row.sheet_name,
            "row": offer.source_row.row_number,
            "block": offer.source_row.source_block,
            "reference": offer.source_row.source_reference,
            "original_text": offer.source_row.raw_text,
        },
        "payload": offer.payload_json,
    }


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")
