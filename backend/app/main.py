import logging
import uuid
from contextlib import asynccontextmanager

import anyio.to_thread

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import text

from app.administration.router import router as admin_router
from app.accounts.router import router as accounts_router
from app.administration.jobs import router as jobs_router
from app.auth.router import router as auth_router
from app.core.config import get_settings
from app.dispatch.router import router as dispatch_router
from app.core.errors import BusinessConflict
from app.core.context import client_ip_var, request_id_var
from app.core.db import SessionLocal, engine
from app.core.middleware import AdmissionControl, MetricsMiddleware, RequestContextMiddleware
from app.core.logging import configure_logging
from app.core import observability as obs
from app.documents.router import router as documents_router
from app.inventory.router import router as inventory_router
from app.masters.router import router as masters_router
from app.procurement.router import router as procurement_router
from app.production.router import router as production_router
from app.quality.router import router as quality_router
from app.rnd.router import router as rnd_router

configure_logging()
obs.instrument_engine(engine)
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
app.add_middleware(MetricsMiddleware)
app.add_middleware(RequestContextMiddleware)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error rid=%s", request_id_var.get())
    return JSONResponse({"detail": "Internal server error", "request_id": request_id_var.get()}, status_code=500)


@app.exception_handler(BusinessConflict)
async def business_conflict(request: Request, exc: BusinessConflict):
    obs.BUSINESS_CONFLICTS.labels(exc.code).inc()
    return JSONResponse({"detail": exc.detail}, status_code=409)


@app.get("/api/metrics", include_in_schema=False)
def metrics(request: Request):
    """Prometheus scrape endpoint. Exempt from admission control (see main.py); token-protected when configured."""
    s = get_settings()
    if s.metrics_token:
        if request.headers.get("authorization") != f"Bearer {s.metrics_token}":
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    elif s.environment == "production":
        return JSONResponse({"detail": "Metrics disabled: set MINTREE_METRICS_TOKEN"}, status_code=404)
    return Response(obs.render_metrics(), media_type="text/plain; version=0.0.4")


@app.get("/api/health")
def health():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
    return {"status": "ok", "environment": get_settings().environment}


for r in (auth_router, admin_router, jobs_router, masters_router, documents_router, inventory_router, procurement_router, accounts_router, rnd_router, quality_router, production_router, dispatch_router):
    app.include_router(r, prefix="/api")
