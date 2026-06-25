from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class PilotImportRequest(BaseModel):
    report_dir: str = Field(min_length=1)
    actor: str = Field(default="api", min_length=1, max_length=255)
    idempotency_key: str | None = Field(default=None, max_length=128)


class ReviewDecisionRequest(BaseModel):
    action: str
    operator: str = Field(min_length=1, max_length=255)
    comment: str = Field(min_length=1)
    changes: dict[str, Any] = Field(default_factory=dict)
    rule_id: str | None = None
    rule_version: str | None = None

    @field_validator("action")
    @classmethod
    def normalize_action(cls, value: str) -> str:
        return value.strip().upper()


class ReviewApplyRequest(BaseModel):
    applied_by: str = Field(min_length=1, max_length=255)


class LLMRunRequest(BaseModel):
    fingerprint: str = Field(min_length=1, max_length=64)
    provider: str = Field(min_length=1, max_length=64)
    status: str = Field(min_length=1, max_length=32)
    model: str | None = Field(default=None, max_length=255)
    input_count: int = Field(default=0, ge=0)
    output_count: int = Field(default=0, ge=0)
    request_hash: str | None = Field(default=None, max_length=64)
    response_hash: str | None = Field(default=None, max_length=64)
    live_model_verified: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    error_text: str | None = None

class MailRequestCreate(BaseModel):
    request_key: str = Field(min_length=1, max_length=128)
    recipient_email: str = Field(min_length=3, max_length=320)
    sender_email: str | None = Field(default=None, max_length=320)
    subject: str = Field(min_length=1, max_length=900)
    body_text: str = Field(min_length=1, max_length=2_000_000)
    supplier_id: str | None = Field(default=None, max_length=40)
    deal_external_id: str | None = Field(default=None, max_length=128)
    actor: str = Field(default="api", min_length=1, max_length=255)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MailReplayRequest(BaseModel):
    eml_path: str = Field(min_length=1, max_length=2048)
    storage_root: str = Field(min_length=1, max_length=2048)
    actor: str = Field(default="api", min_length=1, max_length=255)
    max_attachment_bytes: int = Field(default=25 * 1024 * 1024, ge=1)



class CommercialDocumentCreate(BaseModel):
    document_key: str = Field(min_length=1, max_length=128)
    document_type: str = Field(min_length=1, max_length=32)
    payload: dict[str, Any]
    created_by: str = Field(min_length=1, max_length=255)
    deal_external_id: str | None = Field(default=None, max_length=128)
    template_version: str = Field(default="test-v1", min_length=1, max_length=64)


class CommercialDocumentRender(BaseModel):
    output_dir: str = Field(min_length=1, max_length=2048)
    actor: str = Field(min_length=1, max_length=255)


class BudgetMockUpdate(BaseModel):
    deal_external_id: str = Field(min_length=1, max_length=128)
    actor: str = Field(min_length=1, max_length=255)
