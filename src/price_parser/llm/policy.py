from __future__ import annotations

import os
from typing import Mapping, Any

LLM_POLICY_VERSION = "1.0"
DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_LIVE_BATCH_SIZE = 1
MAX_LIVE_BATCH_SIZE = 10
MAX_PILOT_CASES = 25

RETRYABLE_STATUS_CODES = {408, 409, 429}


def resolve_openai_api_key(
    explicit: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the API key without silently accepting conflicting variables."""
    if explicit is not None:
        value = explicit.strip()
        if not value:
            raise RuntimeError("Передан пустой API-ключ")
        return value

    env = os.environ if environ is None else environ
    canonical = str(env.get("OPENAI_API_KEY", "") or "").strip()
    legacy = str(env.get("LLM_API_KEY", "") or "").strip()

    if canonical and legacy and canonical != legacy:
        raise RuntimeError(
            "OPENAI_API_KEY и LLM_API_KEY заданы одновременно и различаются. "
            "Оставьте один проверенный ключ; приоритет не выбирается молча."
        )

    resolved = canonical or legacy
    if not resolved:
        raise RuntimeError("Не задан OPENAI_API_KEY или LLM_API_KEY")
    return resolved


def is_retryable_openai_error(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status in RETRYABLE_STATUS_CODES or status >= 500

    name = type(exc).__name__.lower()
    return "timeout" in name or "connection" in name


def format_openai_error(exc: Exception) -> str:
    """Return safe diagnostics without request payload or secrets."""
    parts: list[str] = [type(exc).__name__]
    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    code = getattr(exc, "code", None)
    param = getattr(exc, "param", None)

    if status is not None:
        parts.append(f"status={status}")
    if code:
        parts.append(f"code={code}")
    if param:
        parts.append(f"param={param}")
    if request_id:
        parts.append(f"request_id={request_id}")

    body = getattr(exc, "body", None)
    message: Any = None
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict):
            message = error.get("message")
    if not message:
        message = str(exc)
    if message:
        parts.append(f"message={message}")
    return "; ".join(parts)


def validate_live_batch_size(value: int) -> int:
    if value < 1 or value > MAX_LIVE_BATCH_SIZE:
        raise ValueError(
            f"batch_size должен быть от 1 до {MAX_LIVE_BATCH_SIZE}; "
            f"проверенное значение по умолчанию — {DEFAULT_LIVE_BATCH_SIZE}"
        )
    return value
