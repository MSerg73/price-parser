"""mail workflow and attachment storage

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-19 14:10:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mail_requests",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("supplier_id", sa.String(length=40), nullable=True),
        sa.Column("deal_external_id", sa.String(length=128), nullable=True),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        sa.Column("sender_email", sa.String(length=320), nullable=True),
        sa.Column("subject", sa.String(length=998), nullable=False),
        sa.Column("subject_token", sa.String(length=64), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=False),
        sa.Column("body_hash", sa.String(length=64), nullable=False),
        sa.Column("message_id", sa.String(length=998), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["supplier_id"], ["suppliers.id"],
            name=op.f("fk_mail_requests_supplier_id_suppliers"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_requests")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_mail_requests_idempotency_key")),
        sa.UniqueConstraint("message_id", name=op.f("uq_mail_requests_message_id")),
        sa.UniqueConstraint("request_key", name=op.f("uq_mail_requests_request_key")),
        sa.UniqueConstraint("subject_token", name=op.f("uq_mail_requests_subject_token")),
    )
    with op.batch_alter_table("mail_requests") as batch_op:
        batch_op.create_index(
            "ix_mail_requests_status_created", ["status", "created_at"], unique=False
        )

    op.create_table(
        "mail_messages",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("message_id_header", sa.String(length=998), nullable=True),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("in_reply_to", sa.String(length=998), nullable=True),
        sa.Column("references_json", sa.JSON(), nullable=False),
        sa.Column("sender_email", sa.String(length=320), nullable=True),
        sa.Column("recipients_json", sa.JSON(), nullable=False),
        sa.Column("subject", sa.String(length=998), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=True),
        sa.Column("raw_sha256", sa.String(length=64), nullable=False),
        sa.Column("raw_path", sa.String(length=1024), nullable=False),
        sa.Column("link_method", sa.String(length=32), nullable=True),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"], ["mail_requests.id"],
            name=op.f("fk_mail_messages_request_id_mail_requests"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_messages")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_mail_messages_dedupe_key")),
        sa.UniqueConstraint(
            "message_id_header", name=op.f("uq_mail_messages_message_id_header")
        ),
        sa.UniqueConstraint("raw_sha256", name=op.f("uq_mail_messages_raw_sha256")),
    )
    with op.batch_alter_table("mail_messages") as batch_op:
        batch_op.create_index(
            "ix_mail_messages_request_received",
            ["request_id", "received_at"],
            unique=False,
        )
        batch_op.create_index(
            "ix_mail_messages_status", ["processing_status"], unique=False
        )

    op.create_table(
        "mail_attachments",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("message_id", sa.String(length=80), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("safe_filename", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_path", sa.String(length=1024), nullable=False),
        sa.Column("validation_status", sa.String(length=64), nullable=False),
        sa.Column("parser_status", sa.String(length=32), nullable=False),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"], ["mail_messages.id"],
            name=op.f("fk_mail_attachments_message_id_mail_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_attachments")),
        sa.UniqueConstraint(
            "message_id", "sha256", "safe_filename",
            name="uq_mail_attachments_message_hash_name",
        ),
    )
    with op.batch_alter_table("mail_attachments") as batch_op:
        batch_op.create_index(
            "ix_mail_attachments_status",
            ["validation_status", "parser_status"],
            unique=False,
        )

    op.create_table(
        "mail_processing_attempts",
        sa.Column("id", sa.String(length=80), nullable=False),
        sa.Column("message_id", sa.String(length=80), nullable=False),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"], ["mail_messages.id"],
            name=op.f("fk_mail_processing_attempts_message_id_mail_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mail_processing_attempts")),
        sa.UniqueConstraint(
            "message_id", "operation", "attempt_number",
            name="uq_mail_processing_attempt",
        ),
    )


def downgrade() -> None:
    op.drop_table("mail_processing_attempts")
    with op.batch_alter_table("mail_attachments") as batch_op:
        batch_op.drop_index("ix_mail_attachments_status")
    op.drop_table("mail_attachments")
    with op.batch_alter_table("mail_messages") as batch_op:
        batch_op.drop_index("ix_mail_messages_status")
        batch_op.drop_index("ix_mail_messages_request_received")
    op.drop_table("mail_messages")
    with op.batch_alter_table("mail_requests") as batch_op:
        batch_op.drop_index("ix_mail_requests_status_created")
    op.drop_table("mail_requests")
