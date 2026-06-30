"""SQLAlchemy engine/session helpers (Ch. 5.4)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .schema import Base

if TYPE_CHECKING:
    pass


def make_engine(database_url: str | None = None) -> Engine:
    if database_url is None:
        from ..config import get_settings

        database_url = get_settings().database_url

    kwargs: dict = {}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            kwargs["poolclass"] = StaticPool
        else:
            _ensure_sqlite_dir(database_url)
    return create_engine(database_url, **kwargs)


def _ensure_sqlite_dir(database_url: str) -> None:
    # sqlite:///relative/path.db  or  sqlite:////absolute/path.db
    path = database_url.split("///", 1)[-1]
    parent = Path(path).parent
    if str(parent) not in ("", "."):
        parent.mkdir(parents=True, exist_ok=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine | None = None) -> Engine:
    """Create all tables (idempotent)."""
    engine = engine or make_engine()
    Base.metadata.create_all(engine)
    return engine
