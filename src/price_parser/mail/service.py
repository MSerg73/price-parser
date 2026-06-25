from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage, Message
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from price_parser.db.models import (
    AuditEvent,
    MailAttachment,
    MailMessage,
    MailProcessingAttempt,
    MailRequest,
    utc_now,
)
from .transports import MailTransport

REQUEST_TOKEN_RE = re.compile(r"\[PPREQ:([A-Z0-9_-]{6,64})\]", re.IGNORECASE)
ALLOWED_EXTENSIONS = frozenset({".xls", ".xlsx", ".doc", ".docx", ".csv", ".pdf"})
READY_FOR_PARSER = frozenset({".xls", ".xlsx", ".doc", ".docx", ".csv"})
DEFAULT_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024
OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


class MailProcessingError(ValueError):
    pass


def create_mail_request(
    session: Session,
    *,
    request_key: str,
    recipient_email: str,
    subject: str,
    body_text: str,
    sender_email: str | None = None,
    supplier_id: str | None = None,
    deal_external_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    actor: str = "system",
) -> tuple[MailRequest, bool]:
    request_key = _required(request_key, "request_key", max_length=128)
    recipient_email = _email(recipient_email, "recipient_email")
    sender_email = _email(sender_email, "sender_email") if sender_email else None
    subject = _required(subject, "subject", max_length=900)
    body_text = _required(body_text, "body_text", max_length=2_000_000)

    payload = {
        "request_key": request_key,
        "recipient_email": recipient_email.lower(),
        "sender_email": None if sender_email is None else sender_email.lower(),
        "subject": subject,
        "body_text": body_text,
        "supplier_id": supplier_id,
        "deal_external_id": deal_external_id,
    }
    idem = _hash_json(payload)
    existing = session.scalar(
        select(MailRequest).where(
            or_(
                MailRequest.request_key == request_key,
                MailRequest.idempotency_key == idem,
            )
        )
    )
    if existing is not None:
        if existing.idempotency_key != idem:
            raise MailProcessingError(
                "request_key уже существует с другим содержимым"
            )
        return existing, False

    identifier = f"mailreq-{idem[:24]}"
    token = f"PP-{idem[:16].upper()}"
    request = MailRequest(
        id=identifier,
        request_key=request_key,
        idempotency_key=idem,
        supplier_id=supplier_id,
        deal_external_id=deal_external_id,
        recipient_email=recipient_email,
        sender_email=sender_email,
        subject=subject,
        subject_token=token,
        body_text=body_text,
        body_hash=_hash_bytes(body_text.encode("utf-8")),
        message_id=f"<{identifier}@price-parser.local>",
        status="DRAFT",
        metadata_json=metadata or {},
    )
    session.add(request)
    _append_audit(
        session,
        event_type="MAIL_REQUEST_CREATED",
        entity_type="mail_request",
        entity_id=request.id,
        actor=actor,
        idempotency_key=f"mail-request:{idem}",
        payload={"request_key": request_key, "recipient": recipient_email},
    )
    session.commit()
    session.refresh(request)
    return request, True


def get_mail_request(session: Session, request_id: str) -> MailRequest | None:
    return session.get(MailRequest, request_id)


