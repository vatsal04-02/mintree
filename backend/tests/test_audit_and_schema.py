import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.audit.models import AuditLog
from app.core.db import Base, engine
from app.core.permissions import all_permission_codes


def test_audit_log_is_append_only_at_database_level(admin, db):
    admin.post("/api/masters/brands", json={"code": "B1", "name": "Brand"})
    row = db.scalar(select(AuditLog).order_by(AuditLog.id.desc()))
    for sql in ("UPDATE audit_logs SET action='tampered'", "DELETE FROM audit_logs", "TRUNCATE audit_logs"):
        with pytest.raises(DBAPIError, match="append-only"):
            with db.begin_nested():
                db.execute(text(sql))
    assert row.action != "tampered"


def test_audit_requires_permission_and_carries_request_context(admin, login):
    r = admin.post("/api/masters/brands", json={"code": "B2", "name": "Brand 2"}, headers={"X-Request-ID": "req-123"})
    assert r.headers["x-request-id"] == "req-123"
    entry = admin.get("/api/admin/audit-logs", params={"entity": "brand"}).json()["items"][0]
    assert entry["request_id"] == "req-123" and entry["user_email"] == admin.user.email
    assert login("production_manager").get("/api/admin/audit-logs").status_code == 403


def test_failed_write_leaves_no_partial_data_or_audit(admin):
    admin.post("/api/masters/brands", json={"code": "DUP", "name": "One"})
    before = admin.get("/api/admin/audit-logs", params={"entity": "brand"}).json()["total"]
    assert admin.post("/api/masters/brands", json={"code": "DUP", "name": "Two"}).status_code == 409
    assert admin.get("/api/admin/audit-logs", params={"entity": "brand"}).json()["total"] == before


def test_models_and_migrations_are_in_sync():
    """Fails when someone changes a model without writing a migration."""
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], f"Model/migration drift: {diff}"


def test_permission_registry_is_fully_seeded(admin):
    seeded = {p["code"] for p in admin.get("/api/admin/permissions").json()}
    assert seeded == set(all_permission_codes())
    sysadmin = next(r for r in admin.get("/api/admin/roles").json() if r["code"] == "system_admin")
    assert set(sysadmin["permission_codes"]) == seeded


def test_security_headers(anon):
    r = anon.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
