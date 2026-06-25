from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from price_parser.nomenclature import (
    NomenclatureSearchService,
    SearchOptions,
    SearchQuery,
    SearchableItem,
    load_catalog,
    parse_dimensions,
)
from price_parser.nomenclature.normalization import normalize_grade_key
from price_parser.operator_review import review_item_fingerprint

from .models import (
    AuditEvent,
    ImportJob,
    ImportJobOffer,
    LLMRun,
    MailAttachment,
    MailMessage,
    MailProcessingAttempt,
    MailRequest,
    Offer,
    ReferenceResearchTask,
    ReviewDecision,
    ReviewItem,
    SourceDocument,
    SourceRow,
    Supplier,
    utc_now,
)

REQUIRED_PILOT_FILES = (
    "normalized_items.jsonl",
    "normalization_review_queue.jsonl",
    "reference_research_queue.jsonl",
    "source_manifest.json",
)
SUPPORTED_REVIEW_ACTIONS = frozenset({"ACCEPT_AS_IS", "UPDATE_FIELDS", "DEFER"})
CONFIRMED_REVIEW_ACTIONS = frozenset({"ACCEPT_AS_IS", "UPDATE_FIELDS"})
REVIEW_CHANGE_FIELDS = frozenset(
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
        "dim1_unit",
    }
)
DECIMAL_FIELDS = frozenset({"dim1", "dim2", "dim3", "quantity_value"})


class PersistenceError(ValueError):
    pass


