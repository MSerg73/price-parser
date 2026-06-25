from __future__ import annotations

import io
import zipfile
from email.message import EmailMessage
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from price_parser.api import create_app
from price_parser.db.commands import database_status, upgrade_database
from price_parser.db.session import create_engine_and_session
from price_parser.mail import (
    MailProcessingError,
    MockSMTPTransport,
    create_mail_request,
    list_mail_attachments,
    list_mail_messages,
    replay_eml,
    send_mail_request,
)


def _db_url(tmp_path: Path) -> str:
    return "sqlite:///" + str((tmp_path / "mail.db").resolve()).replace("\\", "/")


def _xlsx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return buffer.getvalue()


def _reply_bytes(
    *,
    in_reply_to: str | None = None,
    references: str | None = None,
    subject: str = "Ответ поставщика",
    message_id: str | None = "<reply-1@example.test>",
    attachment_name: str = "../offer.xlsx",
    attachment_payload: bytes | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = "supplier@example.test"
    message["To"] = "sales@example.test"
    message["Subject"] = subject
    if message_id:
        message["Message-ID"] = message_id
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
    if references:
        message["References"] = references
    message.set_content("Цена и срок во вложении")
    if attachment_payload is not None:
        message.add_attachment(
            attachment_payload,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=attachment_name,
        )
    return message.as_bytes()


def test_mail_request_send_and_replay_are_idempotent(tmp_path: Path) -> None:
    url = _db_url(tmp_path)
    upgrade_database(url)
    engine, factory = create_engine_and_session(url)
    try:
        with factory() as session:
            request, created = create_mail_request(
                session,
                request_key="deal-42:supplier-1",
                recipient_email="supplier@example.test",
                sender_email="sales@example.test",
                subject="Запрос цены",
                body_text="Просим сообщить цену",
                deal_external_id="42",
                actor="test",
            )
            assert created is True
            again, created = create_mail_request(
                session,
                request_key="deal-42:supplier-1",
                recipient_email="supplier@example.test",
                sender_email="sales@example.test",
                subject="Запрос цены",
                body_text="Просим сообщить цену",
                deal_external_id="42",
                actor="test",
            )
            assert created is False
            assert again.id == request.id

            transport = MockSMTPTransport()
            sent, changed = send_mail_request(
                session,
                request_id=request.id,
                transport=transport,
                storage_root=tmp_path / "mail-store",
                actor="test",
            )
            assert changed is True
            assert sent.status == "SENT"
            sent_again, changed = send_mail_request(
                session,
                request_id=request.id,
                transport=transport,
                storage_root=tmp_path / "mail-store",
                actor="test",
            )
            assert changed is False
            assert len(transport.messages) == 1

            raw = _reply_bytes(
                in_reply_to=request.message_id,
                references=request.message_id,
                attachment_payload=_xlsx_bytes(),
            )
            received, created = replay_eml(
                session,
                raw,
                storage_root=tmp_path / "mail-store",
                actor="test",
            )
            assert created is True
            assert received.request_id == request.id
            assert received.link_method == "IN_REPLY_TO"
            assert received.processing_status == "PROCESSED"
            assert len(received.attachments) == 1
            attachment = received.attachments[0]
            assert attachment.safe_filename == "offer.xlsx"
            assert attachment.validation_status == "ACCEPTED"
            assert attachment.parser_status == "READY"
            assert Path(attachment.storage_path).is_file()

            duplicate, created = replay_eml(
                session,
                raw,
                storage_root=tmp_path / "mail-store",
                actor="test",
            )
            assert created is False
            assert duplicate.id == received.id

            second_raw = _reply_bytes(
                in_reply_to=request.message_id,
                message_id="<reply-2@example.test>",
                attachment_payload=_xlsx_bytes(),
            )
            second, created = replay_eml(
                session,
                second_raw,
                storage_root=tmp_path / "mail-store",
                actor="test",
            )
            assert created is True
            assert second.attachments[0].id != attachment.id

            messages = list_mail_messages(session)
            assert messages["total"] == 3
            attachments = list_mail_attachments(session, parser_status="READY")
            assert attachments["total"] == 2
    finally:
        engine.dispose()


def test_mail_replay_token_fallback_and_quarantine(tmp_path: Path) -> None:
    url = _db_url(tmp_path)
    upgrade_database(url)
    engine, factory = create_engine_and_session(url)
    try:
        with factory() as session:
            request, _ = create_mail_request(
                session,
                request_key="deal-77:supplier-2",
                recipient_email="supplier@example.test",
                sender_email="sales@example.test",
                subject="Запрос",
                body_text="Тест",
            )
            raw = _reply_bytes(
                subject=f"[PPREQ:{request.subject_token}] изменённая тема",
                message_id=None,
                attachment_name="payload.exe",
                attachment_payload=b"MZ-not-executable",
            )
            item, created = replay_eml(
                session,
                raw,
                storage_root=tmp_path / "mail-store",
            )
            assert created is True
            assert item.request_id == request.id
            assert item.link_method == "REQUEST_TOKEN"
            assert item.processing_status == "REVIEW"
            assert item.attachments[0].validation_status == "REJECTED_EXTENSION"
            assert item.attachments[0].parser_status == "BLOCKED"
            assert "quarantine" in item.attachments[0].storage_path

            duplicate, created = replay_eml(
                session,
                raw,
                storage_root=tmp_path / "mail-store",
            )
            assert created is False
            assert duplicate.raw_sha256 == item.raw_sha256
    finally:
        engine.dispose()


def test_mail_request_conflict_and_retry_dead_letter(tmp_path: Path) -> None:
    url = _db_url(tmp_path)
    upgrade_database(url)
    engine, factory = create_engine_and_session(url)
    try:
        with factory() as session:
            request, _ = create_mail_request(
                session,
                request_key="same-key",
                recipient_email="supplier@example.test",
                sender_email="sales@example.test",
                subject="A",
                body_text="A",
            )
            with pytest.raises(MailProcessingError):
                create_mail_request(
                    session,
                    request_key="same-key",
                    recipient_email="supplier@example.test",
                    sender_email="sales@example.test",
                    subject="B",
                    body_text="B",
                )

            transport = MockSMTPTransport(fail=True)
            for attempt in range(3):
                with pytest.raises(MailProcessingError):
                    send_mail_request(
                        session,
                        request_id=request.id,
                        transport=transport,
                        storage_root=tmp_path / "mail-store",
                        max_retries=3,
                    )
            session.refresh(request)
            assert request.retry_count == 3
            assert request.status == "DEAD_LETTER"
    finally:
        engine.dispose()


def test_mail_api_and_migration(tmp_path: Path) -> None:
    url = _db_url(tmp_path)
    app = create_app(url, auto_migrate=True, api_token="secret")
    client = TestClient(app)
    headers = {"X-API-Key": "secret"}

    health = client.get("/health", headers=headers)
    assert health.status_code == 200
    assert health.json()["live_mail_e2e"] == "NOT_VERIFIED"
    assert database_status(url)["revision"] == "0003_commercial_documents"

    created = client.post(
        "/mail/requests",
        headers=headers,
        json={
            "request_key": "api-request-1",
            "recipient_email": "supplier@example.test",
            "sender_email": "sales@example.test",
            "subject": "Запрос",
            "body_text": "Тест",
            "deal_external_id": "100",
        },
    )
    assert created.status_code == 200
    request_id = created.json()["request"]["id"]

    eml = tmp_path / "reply.eml"
    eml.write_bytes(
        _reply_bytes(
            in_reply_to=created.json()["request"]["message_id"],
            attachment_payload=_xlsx_bytes(),
        )
    )
    replayed = client.post(
        "/mail/replay",
        headers=headers,
        json={
            "eml_path": str(eml),
            "storage_root": str(tmp_path / "mail-store"),
        },
    )
    assert replayed.status_code == 200
    assert replayed.json()["message"]["request_id"] == request_id

    messages = client.get("/mail/messages", headers=headers)
    assert messages.status_code == 200
    assert messages.json()["total"] == 1

    attachments = client.get(
        "/mail/attachments",
        headers=headers,
        params={"parser_status": "READY"},
    )
    assert attachments.status_code == 200
    assert attachments.json()["total"] == 1
