from __future__ import annotations

import os
from pathlib import Path
def _sqlite_url(path: Path) -> str:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + resolved.as_posix()


def default_database_url() -> str:
    configured = os.getenv("DATABASE_URL")
    if configured and configured.strip():
        return configured.strip()

    root = os.getenv("PRICE_PARSER_ROOT")
    if root and root.strip():
        return _sqlite_url(Path(root) / "data" / "price_parser.db")

    windows_root = Path("C:/AI Test")
    if os.name == "nt" and windows_root.exists():
        return _sqlite_url(windows_root / "data" / "price_parser.db")

    return _sqlite_url(Path.cwd() / ".price_parser" / "price_parser.db")


def resolve_database_url(value: str | None = None) -> str:
    return value.strip() if value and value.strip() else default_database_url()