def import_pilot_report(
    session: Session,
    report_dir: str | Path,
    *,
    actor: str = "system",
    idempotency_key: str | None = None,
) -> tuple[ImportJob, bool]:
    directory = Path(report_dir).expanduser().resolve()
    if not directory.is_dir():
        raise PersistenceError(f"Каталог отчётов не найден: {directory}")

    files = {name: directory / name for name in REQUIRED_PILOT_FILES}
    missing = [name for name, path in files.items() if not path.is_file()]
    if missing:
        raise PersistenceError(
            "В каталоге отчётов отсутствуют файлы: " + ", ".join(missing)
        )

    source_bundle_hash = _bundle_hash(files.values())
    key = (idempotency_key or f"pilot:{source_bundle_hash}").strip()
    if not key:
        raise PersistenceError("Пустой idempotency key")

    existing = session.scalar(select(ImportJob).where(ImportJob.idempotency_key == key))
    if existing is not None:
        return existing, False

    job = ImportJob(
        id=_stable_id("import", key, length=24),
        idempotency_key=key,
        source_bundle_hash=source_bundle_hash,
        status="RUNNING",
        actor=actor.strip() or "system",
        counts_json={},
    )
    session.add(job)
    session.commit()

    try:
        source_manifest = _load_json(files["source_manifest.json"])
        if not isinstance(source_manifest, list):
            raise PersistenceError("source_manifest.json должен содержать массив")

        normalized = _load_jsonl(files["normalized_items.jsonl"])
        review_rows = _load_jsonl(files["normalization_review_queue.jsonl"])
        reference_rows = _load_jsonl(files["reference_research_queue.jsonl"])

        documents = _upsert_documents(session, source_manifest)
        suppliers: dict[str, Supplier] = {}
        counts = {
            "documents": len(documents),
            "offers_input": len(normalized),
            "offers_created": 0,
            "offers_existing": 0,
            "import_links_created": 0,
            "review_items_created": 0,
            "reference_tasks_created": 0,
        }

        for row in normalized:
            offer_id = _required_text(row, "id")
            filename = _required_text(row, "source_file")
            document = documents.get(filename)
            if document is None:
                raise PersistenceError(
                    f"Файл {filename!r} отсутствует в source_manifest.json"
                )

            supplier_name = _required_text(row, "supplier")
            supplier = suppliers.get(supplier_name)
            if supplier is None:
                supplier = session.scalar(
                    select(Supplier).where(Supplier.name == supplier_name)
                )
                if supplier is None:
                    supplier = Supplier(
                        id=_stable_id("supplier", supplier_name.casefold()),
                        name=supplier_name,
                    )
                    session.add(supplier)
                    session.flush()
                suppliers[supplier_name] = supplier

            source_row = _get_or_create_source_row(session, document, row)
            payload_hash = _hash_json(row)
            existing_offer = session.get(Offer, offer_id)
            if existing_offer is not None:
                if existing_offer.payload_hash != payload_hash:
                    raise PersistenceError(
                        f"Коллизия offer id {offer_id}: payload отличается"
                    )
                counts["offers_existing"] += 1
                offer = existing_offer
                import_state = "EXISTING"
            else:
                offer = Offer(
                    id=offer_id,
                    import_job_id=job.id,
                    source_row_id=source_row.id,
                    supplier_id=supplier.id,
                    nomenclature_key=_required_text(row, "nomenclature_key"),
                    profile=_required_text(row, "profile"),
                    grade=_required_text(row, "grade"),
                    grade_key=str(row.get("grade_key") or "").strip(),
                    dim1=_decimal_or_none(row.get("dim1")),
                    dim2=_decimal_or_none(row.get("dim2")),
                    dim3=_decimal_or_none(row.get("dim3")),
                    dim1_unit=_optional_text(row.get("dim1_unit")),
                    availability=_optional_text(row.get("availability")),
                    quantity_value=_decimal_or_none(row.get("quantity_value")),
                    quantity_unit=_optional_text(row.get("quantity_unit")),
                    price_rub_kg=_decimal_or_none(row.get("price_rub_kg")),
                    display_name=_optional_text(row.get("display_name")),
                    comment=_optional_text(row.get("comment")),
                    parse_status=_required_text(row, "parse_status"),
                    confidence=_decimal_or_none(row.get("confidence")) or Decimal("0"),
                    requires_review=bool(row.get("requires_review")),
                    reference_research_required=bool(
                        row.get("reference_research_required")
                    ),
                    reference_status=_optional_text(row.get("reference_status")),
                    automatic_application_performed=bool(
                        row.get("automatic_application_performed", False)
                    ),
                    payload_hash=payload_hash,
                    payload_json=row,
                )
                session.add(offer)
                counts["offers_created"] += 1
                import_state = "CREATED"

            session.flush()
            link = session.get(ImportJobOffer, (job.id, offer_id))
            if link is None:
                session.add(
                    ImportJobOffer(
                        import_job_id=job.id,
                        offer_id=offer_id,
                        import_state=import_state,
                        payload_hash=payload_hash,
                    )
                )
                counts["import_links_created"] += 1

        session.flush()
        for row in review_rows:
            offer_id = _required_text(row, "id")
            if session.get(Offer, offer_id) is None:
                raise PersistenceError(
                    f"REVIEW ссылается на отсутствующее предложение: {offer_id}"
                )
            if session.scalar(
                select(ReviewItem).where(ReviewItem.offer_id == offer_id)
            ) is None:
                session.add(
                    ReviewItem(
                        id=_stable_id("review", offer_id),
                        offer_id=offer_id,
                        status="OPEN",
                        source_fingerprint=review_item_fingerprint(row),
                        reasons_json=list(row.get("review_reasons") or []),
                        warnings_json=list(row.get("warnings") or []),
                    )
                )
                counts["review_items_created"] += 1

        for row in reference_rows:
            offer_id = _required_text(row, "id")
            if session.get(Offer, offer_id) is None:
                raise PersistenceError(
                    f"НТД-задача ссылается на отсутствующее предложение: {offer_id}"
                )
            if session.scalar(
                select(ReferenceResearchTask).where(
                    ReferenceResearchTask.offer_id == offer_id
                )
            ) is None:
                session.add(
                    ReferenceResearchTask(
                        id=_stable_id("reference", offer_id),
                        offer_id=offer_id,
                        status="OPEN",
                        source_designation=_optional_text(row.get("grade")),
                        reference_status=_optional_text(row.get("reference_status")),
                        hints_json=list(row.get("operator_hints") or []),
                        payload_json=row,
                    )
                )
                counts["reference_tasks_created"] += 1

        _append_audit(
            session,
            event_type="PILOT_IMPORT_COMPLETED",
            entity_type="IMPORT_JOB",
            entity_id=job.id,
            actor=job.actor,
            idempotency_key=_hash_text(f"audit:{job.id}:completed"),
            payload={"source_bundle_hash": source_bundle_hash, "counts": counts},
        )
        job.status = "COMPLETED"
        job.completed_at = utc_now()
        job.counts_json = counts
        session.commit()
        return job, True
    except Exception as exc:
        session.rollback()
        failed = session.get(ImportJob, job.id)
        if failed is not None:
            failed.status = "FAILED"
            failed.completed_at = utc_now()
            failed.error_text = str(exc)
            session.commit()
        raise


