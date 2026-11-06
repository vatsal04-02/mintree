import logging
import uuid
from contextlib import asynccontextmanager

import anyio.to_thread

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.administration.router import router as admin_router
from app.auth.router import router as auth_router
from app.core.config import get_settings
from app.core.context import client_ip_var, request_id_var
from app.core.db import SessionLocal
from app.core.middleware import AdmissionControl, RequestContextMiddleware
from app.documents.router import router as documents_router
from app.masters.router import router as masters_router

log = logging.getLogger("mintree")

@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    anyio.to_thread.current_default_thread_limiter().total_tokens = s.threadpool_size or (slots() + 4)
    yield


def slots() -> int:
    s = get_settings()
    return s.max_in_flight or (s.db_pool_size + s.db_max_overflow)


app = FastAPI(title="Mintree Factory OS", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json",
              lifespan=lifespan)
# Order: last added runs first. Request context must wrap admission control so shed requests still get an ID.
app.add_middleware(AdmissionControl, slots=slots(), timeout_s=get_settings().admission_timeout_s,
                   exempt_prefixes=("/api/metrics",))
app.add_middleware(RequestContextMiddleware)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error rid=%s", request_id_var.get())
    return JSONResponse({"detail": "Internal server error", "request_id": request_id_var.get()}, status_code=500)


@app.get("/api/health")
def health():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok", "environment": get_settings().environment}


for r in (auth_router, admin_router, masters_router, documents_router):
    app.include_router(r, prefix="/api")
