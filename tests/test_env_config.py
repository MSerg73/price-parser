
from __future__ import annotations

from pathlib import Path

import pytest

from price_parser.env_config import load_env_file, load_project_env
from price_parser.llm.policy import resolve_openai_api_key


def test_ascii_crlf_env_is_loaded_without_exposing_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_bytes(
        b"OPENAI_API_KEY=test-secret\r\n"
        b"LLM_MODEL=gpt-5.4-mini\r\n"
        b"LLM_API_KEY=\r\n"
    )
    environ: dict[str, str] = {}

    result = load_project_env(env_file, environ=environ)

    assert result.found is True
    assert result.path == env_file.resolve()
    assert set(result.loaded_keys) == {
        "OPENAI_API_KEY",
        "LLM_MODEL",
        "LLM_API_KEY",
    }
    assert environ["OPENAI_API_KEY"] == "test-secret"
    assert environ["LLM_MODEL"] == "gpt-5.4-mini"
    assert resolve_openai_api_key(environ=environ) == "test-secret"


def test_existing_environment_wins_by_default(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=file-key\n", encoding="ascii")
    environ = {"OPENAI_API_KEY": "process-key"}

    loaded = load_env_file(env_file, environ=environ)

    assert loaded == ()
    assert environ["OPENAI_API_KEY"] == "process-key"


def test_malformed_env_line_is_rejected(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY\n", encoding="ascii")

    with pytest.raises(ValueError, match="нет '='"):
        load_env_file(env_file, environ={})