def get_import_job(session: Session, job_id: str) -> ImportJob | None:
    return session.get(ImportJob, job_id)


def persistence_counts(session: Session) -> dict[str, int]:
    tables = {
        "suppliers": Supplier,
        "source_documents": SourceDocument,
        "source_rows": SourceRow,
        "import_jobs": ImportJob,
        "import_job_offers": ImportJobOffer,
        "offers": Offer,
        "review_items": ReviewItem,
        "review_decisions": ReviewDecision,
        "reference_research_tasks": ReferenceResearchTask,
        "llm_runs": LLMRun,
        "mail_requests": MailRequest,
        "mail_messages": MailMessage,
        "mail_attachments": MailAttachment,
        "mail_processing_attempts": MailProcessingAttempt,
        "audit_events": AuditEvent,
    }
    return {
        name: int(session.scalar(select(func.count()).select_from(model)) or 0)
        for name, model in tables.items()
    }


def search_offers(
    session: Session,
    *,
    profile: str,
    grade: str,
    dimensions: str | None = None,
    limit: int = 20,
    fuzzy_threshold: float = 0.82,
) -> dict[str, Any]:
    rows = session.scalars(select(Offer).order_by(Offer.id)).all()
    items = [
        SearchableItem(
            id=row.id,
            supplier=row.supplier.name,
            profile=row.profile,
            grade=row.grade,
            dimensions=(row.dim1, row.dim2, row.dim3),
            dimension_units=(row.dim1_unit, None, None),
            source_reference=row.source_row.source_reference,
            payload={
                **dict(row.payload_json),
                "requires_review": row.requires_review,
                "reference_research_required": row.reference_research_required,
            },
        )
        for row in rows
    ]
    dimension_text = dimensions or ""
    units = (
        ("INCH", None, None)
        if "/" in dimension_text
        or any(token in dimension_text.lower() for token in ('"', "дюйм", "in"))
        else (None, None, None)
    )
    response = NomenclatureSearchService(load_catalog()).search(
        SearchQuery(
            profile=profile,
            grade=grade,
            dimensions=parse_dimensions(dimensions),
            source_reference="BACKEND_API",
            dimension_units=units,
        ),
        items,
        SearchOptions(limit=limit, fuzzy_threshold=fuzzy_threshold),
    )
    return response.to_dict()


