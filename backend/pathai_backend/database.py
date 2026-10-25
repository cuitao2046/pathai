from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


_LOCAL_DATABASE_URL = f"sqlite:///{(Path(__file__).resolve().parent.parent / '.local' / 'pathai.db').as_posix()}"


def resolve_database_url(app_env: str, database_url: str | None) -> str:
    environment = app_env.strip().lower()
    if environment not in {"local", "development", "test"}:
        if not database_url:
            raise RuntimeError("PATHAI_DATABASE_URL must be configured outside local development")
        parsed_url = make_url(database_url.replace("postgres://", "postgresql://", 1))
        if parsed_url.get_backend_name() != "postgresql":
            raise RuntimeError("Non-local environments require PostgreSQL")
        if parsed_url.drivername == "postgresql":
            parsed_url = parsed_url.set(drivername="postgresql+psycopg")
        return parsed_url.render_as_string(hide_password=False)
    return database_url or _LOCAL_DATABASE_URL


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    url = resolve_database_url(os.getenv("PATHAI_ENV", "local"), os.getenv("PATHAI_DATABASE_URL"))
    parsed_url = make_url(url)
    if parsed_url.get_backend_name() == "sqlite" and parsed_url.database not in {None, ":memory:"}:
        Path(parsed_url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, pool_pre_ping=True)
    if parsed_url.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
    return engine


def get_session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)
