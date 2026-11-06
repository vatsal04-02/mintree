"""Unit of work: the explicit transaction boundary for business postings.

    result = run_tx(db, lambda db: service_logic(db, ...), name="inventory.goods_receipt.post")

Guarantees
  * The transaction COMMITS inside this call, i.e. before the HTTP response exists. A failed commit is an
    exception, never a 200 (see BACKEND_ARCHITECTURE.md §2.1).
  * Any exception rolls the whole transaction back. Nothing is half-posted.
  * Deadlocks (40P01), serialization failures (40001) and lock timeouts (55P03) are retried a bounded number of
    times with jittered exponential back-off; the *whole* `work` function re-runs on a clean transaction, so it
    must be a pure function of the database state (no external calls, no side effects outside `db`).
  * Isolation is READ COMMITTED unless the caller asks for stronger (`isolation="SERIALIZABLE"`) for an invariant
    that needs it. Routine postings rely on row locks, not on SERIALIZABLE.
  * The request's earlier read-only transaction (authentication) is ended first, so the posting transaction is
    short and the request holds at most one connection at a time (admission-control invariant).
"""
import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.orm import Session

from app.core import observability as obs
from app.core.context import stats_var

log = logging.getLogger("mintree.tx")
T = TypeVar("T")

RETRYABLE = {"40P01": "deadlock", "40001": "serialization", "55P03": "lock_timeout"}
DEFAULT_RETRIES = 3
BASE_BACKOFF_S = 0.02


class ServiceBusy(HTTPException):
    """Contention that outlasted the retry budget. Clients should retry; nothing was posted."""

    def __init__(self, reason: str):
        super().__init__(503, f"The system is busy ({reason}); nothing was saved. Please retry.",
                         headers={"Retry-After": "1"})


def sqlstate(exc: BaseException) -> str | None:
    orig = getattr(exc, "orig", exc)
    return getattr(orig, "sqlstate", None) or getattr(getattr(orig, "diag", None), "sqlstate", None)


def run_tx(db: Session, work: Callable[[Session], T], *, name: str, isolation: str | None = None,
           max_retries: int = DEFAULT_RETRIES) -> T:
    attempts = max_retries + 1
    for attempt in range(attempts):
        try:
            if db.in_transaction():
                db.commit()  # end the auth/read transaction; releases its connection back to the pool
            if isolation:
                db.connection(execution_options={"isolation_level": isolation})
            result = work(db)
            db.commit()
            return result
        except (DBAPIError, OperationalError) as e:
            db.rollback()
            reason = RETRYABLE.get(sqlstate(e) or "")
            if not reason:
                if sqlstate(e) == "57014":  # statement_timeout
                    log.warning("statement timeout in %s", name)
                    raise ServiceBusy("statement timeout") from e
                raise
            if attempt == attempts - 1:
                obs.TX_FAILED.labels(reason).inc()
                log.error("transaction %s failed after %d attempts: %s", name, attempts, reason)
                raise ServiceBusy(reason) from e
            obs.TX_RETRIES.labels(reason).inc()
            st = stats_var.get()
            if st is not None:
                st.retries += 1
            log.info("retrying %s after %s (attempt %d/%d)", name, reason, attempt + 1, attempts)
            time.sleep(BASE_BACKOFF_S * (2 ** attempt) * (0.5 + random.random()))
        except BaseException:
            db.rollback()
            raise
    raise AssertionError("unreachable")
