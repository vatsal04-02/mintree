"""Structured JSON logs; every record carries the correlation (request) ID."""
import json
import logging
import logging.handlers
import queue
import sys
import time

from app.core.config import get_settings
from app.core.context import request_id_var


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
               "level": record.levelname, "logger": record.name, "msg": record.getMessage(),
               "request_id": request_id_var.get()}
        extra = getattr(record, "fields", None)
        if extra:
            out.update(extra)
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


class DropOnFullQueueHandler(logging.handlers.QueueHandler):
    """Never blocks the caller. Request threads log while holding DB transactions (and row locks); a stalled log sink
    (full pipe, slow disk) must not be able to stall them. Overflow is dropped and counted."""

    dropped = 0

    def enqueue(self, record):
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            DropOnFullQueueHandler.dropped += 1
            try:
                from app.core import observability as obs
                obs.LOG_DROPPED.inc()
            except Exception:
                pass


def configure_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_mintree_configured", False):
        return
    sink = logging.StreamHandler(sys.stderr)
    sink.setFormatter(JsonFormatter() if get_settings().log_json else logging.Formatter("%(levelname)s %(name)s %(message)s"))
    q: queue.Queue = queue.Queue(maxsize=20000)
    listener = logging.handlers.QueueListener(q, sink, respect_handler_level=True)
    listener.start()                       # a background thread owns the (possibly blocking) write
    root.handlers[:] = [DropOnFullQueueHandler(q)]
    root.setLevel(logging.INFO)
    root._mintree_configured = True  # type: ignore[attr-defined]
    root._mintree_listener = listener  # type: ignore[attr-defined]
