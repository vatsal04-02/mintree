"""Structured JSON logs; every record carries the correlation (request) ID."""
import json
import logging
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


def configure_logging() -> None:
    root = logging.getLogger()
    if getattr(root, "_mintree_configured", False):
        return
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(JsonFormatter() if get_settings().log_json else logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root.handlers[:] = [h]
    root.setLevel(logging.INFO)
    root._mintree_configured = True  # type: ignore[attr-defined]