def send_mail_request(
    session: Session,
    *,
    request_id: str,
    transport: MailTransport,
    storage_root: str | Path,
    actor: str = "system",
    confirm_live_mail: bool = False,
    max_retries: int = 3,
) -> tuple[MailRequest, bool]:
    request = session.get(MailRequest, request_id)
    if request is None:
        raise MailProcessingError(f"Mail request not found: {request_id}")
    if request.status == "SENT":
        return request, False
    if getattr(transport, "is_live", True) and not confirm_live_mail:
        raise MailProcessingError(
            "Для реальной SMTP-отправки обязателен confirm_live_mail"
        )

    message = build_outbound_message(request)
    try:
        transport.send(message)
        raw = message.as_bytes(policy=policy.SMTP)
        raw_path = _store_raw(raw, storage_root, direction="outbound")
        raw_hash = _hash_bytes(raw)
        dedupe_key = f"mid:{_normalize_message_id(request.message_id)}"
        existing = session.scalar(
            select(MailMessage).where(MailMessage.dedupe_key == dedupe_key)
        )
        if existing is None:
            session.add(
                MailMessage(
                    id=f"mailmsg-{raw_hash[:24]}",
                    request_id=request.id,
                    direction="OUTBOUND",
                    message_id_header=_normalize_message_id(request.message_id),
                    dedupe_key=dedupe_key,
                    sender_email=request.sender_email,
                    recipients_json=[request.recipient_email],
                    subject=str(message["Subject"]),
                    text_body=request.body_text,
                    raw_sha256=raw_hash,
                    raw_path=str(raw_path),
                    link_method="REQUEST",
                    processing_status="SENT",
                    processed_at=utc_now(),
                    metadata_json={"transport": type(transport).__name__},
                )
            )
        request.status = "SENT"
        request.sent_at = utc_now()
        request.last_error = None
        _append_audit(
            session,
            event_type="MAIL_REQUEST_SENT",
            entity_type="mail_request",
            entity_id=request.id,
            actor=actor,
            idempotency_key=f"mail-sent:{request.id}",
            payload={
                "message_id": request.message_id,
                "transport": type(transport).__name__,
                "live": bool(getattr(transport, "is_live", True)),
            },
        )
        session.commit()
        session.refresh(request)
        return request, True
    except Exception as exc:
        request.retry_count += 1
        request.last_error = str(exc)
        request.status = "DEAD_LETTER" if request.retry_count >= max_retries else "RETRY"
        _append_audit(
            session,
            event_type="MAIL_REQUEST_SEND_FAILED",
            entity_type="mail_request",
            entity_id=request.id,
            actor=actor,
            idempotency_key=None,
            payload={"attempt": request.retry_count, "error": str(exc)},
        )
        session.commit()
        raise MailProcessingError(str(exc)) from exc


def build_outbound_message(request: MailRequest) -> EmailMessage:
    if not request.sender_email:
        raise MailProcessingError("Для отправки не задан sender_email")
    message = EmailMessage()
    message["From"] = request.sender_email
    message["To"] = request.recipient_email
    message["Subject"] = f"[PPREQ:{request.subject_token}] {request.subject}"
    message["Message-ID"] = request.message_id
    message["X-Price-Parser-Request-ID"] = request.id
    message.set_content(request.body_text)
    return message


