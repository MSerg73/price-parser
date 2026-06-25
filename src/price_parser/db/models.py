from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Supplier(Base):
    __tablename__ = "suppliers"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    offers: Mapped[list["Offer"]] = relationship(back_populates="supplier")


class SourceDocument(Base):
    __tablename__ = "source_documents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    rows: Mapped[list["SourceRow"]] = relationship(back_populates="document")


class ImportJob(Base):
    __tablename__ = "import_jobs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    source_bundle_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    actor: Mapped[str] = mapped_column(String(255), nullable=False, default="system")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    counts_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_text: Mapped[str | None] = mapped_column(Text)

    offers: Mapped[list["Offer"]] = relationship(back_populates="import_job")
    offer_links: Mapped[list["ImportJobOffer"]] = relationship(
        back_populates="import_job", cascade="all, delete-orphan"
    )


class ImportJobOffer(Base):
    __tablename__ = "import_job_offers"

    import_job_id: Mapped[str] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    offer_id: Mapped[str] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), primary_key=True
    )
    import_state: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    import_job: Mapped[ImportJob] = relationship(back_populates="offer_links")
    offer: Mapped["Offer"] = relationship(back_populates="import_links")


class SourceRow(Base):
    __tablename__ = "source_rows"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "sheet_name",
            "row_number",
            "source_block",
            "raw_hash",
            name="uq_source_rows_provenance",
        ),
        Index("ix_source_rows_document_sheet_row", "document_id", "sheet_name", "row_number"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("source_documents.id", ondelete="RESTRICT"), nullable=False
    )
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_block: Mapped[str | None] = mapped_column(String(255))
    source_reference: Mapped[str] = mapped_column(String(1024), nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    document: Mapped[SourceDocument] = relationship(back_populates="rows")
    offers: Mapped[list["Offer"]] = relationship(back_populates="source_row")


class Offer(Base):
    __tablename__ = "offers"
    __table_args__ = (
        Index("ix_offers_profile_grade", "profile", "grade_key"),
        Index("ix_offers_review_flags", "requires_review", "reference_research_required"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    import_job_id: Mapped[str] = mapped_column(
        ForeignKey("import_jobs.id", ondelete="RESTRICT"), nullable=False
    )
    source_row_id: Mapped[str] = mapped_column(
        ForeignKey("source_rows.id", ondelete="RESTRICT"), nullable=False
    )
    supplier_id: Mapped[str] = mapped_column(
        ForeignKey("suppliers.id", ondelete="RESTRICT"), nullable=False
    )
    nomenclature_key: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    profile: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    grade: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    grade_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    dim1: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    dim2: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    dim3: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    dim1_unit: Mapped[str | None] = mapped_column(String(32))
    availability: Mapped[str | None] = mapped_column(String(255))
    quantity_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    quantity_unit: Mapped[str | None] = mapped_column(String(32))
    price_rub_kg: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    display_name: Mapped[str | None] = mapped_column(String(512))
    comment: Mapped[str | None] = mapped_column(Text)
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(6, 5), nullable=False, default=Decimal("0")
    )
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reference_research_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    reference_status: Mapped[str | None] = mapped_column(String(64))
    automatic_application_performed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    import_job: Mapped[ImportJob] = relationship(back_populates="offers")
    source_row: Mapped[SourceRow] = relationship(back_populates="offers")
    supplier: Mapped[Supplier] = relationship(back_populates="offers")
    review_item: Mapped["ReviewItem | None"] = relationship(
        back_populates="offer", uselist=False
    )
    reference_task: Mapped["ReferenceResearchTask | None"] = relationship(
        back_populates="offer", uselist=False
    )
    decisions: Mapped[list["ReviewDecision"]] = relationship(back_populates="offer")
    import_links: Mapped[list[ImportJobOffer]] = relationship(
        back_populates="offer", cascade="all, delete-orphan"
    )


class ReviewItem(Base):
    __tablename__ = "review_items"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    offer_id: Mapped[str] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reasons_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    warnings_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    offer: Mapped[Offer] = relationship(back_populates="review_item")


class ReviewDecision(Base):
    __tablename__ = "review_decisions"
    __table_args__ = (Index("ix_review_decisions_offer_time", "offer_id", "decided_at"),)

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    offer_id: Mapped[str] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_status: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    changes_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    operator: Mapped[str] = mapped_column(String(255), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(255))
    rule_version: Mapped[str | None] = mapped_column(String(64))
    previous_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("review_decisions.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_by: Mapped[str | None] = mapped_column(String(255))

    offer: Mapped[Offer] = relationship(back_populates="decisions", foreign_keys=[offer_id])


class ReferenceResearchTask(Base):
    __tablename__ = "reference_research_tasks"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    offer_id: Mapped[str] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN")
    source_designation: Mapped[str | None] = mapped_column(String(255))
    reference_status: Mapped[str | None] = mapped_column(String(64))
    hints_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )

    offer: Mapped[Offer] = relationship(back_populates="reference_task")


class LLMRun(Base):
    __tablename__ = "llm_runs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    response_hash: Mapped[str | None] = mapped_column(String(64))
    automatic_application_performed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    live_model_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_text: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        Index("ix_audit_events_created_at", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

class MailRequest(Base):
    __tablename__ = "mail_requests"
    __table_args__ = (
        Index("ix_mail_requests_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    supplier_id: Mapped[str | None] = mapped_column(
        ForeignKey("suppliers.id", ondelete="SET NULL")
    )
    deal_external_id: Mapped[str | None] = mapped_column(String(128))
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    sender_email: Mapped[str | None] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(998), nullable=False)
    subject_token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    message_id: Mapped[str] = mapped_column(String(998), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    messages: Mapped[list["MailMessage"]] = relationship(back_populates="request")


class MailMessage(Base):
    __tablename__ = "mail_messages"
    __table_args__ = (
        Index("ix_mail_messages_request_received", "request_id", "received_at"),
        Index("ix_mail_messages_status", "processing_status"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(
        ForeignKey("mail_requests.id", ondelete="SET NULL")
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    message_id_header: Mapped[str | None] = mapped_column(String(998), unique=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(998))
    references_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    sender_email: Mapped[str | None] = mapped_column(String(320))
    recipients_json: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    subject: Mapped[str] = mapped_column(String(998), nullable=False, default="")
    text_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    html_body: Mapped[str | None] = mapped_column(Text)
    raw_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    raw_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    link_method: Mapped[str | None] = mapped_column(String(32))
    processing_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="RECEIVED"
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    request: Mapped[MailRequest | None] = relationship(back_populates="messages")
    attachments: Mapped[list["MailAttachment"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )
    attempts: Mapped[list["MailProcessingAttempt"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )


class MailAttachment(Base):
    __tablename__ = "mail_attachments"
    __table_args__ = (
        UniqueConstraint(
            "message_id",
            "sha256",
            "safe_filename",
            name="uq_mail_attachments_message_hash_name",
        ),
        Index("ix_mail_attachments_status", "validation_status", "parser_status"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("mail_messages.id", ondelete="CASCADE"), nullable=False
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    safe_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    validation_status: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    error_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    message: Mapped[MailMessage] = relationship(back_populates="attachments")


class MailProcessingAttempt(Base):
    __tablename__ = "mail_processing_attempts"
    __table_args__ = (
        UniqueConstraint(
            "message_id", "operation", "attempt_number",
            name="uq_mail_processing_attempt"
        ),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("mail_messages.id", ondelete="CASCADE"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )

    message: Mapped[MailMessage] = relationship(back_populates="attempts")

class CommercialDocument(Base):
    __tablename__ = "commercial_documents"
    __table_args__ = (
        UniqueConstraint("document_key", "version", name="uq_commercial_documents_key_version"),
        Index("ix_commercial_documents_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    document_key: Mapped[str] = mapped_column(String(128), nullable=False)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    deal_external_id: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    discount_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    delivery_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    vat_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    grand_total: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    calculation_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    pdf_path: Mapped[str | None] = mapped_column(String(2048))
    pdf_sha256: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    budget_updates: Mapped[list["BudgetUpdate"]] = relationship(back_populates="document")


class BudgetUpdate(Base):
    __tablename__ = "budget_updates"
    __table_args__ = (
        Index("ix_budget_updates_deal_created", "deal_external_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("commercial_documents.id", ondelete="CASCADE"), nullable=False
    )
    deal_external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="RUB")
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    response_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[CommercialDocument] = relationship(back_populates="budget_updates")

