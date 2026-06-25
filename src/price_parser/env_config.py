from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import MutableMapping

_ALLOWED_KEYS = {"OPENAI_API_KEY", "LLM_API_KEY", "LLM_MODEL"}
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class EnvLoadResult:
    path: Path | None
    loaded_keys: tuple[str, ...]
    found: bool


def load_project_env(
    explicit_path: str | Path | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
    override: bool = False,
) -> EnvLoadResult:
    """Load the local project .env without printing or returning secret values.

    Search order:
    1. explicit_path;
    2. PRICE_PARSER_ENV_FILE;
    3. current working directory/.env;
    4. repository root/.env for an editable/source checkout.

    Existing process variables win unless ``override=True``.
    """
    target_env = os.environ if environ is None else environ
    candidates: list[Path] = []

    if explicit_path is not None:
        candidates.append(Path(explicit_path))
    else:
        configured = str(target_env.get("PRICE_PARSER_ENV_FILE", "") or "").strip()
        if configured:
            candidates.append(Path(configured))
        candidates.append(Path.cwd() / ".env")
        candidates.append(Path(__file__).resolve().parents[2] / ".env")

    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            loaded = load_env_file(resolved, environ=target_env, override=override)
            return EnvLoadResult(path=resolved, loaded_keys=loaded, found=True)

    return EnvLoadResult(path=None, loaded_keys=(), found=False)


def load_env_file(
    path: str | Path,
    *,
    environ: MutableMapping[str, str] | None = None,
    override: bool = False,
) -> tuple[str, ...]:
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(target)

    raw = target.read_bytes()
    text: str
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Не удалось определить кодировку .env: {target}")

    target_env = os.environ if environ is None else environ
    loaded: list[str] = []

    for line_number, source_line in enumerate(text.splitlines(), start=1):
        line = source_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ValueError(f"Некорректная строка .env {line_number}: нет '='")

        key, value = line.split("=", 1)
        key = key.strip()
        if not _KEY_RE.fullmatch(key):
            raise ValueError(f"Некорректное имя переменной .env в строке {line_number}")
        if key not in _ALLOWED_KEYS:
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        if override or not str(target_env.get(key, "") or "").strip():
            target_env[key] = value
            loaded.append(key)

    return tuple(loaded)
