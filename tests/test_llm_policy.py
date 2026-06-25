import pytest

from price_parser.llm.policy import (
    DEFAULT_LIVE_BATCH_SIZE,
    format_openai_error,
    is_retryable_openai_error,
    resolve_openai_api_key,
    validate_live_batch_size,
)


class ApiError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__("failure")
        self.status_code = status_code
        self.request_id = "req_test"
        self.code = "bad_request" if status_code == 400 else "rate_limit"
        self.param = "text.format" if status_code == 400 else None
        self.body = {"error": {"message": "safe diagnostic"}}


def test_api_key_prefers_single_canonical_variable() -> None:
    assert resolve_openai_api_key(
        environ={"OPENAI_API_KEY": "verified-key"}
    ) == "verified-key"


def test_api_key_rejects_conflicting_variables() -> None:
    with pytest.raises(RuntimeError, match="различаются"):
        resolve_openai_api_key(
            environ={
                "OPENAI_API_KEY": "verified-key",
                "LLM_API_KEY": "stale-key",
            }
        )


def test_non_retryable_400_and_retryable_429() -> None:
    assert is_retryable_openai_error(ApiError(400)) is False
    assert is_retryable_openai_error(ApiError(429)) is True


def test_safe_error_diagnostics_include_request_id_without_payload() -> None:
    message = format_openai_error(ApiError(400))
    assert "status=400" in message
    assert "request_id=req_test" in message
    assert "safe diagnostic" in message


def test_live_batch_size_policy() -> None:
    assert DEFAULT_LIVE_BATCH_SIZE == 1
    assert validate_live_batch_size(1) == 1
    with pytest.raises(ValueError):
        validate_live_batch_size(0)
    with pytest.raises(ValueError):
        validate_live_batch_size(11)
