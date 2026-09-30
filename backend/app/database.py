from collections.abc import Generator
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def make_engine(url: str):
    if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
        Path(url.removeprefix("sqlite:///")).resolve().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        url, pool_pre_ping=True, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {}
    )
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure_sqlite(connection, _):
            from .retrieval import sqlite_cosine

            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.create_function("cosine_distance", 2, sqlite_cosine, deterministic=True)

    return engine


def make_sessions(engine):
    return sessionmaker(engine, expire_on_commit=False)


def db_session(request: Request) -> Generator[Session, None, None]:
    with request.app.state.sessions() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
