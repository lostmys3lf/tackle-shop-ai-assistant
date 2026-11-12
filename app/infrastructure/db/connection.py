"""PostgreSQL database connection management using SQLAlchemy."""

from collections.abc import Generator
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from app.config import settings

_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker | None = None


def get_engine() -> Engine:
    """Return the SQLAlchemy Engine singleton instance."""
    global _ENGINE  # pylint: disable=global-statement
    if _ENGINE is None:
        _ENGINE = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5},
        )
    return _ENGINE


def get_session_factory() -> sessionmaker:
    """Return the sessionmaker singleton configured for the engine."""
    global _SESSION_FACTORY  # pylint: disable=global-statement
    if _SESSION_FACTORY is None:
        _SESSION_FACTORY = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    return _SESSION_FACTORY


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Context manager yielding a transactional database session."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_connection() -> bool:
    """Check the database connection health by executing a simple query."""
    try:
        engine = get_engine()
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except (SQLAlchemyError, OSError):
        return False
