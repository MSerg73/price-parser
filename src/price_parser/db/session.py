from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import resolve_database_url


def create_engine_and_session(
    database_url: str | None = None,
    *,
    echo: bool = False,
) -> tuple[Engine, sessionmaker[Session]]:
    url = resolve_database_url(database_url)
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False

    engine = create_engine(
        url,
        echo=echo,
        future=True,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    if url.startswith("sqlite"):
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)

    factory = sessionmaker(
        bind=engine,
        class_=Session,
        expire_on_commit=False,
        autoflush=False,
        future=True,
    )
    return engine, factory


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
