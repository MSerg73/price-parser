"""commercial documents and budget updates

Revision ID: 0003_commercial_documents
Revises: 0002
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_commercial_documents"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commercial_documents",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("document_key", sa.String(length=128), nullable=False),
        sa.Column("document_type", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("deal_external_id", sa.String(length=128)),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("template_version", sa.String(length=64), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("subtotal", sa.Numeric(18, 2), nullable=False),
        sa.Column("discount_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("delivery_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("vat_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("grand_total", sa.Numeric(18, 2), nullable=False),
        sa.Column("calculation_json", sa.JSON(), nullable=False),
        sa.Column("pdf_path", sa.String(length=2048)),
        sa.Column("pdf_sha256", sa.String(length=64)),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("document_key", "version", name="uq_commercial_documents_key_version"),
    )
    op.create_index(
        "ix_commercial_documents_status_created",
        "commercial_documents",
        ["status", "created_at"],
    )
    op.create_table(
        "budget_updates",
        sa.Column("id", sa.String(length=80), primary_key=True),
        sa.Column("document_id", sa.String(length=80), sa.ForeignKey("commercial_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deal_external_id", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("error_text", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_budget_updates_deal_created",
        "budget_updates",
        ["deal_external_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_budget_updates_deal_created", table_name="budget_updates")
    op.drop_table("budget_updates")
    op.drop_index("ix_commercial_documents_status_created", table_name="commercial_documents")
    op.drop_table("commercial_documents")