def replay_eml(
    session: Session,
    eml: str | Path | bytes,
    *,
    storage_root: str | Path,
    actor: str = "replay",
    max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
) -> tuple[MailMessage, bool]:
    raw, source_name = _read_eml(eml)
    raw_hash = _hash_bytes(raw)
    try:
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as exc:
        raise MailProcessingError(f"Повреждённое MIME-сообщение: {exc}") from exc

    message_id = _normalize_message_id(parsed.get("Message-ID"))
    dedupe_key = f"mid:{message_id}" if message_id else f"raw:{raw_hash}"
    existing = session.scalar(
        select(MailMessage).where(
            or_(
                MailMessage.dedupe_key == dedupe_key,
                MailMessage.raw_sha256 == raw_hash,
            )
        )
    )
    if existing is not None:
        return existing, False

    request, link_method = _link_request(session, parsed)
    text_body, html_body = _extract_bodies(parsed)
    raw_path = _store_raw(raw, storage_root, direction="inbound")
    sender = parseaddr(str(parsed.get("From", "")))[1] or None
    recipients = [
        addr for _name, addr in getaddresses(
            [str(parsed.get(name, "")) for name in ("To", "Cc")]
        ) if addr
    ]
    references = _message_id_list(parsed.get("References"))
    in_reply_to = _normalize_message_id(parsed.get("In-Reply-To"))
    received_at = _message_date(parsed) or utc_now()
    message = MailMessage(
        id=f"mailmsg-{raw_hash[:24]}",
        request_id=None if request is None else request.id,
        direction="INBOUND",
        message_id_header=message_id,
        dedupe_key=dedupe_key,
        in_reply_to=in_reply_to,
        references_json=references,
        sender_email=sender,
        recipients_json=recipients,
        subject=str(parsed.get("Subject", "")),
        text_body=text_body,
        html_body=html_body,
        raw_sha256=raw_hash,
        raw_path=str(raw_path),
        link_method=link_method,
        processing_status="PROCESSING",
        received_at=received_at,
        metadata_json={"source_name": source_name},
    )
    session.add(message)
    session.flush()

    attempt = MailProcessingAttempt(
        id=f"mailattempt-{raw_hash[:20]}-1",
        message_id=message.id,
        operation="MIME_REPLAY",
        attempt_number=1,
        status="STARTED",
    )
    session.add(attempt)

    rejected = 0
    attachment_count = 0
    try:
        for part in parsed.iter_attachments():
            payload = part.get_payload(decode=True)
            if payload is None:
                payload = b""
            original_name = part.get_filename() or f"attachment-{attachment_count + 1}.bin"
            attachment = _store_attachment(
                message_id=message.id,
                payload=payload,
                original_name=original_name,
                mime_type=part.get_content_type() or "application/octet-stream",
                storage_root=storage_root,
                max_attachment_bytes=max_attachment_bytes,
            )
            attachment_count += 1
            if attachment.validation_status != "ACCEPTED":
                rejected += 1
            session.add(attachment)
        message.processing_status = "REVIEW" if rejected else "PROCESSED"
        message.processed_at = utc_now()
        attempt.status = "COMPLETED"
        _append_audit(
            session,
            event_type="MAIL_MESSAGE_REPLAYED",
            entity_type="mail_message",
            entity_id=message.id,
            actor=actor,
            idempotency_key=f"mail-replay:{dedupe_key}",
            payload={
                "request_id": message.request_id,
                "link_method": link_method,
                "attachments": attachment_count,
                "rejected_attachments": rejected,
                "automatic_application_performed": False,
            },
        )
        session.commit()
        session.refresh(message)
        return message, True
    except Exception as exc:
        message.processing_status = "DEAD_LETTER"
        message.last_error = str(exc)
        message.retry_count += 1
        attempt.status = "FAILED"
        attempt.error_text = str(exc)
        session.commit()
        raise MailProcessingError(str(exc)) from exc