def list_reviews(
    session: Session,
    *,
    status: str | None = "OPEN",
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    statement = select(ReviewItem).order_by(ReviewItem.created_at, ReviewItem.id)
    if status:
        statement = statement.where(ReviewItem.status == status.upper())
    total_statement = select(func.count()).select_from(ReviewItem)
    if status:
        total_statement = total_statement.where(ReviewItem.status == status.upper())
    rows = session.scalars(statement.offset(offset).limit(limit)).all()
    return {
        "total": int(session.scalar(total_statement) or 0),
        "limit": limit,
        "offset": offset,
        "items": [_review_to_dict(row) for row in rows],
    }


def record_review_decision(
    session: Session,
    *,
    offer_id: str,
    action: str,
    operator: str,
    comment: str,
    changes: dict[str, Any] | None = None,
    rule_id: str | None = None,
    rule_version: str | None = None,
) -> tuple[ReviewDecision, bool]:
    review = session.scalar(select(ReviewItem).where(ReviewItem.offer_id == offer_id))
    if review is None:
        raise PersistenceError(f"Позиция {offer_id!r} отсутствует в REVIEW")

    action = action.strip().upper()
    if action not in SUPPORTED_REVIEW_ACTIONS:
        raise PersistenceError(f"Недопустимое действие REVIEW: {action}")
    operator = operator.strip()
    comment = comment.strip()
    if not operator or not comment:
        raise PersistenceError("Оператор и комментарий обязательны")

    normalized_changes = _validate_review_changes(action, changes or {})
    rule_id = _optional_text(rule_id)
    rule_version = _optional_text(rule_version)
    if action == "UPDATE_FIELDS" and (not rule_id or not rule_version):
        raise PersistenceError(
            "Для UPDATE_FIELDS обязательны rule_id и rule_version"
        )

    latest = session.scalar(
        select(ReviewDecision)
        .where(ReviewDecision.offer_id == offer_id)
        .order_by(ReviewDecision.decided_at.desc(), ReviewDecision.id.desc())
        .limit(1)
    )
    payload = {
        "offer_id": offer_id,
        "source_fingerprint": review.source_fingerprint,
        "action": action,
        "operator": operator,
        "comment": comment,
        "changes": normalized_changes,
        "rule_id": rule_id,
        "rule_version": rule_version,
    }
    key = _hash_json(payload)
    existing = session.scalar(
        select(ReviewDecision).where(ReviewDecision.idempotency_key == key)
    )
    if existing is not None:
        return existing, False

    decision = ReviewDecision(
        id=_stable_id("decision", key, length=32),
        offer_id=offer_id,
        idempotency_key=key,
        source_fingerprint=review.source_fingerprint,
        decision_status=(
            "CONFIRMED" if action in CONFIRMED_REVIEW_ACTIONS else "DEFERRED"
        ),
        action=action,
        changes_json=normalized_changes,
        operator=operator,
        comment=comment,
        rule_id=rule_id,
        rule_version=rule_version,
        previous_decision_id=latest.id if latest else None,
    )
    session.add(decision)
    review.status = "DEFERRED" if action == "DEFER" else "DECIDED"
    _append_audit(
        session,
        event_type="REVIEW_DECISION_RECORDED",
        entity_type="OFFER",
        entity_id=offer_id,
        actor=operator,
        idempotency_key=_hash_text(f"audit:decision:{decision.id}"),
        payload={"decision_id": decision.id, "action": action},
    )
    session.commit()
    return decision, True


def apply_review_decision(
    session: Session,
    *,
    offer_id: str,
    applied_by: str,
) -> tuple[ReviewDecision, bool]:
    review = session.scalar(select(ReviewItem).where(ReviewItem.offer_id == offer_id))
    offer = session.get(Offer, offer_id)
    if review is None or offer is None:
        raise PersistenceError(f"Позиция {offer_id!r} отсутствует в REVIEW")

    decision = session.scalar(
        select(ReviewDecision)
        .where(ReviewDecision.offer_id == offer_id)
        .order_by(ReviewDecision.decided_at.desc(), ReviewDecision.id.desc())
        .limit(1)
    )
    if decision is None or decision.decision_status != "CONFIRMED":
        raise PersistenceError("Нет подтверждённого решения для применения")
    if decision.source_fingerprint != review.source_fingerprint:
        raise PersistenceError("Источник изменился после принятия решения")
    if decision.applied_at is not None:
        return decision, False

    payload = dict(offer.payload_json)
    if decision.action == "UPDATE_FIELDS":
        for field, value in decision.changes_json.items():
            setattr(offer, field, _model_value(field, value))
            payload[field] = value
        if "grade" in decision.changes_json:
            offer.grade_key = normalize_grade_key(offer.grade)
            payload["grade_key"] = offer.grade_key
        if {"profile", "grade", "dim1", "dim2", "dim3"} & set(
            decision.changes_json
        ):
            offer.nomenclature_key = _nomenclature_key(offer)
            payload["nomenclature_key"] = offer.nomenclature_key

    offer.requires_review = False
    offer.parse_status = "ACCEPTED"
    payload["requires_review"] = False
    payload["parse_status"] = "ACCEPTED"
    payload["automatic_application_performed"] = False
    offer.payload_json = payload
    offer.payload_hash = _hash_json(payload)
    review.status = "RESOLVED"
    decision.applied_at = utc_now()
    decision.applied_by = applied_by.strip() or "system"
    _append_audit(
        session,
        event_type="REVIEW_DECISION_APPLIED",
        entity_type="OFFER",
        entity_id=offer_id,
        actor=decision.applied_by,
        idempotency_key=_hash_text(f"audit:apply:{decision.id}"),
        payload={
            "decision_id": decision.id,
            "action": decision.action,
            "automatic_application_performed": False,
        },
    )
    session.commit()
    return decision, True


def record_llm_run(
    session: Session,
    *,
    fingerprint: str,
    provider: str,
    status: str,
    model: str | None = None,
    input_count: int = 0,
    output_count: int = 0,
    request_hash: str | None = None,
    response_hash: str | None = None,
    live_model_verified: bool = False,
    metadata: dict[str, Any] | None = None,
    error_text: str | None = None,
) -> tuple[LLMRun, bool]:
    fingerprint = fingerprint.strip()
    if not fingerprint:
        raise PersistenceError("fingerprint LLM-запуска обязателен")
    existing = session.scalar(select(LLMRun).where(LLMRun.fingerprint == fingerprint))
    if existing is not None:
        return existing, False
    run = LLMRun(
        id=_stable_id("llm-run", fingerprint, length=32),
        fingerprint=fingerprint,
        provider=provider.strip(),
        model=_optional_text(model),
        status=status.strip().upper(),
        input_count=max(0, input_count),
        output_count=max(0, output_count),
        request_hash=_optional_text(request_hash),
        response_hash=_optional_text(response_hash),
        automatic_application_performed=False,
        live_model_verified=bool(live_model_verified),
        metadata_json=metadata or {},
        error_text=_optional_text(error_text),
        completed_at=utc_now() if status.strip().upper() in {"COMPLETED", "FAILED"} else None,
    )
    session.add(run)
    session.commit()
    return run, True


def _upsert_documents(
    session: Session, source_manifest: list[dict[str, Any]]
) -> dict[str, SourceDocument]:
    result: dict[str, SourceDocument] = {}
    for item in source_manifest:
        filename = _required_text(item, "file")
        sha256 = _required_text(item, "sha256").lower()
        document = session.scalar(
            select(SourceDocument).where(SourceDocument.sha256 == sha256)
        )
        if document is None:
            document = SourceDocument(
                id=_stable_id("document", sha256),
                filename=filename,
                sha256=sha256,
                size_bytes=int(item["size_bytes"]) if item.get("size_bytes") is not None else None,
                metadata_json=item,
            )
            session.add(document)
            session.flush()
        elif document.filename != filename:
            raise PersistenceError(
                f"SHA-256 {sha256} уже связан с другим именем: {document.filename}"
            )
        result[filename] = document
    return result


def _get_or_create_source_row(
    session: Session, document: SourceDocument, row: dict[str, Any]
) -> SourceRow:
    original_text = _required_text(row, "original_text")
    sheet = _required_text(row, "source_sheet")
    row_number = int(row.get("source_row"))
    block = _optional_text(row.get("source_block"))
    raw_hash = _hash_text(original_text)
    identity = _hash_json(
        {
            "document_sha256": document.sha256,
            "sheet": sheet,
            "row": row_number,
            "block": block,
            "raw_hash": raw_hash,
        }
    )
    row_id = _stable_id("row", identity)
    existing = session.get(SourceRow, row_id)
    if existing is not None:
        return existing
    source_row = SourceRow(
        id=row_id,
        document_id=document.id,
        sheet_name=sheet,
        row_number=row_number,
        source_block=block,
        source_reference=_required_text(row, "source_reference"),
        raw_text=original_text,
        raw_hash=raw_hash,
        payload_json={
            "source_file": row.get("source_file"),
            "source_sheet": sheet,
            "source_row": row_number,
            "source_block": block,
            "original_text": original_text,
        },
    )
    session.add(source_row)
    session.flush()
    return source_row


def _review_to_dict(review: ReviewItem) -> dict[str, Any]:
    offer = review.offer
    latest = max(
        offer.decisions,
        key=lambda value: (value.decided_at, value.id),
        default=None,
    )
    return {
        "id": review.id,
        "offer_id": review.offer_id,
        "status": review.status,
        "source_fingerprint": review.source_fingerprint,
        "reasons": review.reasons_json,
        "warnings": review.warnings_json,
        "offer": {
            "profile": offer.profile,
            "grade": offer.grade,
            "dimensions": [_decimal_text(offer.dim1), _decimal_text(offer.dim2), _decimal_text(offer.dim3)],
            "supplier": offer.supplier.name,
            "source_reference": offer.source_row.source_reference,
            "original_text": offer.source_row.raw_text,
        },
        "latest_decision": None if latest is None else _decision_to_dict(latest),
    }


def decision_to_dict(decision: ReviewDecision) -> dict[str, Any]:
    return _decision_to_dict(decision)


def import_job_to_dict(job: ImportJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "idempotency_key": job.idempotency_key,
        "source_bundle_hash": job.source_bundle_hash,
        "status": job.status,
        "actor": job.actor,
        "started_at": job.started_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "counts": job.counts_json,
        "error": job.error_text,
    }


def llm_run_to_dict(run: LLMRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "fingerprint": run.fingerprint,
        "provider": run.provider,
        "model": run.model,
        "status": run.status,
        "input_count": run.input_count,
        "output_count": run.output_count,
        "automatic_application_performed": run.automatic_application_performed,
        "live_model_verified": run.live_model_verified,
        "started_at": run.started_at.isoformat(),
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "metadata": run.metadata_json,
        "error": run.error_text,
    }


def _decision_to_dict(decision: ReviewDecision) -> dict[str, Any]:
    return {
        "id": decision.id,
        "offer_id": decision.offer_id,
        "decision_status": decision.decision_status,
        "action": decision.action,
        "changes": decision.changes_json,
        "operator": decision.operator,
        "comment": decision.comment,
        "rule_id": decision.rule_id,
        "rule_version": decision.rule_version,
        "previous_decision_id": decision.previous_decision_id,
        "decided_at": decision.decided_at.isoformat(),
        "applied_at": decision.applied_at.isoformat() if decision.applied_at else None,
        "applied_by": decision.applied_by,
    }


def _validate_review_changes(action: str, changes: dict[str, Any]) -> dict[str, Any]:
    if action != "UPDATE_FIELDS":
        if changes:
            raise PersistenceError("Изменения допустимы только для UPDATE_FIELDS")
        return {}
    if not changes:
        raise PersistenceError("UPDATE_FIELDS требует хотя бы одно изменение")
    unknown = set(changes) - REVIEW_CHANGE_FIELDS
    if unknown:
        raise PersistenceError(
            "Изменение запрещённых полей: " + ", ".join(sorted(unknown))
        )
    result: dict[str, Any] = {}
    for field, value in changes.items():
        if field in DECIMAL_FIELDS:
            parsed = _decimal_or_none(value)
            result[field] = None if parsed is None else format(parsed, "f")
        else:
            result[field] = None if value is None else str(value).strip()
    return result


def _model_value(field: str, value: Any) -> Any:
    return _decimal_or_none(value) if field in DECIMAL_FIELDS else value


def _nomenclature_key(offer: Offer) -> str:
    return "|".join(
        [
            offer.profile,
            offer.grade_key,
            _decimal_text(offer.dim1) or "",
            _decimal_text(offer.dim2) or "",
            _decimal_text(offer.dim3) or "",
        ]
    )


def _append_audit(
    session: Session,
    *,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor: str,
    idempotency_key: str | None,
    payload: dict[str, Any],
) -> AuditEvent:
    if idempotency_key:
        existing = session.scalar(
            select(AuditEvent).where(AuditEvent.idempotency_key == idempotency_key)
        )
        if existing is not None:
            return existing
    event = AuditEvent(
        id=_stable_id(
            "audit",
            idempotency_key or _hash_json([event_type, entity_type, entity_id, payload]),
            length=32,
        ),
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        idempotency_key=idempotency_key,
        payload_json=payload,
    )
    session.add(event)
    return event


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PersistenceError(
                    f"Некорректный JSONL {path.name}, строка {line_number}"
                ) from exc
            if not isinstance(row, dict):
                raise PersistenceError(
                    f"В {path.name}, строка {line_number} ожидается объект"
                )
            rows.append(row)
    return rows


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise PersistenceError(f"Некорректный JSON: {path}") from exc


def _bundle_hash(paths) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda value: value.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _stable_id(prefix: str, value: str, *, length: int = 20) -> str:
    return f"{prefix}-{_hash_text(value)[:length]}"


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_json(value: Any) -> str:
    return _hash_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    )


def _required_text(row: dict[str, Any], field: str) -> str:
    value = row.get(field)
    if value is None or not str(value).strip():
        raise PersistenceError(f"Обязательное поле {field!r} отсутствует")
    return str(value).strip()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except InvalidOperation as exc:
        raise PersistenceError(f"Некорректное числовое значение: {value!r}") from exc


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    normalized = value.normalize()
    return format(normalized, "f")
