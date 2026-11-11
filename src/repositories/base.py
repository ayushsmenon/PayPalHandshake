"""Session management and transactional boundary context managers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models import Base


class PersistenceError(Exception):
    """Base exception for persistence layer operations."""
    pass


class NotFoundError(PersistenceError):
    """Raised when an expected entity does not exist."""
    pass


class ReplayViolationError(PersistenceError):
    """Raised when an operation violates replay protection (e.g. duplicate idempotency key or replayed mandate)."""
    pass


class MandateExpiredError(PersistenceError):
    """Raised when attempting to operate on an expired mandate."""
    pass


class MandateAlreadyConsumedError(PersistenceError):
    """Raised when attempting to consume a mandate that has already been consumed."""
    pass


class DatabaseSessionManager:
    """Manages database engine, session factory, and transaction scopes."""

    def __init__(self, db_url: str | None = None) -> None:
        self.db_url = db_url or os.getenv("DATABASE_URL", "sqlite:///:memory:")
        # Configure engine options
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
        """Creates all tables defined in Base metadata (useful for tests and in-memory databases)."""
        Base.metadata.create_all(self.engine)

    def drop_tables(self) -> None:
        """Drops all tables defined in Base metadata."""
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        """Provides a transactional scope around a series of operations."""
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
    """Base class for all repository implementations."""

    def __init__(self, session: Session) -> None:
        self.session = session