def list_mail_messages(
    session: Session,
    *,
    status: str | None = None,
    request_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    stmt = select(MailMessage)
    count_stmt = select(func.count(MailMessage.id))
    if status:
        stmt = stmt.where(MailMessage.processing_status == status)
        count_stmt = count_stmt.where(MailMessage.processing_status == status)
    if request_id:
        stmt = stmt.where(MailMessage.request_id == request_id)
        count_stmt = count_stmt.where(MailMessage.request_id == request_id)
    rows = session.scalars(
        stmt.order_by(MailMessage.received_at.desc()).offset(offset).limit(limit)
    ).all()
    return {
        "total": int(session.scalar(count_stmt) or 0),
        "items": [mail_message_to_dict(row) for row in rows],
    }


def list_mail_attachments(
    session: Session,
    *,
    validation_status: str | None = None,
    parser_status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    stmt = select(MailAttachment)
    count_stmt = select(func.count(MailAttachment.id))
    if validation_status:
        stmt = stmt.where(MailAttachment.validation_status == validation_status)
        count_stmt = count_stmt.where(
            MailAttachment.validation_status == validation_status
        )
    if parser_status:
        stmt = stmt.where(MailAttachment.parser_status == parser_status)
        count_stmt = count_stmt.where(MailAttachment.parser_status == parser_status)
    rows = session.scalars(
        stmt.order_by(MailAttachment.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return {
        "total": int(session.scalar(count_stmt) or 0),
        "items": [mail_attachment_to_dict(row) for row in rows],
    }


def mail_request_to_dict(item: MailRequest) -> dict[str, Any]:
    return {
        "id": item.id,
        "request_key": item.request_key,
        "recipient_email": item.recipient_email,
        "sender_email": item.sender_email,
        "deal_external_id": item.deal_external_id,
        "subject": item.subject,
        "subject_token": item.subject_token,
        "message_id": item.message_id,
        "status": item.status,
        "retry_count": item.retry_count,
        "last_error": item.last_error,
        "created_at": item.created_at.isoformat(),
        "sent_at": None if item.sent_at is None else item.sent_at.isoformat(),
    }


def mail_message_to_dict(item: MailMessage) -> dict[str, Any]:
    return {
        "id": item.id,
        "request_id": item.request_id,
        "direction": item.direction,
        "message_id": item.message_id_header,
        "in_reply_to": item.in_reply_to,
        "references": item.references_json,
        "sender_email": item.sender_email,
        "recipients": item.recipients_json,
        "subject": item.subject,
        "raw_sha256": item.raw_sha256,
        "raw_path": item.raw_path,
        "link_method": item.link_method,
        "processing_status": item.processing_status,
        "retry_count": item.retry_count,
        "last_error": item.last_error,
        "received_at": item.received_at.isoformat(),
        "processed_at": None if item.processed_at is None else item.processed_at.isoformat(),
        "attachments": [mail_attachment_to_dict(row) for row in item.attachments],
        "automatic_application_performed": False,
    }


def mail_attachment_to_dict(item: MailAttachment) -> dict[str, Any]:
    return {
        "id": item.id,
        "message_id": item.message_id,
        "original_filename": item.original_filename,
        "safe_filename": item.safe_filename,
        "mime_type": item.mime_type,
        "size_bytes": item.size_bytes,
        "sha256": item.sha256,
        "storage_path": item.storage_path,
        "validation_status": item.validation_status,
        "parser_status": item.parser_status,
        "error_text": item.error_text,
    }


def _link_request(
    session: Session, message: Message
) -> tuple[MailRequest | None, str | None]:
    in_reply_to = _normalize_message_id(message.get("In-Reply-To"))
    if in_reply_to:
        request = session.scalar(
            select(MailRequest).where(MailRequest.message_id == in_reply_to)
        )
        if request is not None:
            return request, "IN_REPLY_TO"

    for reference in reversed(_message_id_list(message.get("References"))):
        request = session.scalar(
            select(MailRequest).where(MailRequest.message_id == reference)
        )
        if request is not None:
            return request, "REFERENCES"

    subject = str(message.get("Subject", ""))
    match = REQUEST_TOKEN_RE.search(subject)
    if match:
        request = session.scalar(
            select(MailRequest).where(
                MailRequest.subject_token == match.group(1).upper()
            )
        )
        if request is not None:
            return request, "REQUEST_TOKEN"
    return None, None


def _store_attachment(
    *,
    message_id: str,
    payload: bytes,
    original_name: str,
    mime_type: str,
    storage_root: str | Path,
    max_attachment_bytes: int,
) -> MailAttachment:
    safe_name = _safe_filename(original_name)
    extension = Path(safe_name).suffix.lower()
    digest = _hash_bytes(payload)
    status, error = _validate_attachment(
        payload,
        extension=extension,
        mime_type=mime_type,
        max_attachment_bytes=max_attachment_bytes,
    )
    parser_status = (
        "READY" if status == "ACCEPTED" and extension in READY_FOR_PARSER else "BLOCKED"
    )
    folder = "accepted" if status == "ACCEPTED" else "quarantine"
    path = (
        Path(storage_root).expanduser().resolve()
        / "attachments"
        / folder
        / digest[:2]
        / f"{digest[:16]}_{safe_name}"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(payload)
    return MailAttachment(
        id=f"mailatt-{_hash_bytes((message_id + digest + safe_name).encode())[:32]}",
        message_id=message_id,
        original_filename=original_name[:512],
        safe_filename=safe_name,
        mime_type=mime_type[:255],
        size_bytes=len(payload),
        sha256=digest,
        storage_path=str(path),
        validation_status=status,
        parser_status=parser_status,
        error_text=error,
    )


def _validate_attachment(
    payload: bytes,
    *,
    extension: str,
    mime_type: str,
    max_attachment_bytes: int,
) -> tuple[str, str | None]:
    if len(payload) > max_attachment_bytes:
        return "REJECTED_SIZE", f"Attachment exceeds {max_attachment_bytes} bytes"
    if extension not in ALLOWED_EXTENSIONS:
        return "REJECTED_EXTENSION", f"Unsupported extension: {extension or '<none>'}"
    if extension in {".xlsx", ".docx"}:
        if not payload.startswith(b"PK"):
            return "REJECTED_SIGNATURE", "ZIP-based Office signature not found"
        marker = b"xl/" if extension == ".xlsx" else b"word/"
        if marker not in payload and b"[Content_Types].xml" not in payload:
            return "REJECTED_SIGNATURE", "Office container marker not found"
    elif extension in {".xls", ".doc"} and not payload.startswith(OLE_MAGIC):
        return "REJECTED_SIGNATURE", "OLE signature not found"
    elif extension == ".pdf" and not payload.startswith(b"%PDF-"):
        return "REJECTED_SIGNATURE", "PDF signature not found"
    elif extension == ".csv":
        try:
            payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                payload.decode("cp1251")
            except UnicodeDecodeError:
                return "REJECTED_SIGNATURE", "CSV is not valid UTF-8 or CP1251 text"
    return "ACCEPTED", None


def _extract_bodies(message: Message) -> tuple[str, str | None]:
    text_parts: list[str] = []
    html_parts: list[str] = []
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_disposition() == "attachment":
                continue
            content_type = part.get_content_type()
            if content_type not in {"text/plain", "text/html"}:
                continue
            try:
                content = part.get_content()
            except Exception:
                payload = part.get_payload(decode=True) or b""
                content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            if content_type == "text/plain":
                text_parts.append(str(content))
            else:
                html_parts.append(str(content))
    else:
        try:
            content = message.get_content()
        except Exception:
            payload = message.get_payload(decode=True) or b""
            content = payload.decode(message.get_content_charset() or "utf-8", errors="replace")
        if message.get_content_type() == "text/html":
            html_parts.append(str(content))
        else:
            text_parts.append(str(content))
    return "\n".join(text_parts).strip(), ("\n".join(html_parts).strip() or None)


def _read_eml(eml: str | Path | bytes) -> tuple[bytes, str]:
    if isinstance(eml, bytes):
        return eml, "<bytes>"
    path = Path(eml).expanduser().resolve()
    if not path.is_file():
        raise MailProcessingError(f"EML file not found: {path}")
    return path.read_bytes(), path.name


def _store_raw(raw: bytes, storage_root: str | Path, *, direction: str) -> Path:
    digest = _hash_bytes(raw)
    path = (
        Path(storage_root).expanduser().resolve()
        / "raw"
        / direction
        / digest[:2]
        / f"{digest}.eml"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(raw)
    return path


def _safe_filename(value: str) -> str:
    name = Path(value.replace("\\", "/")).name
    name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]+", "_", name).strip(" .")
    if not name:
        return "attachment.bin"
    if len(name) > 240:
        suffix = Path(name).suffix[:20]
        name = name[: 240 - len(suffix)] + suffix
    return name


def _message_id_list(value: Any) -> list[str]:
    if not value:
        return []
    return [
        normalized
        for token in re.findall(r"<[^>]+>", str(value))
        if (normalized := _normalize_message_id(token))
    ]


def _normalize_message_id(value: Any) -> str | None:
    if not value:
        return None
    text = " ".join(str(value).split()).strip()
    if not text:
        return None
    if not text.startswith("<"):
        text = f"<{text.strip('<>')}>"
    return text.lower()


def _message_date(message: Message) -> datetime | None:
    value = message.get("Date")
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError, OverflowError):
        return None


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
    seed = idempotency_key or (
        f"{event_type}:{entity_id}:{datetime.now(timezone.utc).isoformat()}"
    )
    event = AuditEvent(
        id=f"audit-{_hash_bytes(seed.encode())[:24]}",
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        actor=actor,
        idempotency_key=idempotency_key,
        payload_json=payload,
    )
    session.add(event)
    return event


def _email(value: str, field: str) -> str:
    text = _required(value, field, max_length=320)
    parsed = parseaddr(text)[1]
    if parsed != text or "@" not in parsed or parsed.startswith("@") or parsed.endswith("@"):
        raise MailProcessingError(f"Invalid {field}")
    return parsed


def _required(value: str, field: str, *, max_length: int) -> str:
    text = (value or "").strip()
    if not text:
        raise MailProcessingError(f"{field} is required")
    if len(text) > max_length:
        raise MailProcessingError(f"{field} exceeds {max_length} characters")
    return text


def _hash_json(value: Any) -> str:
    return _hash_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
