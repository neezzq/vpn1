from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from .config import Settings


def make_engine(settings: Settings):
    url = settings.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        # allow usage across threads (bot + web)
        connect_args = {"check_same_thread": False}
    engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True, future=True)

    # SQLite tuning for concurrency
    if url.startswith("sqlite"):
        with engine.begin() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL;"))
            conn.execute(text("PRAGMA synchronous=NORMAL;"))
    return engine


def make_session_factory(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False, future=True)


@contextmanager
def session_scope(SessionLocal) -> Session:
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
