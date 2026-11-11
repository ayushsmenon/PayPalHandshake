from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models import Base


class PersistenceError(Exception):
    pass


class NotFoundError(PersistenceError):
    pass


class ReplayViolationError(PersistenceError):
    pass


class MandateExpiredError(PersistenceError):
    pass


class MandateAlreadyConsumedError(PersistenceError):
    pass


class DatabaseSessionManager:
    def __init__(self, db_url: str | None = None) -> None:
        self.db_url = db_url or os.getenv("DATABASE_URL", "sqlite:///:memory:")
        engine_kwargs: dict = {}
        if self.db_url.startswith("sqlite"):
            engine_kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30.0}
        else:
            engine_kwargs["pool_pre_ping"] = True

        self.engine = create_engine(self.db_url, **engine_kwargs)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )

    def create_tables(self) -> None:
        Base.metadata.create_all(self.engine)

    def drop_tables(self) -> None:
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        session: Session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class BaseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
