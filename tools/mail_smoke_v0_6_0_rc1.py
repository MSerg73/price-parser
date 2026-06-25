from __future__ import annotations

import argparse
import io
import json
import shutil
import tempfile
import zipfile
from email.message import EmailMessage
from pathlib import Path

from price_parser import __version__
from price_parser.db.commands import database_status, upgrade_database
from price_parser.db.session import create_engine_and_session
from price_parser.mail import (
    MockSMTPTransport,
    create_mail_request,
    list_mail_attachments,
    list_mail_messages,
    replay_eml,
    send_mail_request,
)


def _xlsx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="price_parser_mail_smoke_"))
    try:
        database = stage / "mail-smoke.db"
        storage = stage / "mail-store"
        url = "sqlite:///" + str(database).replace("\\", "/")
        upgrade_database(url)
        engine, factory = create_engine_and_session(url)
        try:
            with factory() as session:
                request, request_created = create_mail_request(
                    session,
                    request_key="smoke:deal-1:supplier-1",
                    recipient_email="supplier@example.test",
                    sender_email="sales@example.test",
                    subject="Запрос цены",
                    body_text="Просим сообщить цену и срок",
                    deal_external_id="deal-1",
                    actor="MAIL_SMOKE",
                )
                transport = MockSMTPTransport()
                request, sent = send_mail_request(
                    session,
                    request_id=request.id,
                    transport=transport,
                    storage_root=storage,
                    actor="MAIL_SMOKE",
                )

                reply = EmailMessage()
                reply["From"] = "supplier@example.test"
                reply["To"] = "sales@example.test"
                reply["Subject"] = "Re: запрос"
                reply["Message-ID"] = "<mail-smoke-reply@example.test>"
                reply["In-Reply-To"] = request.message_id
                reply["References"] = request.message_id
                reply.set_content("Ответ во вложении")
                reply.add_attachment(
                    _xlsx_bytes(),
                    maintype="application",
                    subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    filename="offer.xlsx",
                )
                message, replay_created = replay_eml(
                    session,
                    reply.as_bytes(),
                    storage_root=storage,
                    actor="MAIL_SMOKE",
                )
                duplicate, duplicate_created = replay_eml(
                    session,
                    reply.as_bytes(),
                    storage_root=storage,
                    actor="MAIL_SMOKE",
                )
                messages = list_mail_messages(session)
                attachments = list_mail_attachments(session)
                status = database_status(url)
        finally:
            engine.dispose()

        payload = {
            "version": __version__,
            "database_revision": status["revision"],
            "request_created": request_created,
            "mock_sent": sent,
            "outbound_messages": 1,
            "inbound_replay_created": replay_created,
            "duplicate_replay_created": duplicate_created,
            "linked_request_id": message.request_id,
            "link_method": message.link_method,
            "processing_status": message.processing_status,
            "message_count": messages["total"],
            "attachment_count": attachments["total"],
            "attachment_validation": attachments["items"][0]["validation_status"],
            "attachment_parser_status": attachments["items"][0]["parser_status"],
            "automatic_application_performed": False,
            "live_imap_e2e": "NOT_VERIFIED",
            "live_smtp_e2e": "NOT_VERIFIED",
            "live_llm_e2e": "NOT_VERIFIED",
            "success": (
                request_created
                and sent
                and replay_created
                and not duplicate_created
                and message.request_id == request.id
                and message.processing_status == "PROCESSED"
                and attachments["items"][0]["parser_status"] == "READY"
                and status["revision"] == "0002"
            ),
        }
        output.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["success"] else 1
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
