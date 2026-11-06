from collections.abc import Iterator

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

# Deterministic constraint names so Alembic migrations are stable.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _connect_options() -> str:
    s = get_settings()
    opts = []
    for name, val in (("statement_timeout", s.db_statement_timeout_ms), ("lock_timeout", s.db_lock_timeout_ms),
                      ("idle_in_transaction_session_timeout", s.db_idle_in_tx_timeout_ms)):
        if val:
            opts.append(f"-c {name}={val}")
    return " ".join(opts)


def _make_engine():
    s = get_settings()
    return create_engine(
        s.database_url,
        pool_size=s.db_pool_size,
        max_overflow=s.db_max_overflow,
        pool_timeout=s.db_pool_timeout_s,
        pool_recycle=s.db_pool_recycle_s,
        pool_pre_ping=s.db_pre_ping,
        connect_args={"options": _connect_options(), "application_name": "mintree-api",
                      "prepare_threshold": None if s.db_prepare_threshold.lower() == "none" else int(s.db_prepare_threshold)},
    )


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db() -> Iterator[Session]:
    """One transaction per request: commit on success, roll back on error.

    Audit rows are written in the same transaction as the change they describe.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
