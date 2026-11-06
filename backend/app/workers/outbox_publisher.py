"""Dedicated outbox publisher:  python -m app.workers.outbox_publisher

Each step is its own short transaction; the broker call happens between them with no transaction open.
Several publishers may run concurrently (SKIP LOCKED); each event is claimed by exactly one at a time.
"""
import logging
import os
import signal
import socket
import time

from sqlalchemy.orm import Session

from app.core import observability as obs
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.core.outbox import CLAIM_SQL, MARK_FAILED_SQL, MARK_PUBLISHED_SQL, PURGE_SQL
from app.core.uow import run_tx
from app.workers.broker import Broker
from app.workers import consumers as _consumers  # noqa: F401  (registers consumers)

log = logging.getLogger("mintree.outbox")


class OutboxPublisher:
    def __init__(self, broker: Broker, name: str | None = None):
        s = get_settings()
        self.broker, self.batch, self.lease = broker, s.outbox_batch, s.outbox_lease_s
        self.max_attempts = s.outbox_max_attempts
        self.me = name or f"{socket.gethostname()}:{os.getpid()}"

    def run_once(self) -> int:
        with SessionLocal() as db:
            rows = run_tx(db, lambda d: d.execute(CLAIM_SQL, {"n": self.batch, "me": self.me, "lease": self.lease}).mappings().all(),
                          name="outbox.claim")
        if not rows:
            return 0
        ok, failed = [], []
        for r in rows:  # network I/O: no DB transaction is open here
            try:
                self.broker.publish(event_id=str(r["event_id"]), event_type=r["event_type"], payload=r["payload"],
                                    request_id=r["request_id"])
                ok.append(r["id"])
            except Exception as e:
                failed.append((r["id"], f"{type(e).__name__}: {e}"[:500]))
                obs.OUTBOX_PUBLISH_FAILURES.inc()
                log.warning("publish failed for outbox %s: %s", r["id"], e, extra={"fields": {"outbox_id": r["id"]}})

        def finish(d: Session):
            if ok:
                d.execute(MARK_PUBLISHED_SQL, {"ids": ok, "me": self.me})
            for oid, err in failed:
                d.execute(MARK_FAILED_SQL, {"id": oid, "err": err, "me": self.me, "max": self.max_attempts})

        with SessionLocal() as db:
            run_tx(db, finish, name="outbox.finish")
        obs.OUTBOX_PUBLISHED.inc(len(ok))
        return len(rows)

    def purge(self) -> int:
        with SessionLocal() as db:
            return run_tx(db, lambda d: d.execute(PURGE_SQL, {"d": get_settings().outbox_retention_days}).rowcount, name="outbox.purge")

    def run_forever(self, poll_s: float = 0.2) -> None:
        stop = {"v": False}
        signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("v", True))
        signal.signal(signal.SIGINT, lambda *_: stop.__setitem__("v", True))
        last_purge = 0.0
        while not stop["v"]:
            try:
                n = self.run_once()
            except Exception:
                log.exception("outbox cycle failed")
                n = 0
            if time.time() - last_purge > 3600:
                last_purge = time.time()
                try: self.purge()
                except Exception: log.exception("outbox purge failed")
            if n < self.batch:
                time.sleep(poll_s)


if __name__ == "__main__":
    configure_logging()
    from app.workers.broker import CeleryBroker
    OutboxPublisher(CeleryBroker()).run_forever()
