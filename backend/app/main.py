import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.administration.router import router as admin_router
from app.auth.router import router as auth_router
from app.core.config import get_settings
from app.core.context import client_ip_var, request_id_var
from app.core.db import SessionLocal
from app.documents.router import router as documents_router
from app.masters.router import router as masters_router

log = logging.getLogger("mintree")

app = FastAPI(title="Mintree Factory OS", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex
    request_id_var.set(rid[:64])
    client_ip_var.set(request.client.host if request.client else None)
    response = await call_next(request)
    response.headers["X-Request-ID"] = rid[:64]
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response


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
