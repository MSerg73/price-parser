from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from .config import resolve_database_url
from .session import create_engine_and_session


def _alembic_config(database_url: str | None = None) -> Config:
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(__file__).resolve().parent / "migrations"),
    )
    config.set_main_option("sqlalchemy.url", resolve_database_url(database_url))
    return config


def upgrade_database(database_url: str | None = None, revision: str = "head") -> str:
    url = resolve_database_url(database_url)
    command.upgrade(_alembic_config(url), revision)
    return url


def database_status(database_url: str | None = None) -> dict[str, object]:
    url = resolve_database_url(database_url)
    engine, _ = create_engine_and_session(url)
    try:
        inspector = inspect(engine)
        tables = sorted(inspector.get_table_names())
        revision = None
        if "alembic_version" in tables:
            with engine.connect() as connection:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
        return {
            "database_url": _redact_url(url),
            "reachable": True,
            "revision": revision,
            "tables": tables,
        }
    finally:
        engine.dispose()


def _redact_url(url: str) -> str:
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" not in rest:
        return url
    _credentials, host = rest.rsplit("@", 1)
    return f"{scheme}://***@{host}"
