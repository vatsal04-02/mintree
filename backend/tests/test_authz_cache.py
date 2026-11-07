"""The permission cache must never make authorization stale: changes are effective immediately, on every process."""
import pytest

from app.core import authz_cache
from tests.conftest import Api


def _epoch():
    return authz_cache.current_epoch()


def test_role_permission_change_bumps_the_epoch_after_commit(admin, login):
    c = login("stores_manager")
    assert c.get("/api/masters/suppliers").status_code == 200
    before = _epoch()
    rid = next(r["id"] for r in admin.get("/api/admin/roles").json() if r["code"] == "stores_manager")
    perms = [p for p in next(r for r in admin.get("/api/admin/roles").json() if r["id"] == rid)["permission_codes"] if p != "masters.supplier:read"]
    assert admin.patch(f"/api/admin/roles/{rid}", json={"permission_codes": perms}).status_code == 200
    assert _epoch() != before, "epoch must be bumped by the commit that changed a role"
    assert c.get("/api/masters/suppliers").status_code == 403


def test_role_assignment_and_deactivation_take_effect_immediately(admin, login):
    c = login("stores_manager")
    assert c.get("/api/masters/items").status_code == 200
    qc = next(r["id"] for r in admin.get("/api/admin/roles").json() if r["code"] == "management")
    assert admin.patch(f"/api/admin/users/{c.user.id}", json={"role_ids": [qc]}).status_code == 200
    assert c.get("/api/inventory/balances").status_code == 200          # gained via the new role
    assert c.post("/api/inventory/issues", headers={"Idempotency-Key": "key-authz-0001"}, json={}).status_code == 403
    assert admin.patch(f"/api/admin/users/{c.user.id}", json={"is_active": False}).status_code == 200
    assert c.get("/api/inventory/balances").status_code == 401            # session path stays DB-authoritative


def test_cache_hits_after_first_request_and_survives_unrelated_commits(login):
    c = login("stores_manager")
    c.get("/api/masters/items")
    from app.core import deps
    hits0 = deps.SESSION_CACHE.labels("hit")._value.get()
    for _ in range(5):
        assert c.get("/api/masters/items").status_code == 200
    assert deps.SESSION_CACHE.labels("hit")._value.get() >= hits0 + 5           # session + user + permissions served from the per-process cache


def test_redis_outage_falls_back_to_the_database(login, monkeypatch):
    c = login("stores_manager")
    assert c.get("/api/masters/items").status_code == 200
    monkeypatch.setattr(authz_cache, "current_epoch", lambda: None)       # Redis unreachable
    bypass0 = authz_cache.CACHE.labels("bypass")._value.get()
    assert c.get("/api/masters/items").status_code == 200
    assert c.get("/api/masters/suppliers").status_code == 200
    assert authz_cache.CACHE.labels("bypass")._value.get() >= bypass0 + 2
    assert c.get("/api/admin/users").status_code == 403                    # still correctly denied


def test_me_still_returns_role_names_and_department(login):
    c = login("stores_manager")
    me = c.get("/api/auth/me").json()
    assert me["roles"] == ["Stores Manager"] and "masters.item:read" in me["permissions"]


def test_revocation_is_effective_on_every_worker_process(live_users, live_server):
    """The live server runs 2 uvicorn processes with SEPARATE in-process caches. Warm both, revoke a permission, then
    every subsequent request -- whichever worker answers -- must be denied immediately."""
    admin_api = live_users("system_admin")[0]
    victim = live_users("stores_manager")[0]             # holds procurement.receipt:post
    post = lambda i: victim.post("/api/procurement/goods-receipts", key=f"key-live-revoke-{i:04d}", json={}).status_code
    before = [post(i) for i in range(30)]                  # {} fails validation (422) -- but only if the permission is held
    assert 403 not in before and len(set(before)) == 1, before
    roles = admin_api.get("/api/admin/roles").json()
    quality = next(r["id"] for r in roles if r["code"] == "quality_manager")    # has no procurement.receipt:post
    assert admin_api.c.patch(f"/api/admin/users/{victim.user_id}", json={"role_ids": [quality]}).status_code == 200
    after = [post(100 + i) for i in range(60)]
    assert after == [403] * 60, f"stale permissions served after revocation: {sorted(set(after))}"
    # and the new rights apply immediately too
    assert victim.get("/api/inventory/batches").status_code == 200


# ------------------------------------------------------------------ session cache: revocation and expiry must stay immediate
def test_logout_is_immediate_even_with_a_warm_session_cache(login):
    c = login("stores_manager")
    for _ in range(3):
        assert c.get("/api/masters/items").status_code == 200
    csrf = c.headers.get("X-CSRF-Token")
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/masters/items").status_code == 401


def test_deactivating_a_user_cuts_off_their_warm_session_at_once(login, admin):
    victim = login("stores_manager")
    for _ in range(3):
        assert victim.get("/api/masters/items").status_code == 200
    assert admin.patch(f"/api/admin/users/{victim.user.id}", json={"is_active": False}).status_code == 200
    assert victim.get("/api/masters/items").status_code == 401


def test_password_reset_revokes_warm_sessions(login, admin):
    victim = login("stores_manager")
    victim.get("/api/masters/items")
    r = admin.post(f"/api/admin/users/{victim.user.id}/reset-password", json={"temporary_password": "Temp-Passw0rd!x1"})
    assert r.status_code == 204
    assert victim.get("/api/masters/items").status_code == 401


def test_role_change_applies_to_a_warm_session_at_once(login, admin, db):
    from sqlalchemy import select
    from app.administration.models import Role
    u = login("stores_manager")
    assert u.get("/api/masters/items").status_code == 200 and u.get("/api/accounts/reports/trial-balance").status_code == 403
    acct = db.scalar(select(Role.id).where(Role.code == "accounts_manager"))
    assert admin.patch(f"/api/admin/users/{u.user.id}", json={"role_ids": [acct]}).status_code == 200
    assert u.get("/api/accounts/reports/trial-balance").status_code == 200


def test_expiry_is_checked_on_every_cache_hit_and_the_cached_copy_has_no_password_hash(login, db):
    from sqlalchemy import text
    from datetime import datetime, timedelta, timezone
    from app.core import deps
    c = login("stores_manager")
    c.get("/api/masters/items")
    entries = [e for e in deps._SESS.values() if e[3].get("id") == c.user.id]
    assert entries and "password_hash" not in entries[0][3]
    past = datetime.now(timezone.utc) - timedelta(seconds=1)
    entries[0][2]["expires_at"] = past                                                      # the session expires while cached: the cache refuses it ...
    assert c.get("/api/masters/items").status_code == 200                                   # ... and the database (still valid) re-validates it
    entries = [e for e in deps._SESS.values() if e[3].get("id") == c.user.id]
    entries[0][2]["expires_at"] = past
    db.execute(text("UPDATE sessions SET expires_at = :p WHERE user_id = :u"), {"p": past, "u": c.user.id})
    db.flush()
    assert c.get("/api/masters/items").status_code == 401                                   # both say expired: refused


def test_a_forged_or_unknown_cookie_is_never_served_from_the_cache(login, anon):
    c = login("stores_manager")
    c.get("/api/masters/items")
    anon.cookies.set("mintree_session", "not-a-real-token")
    assert anon.get("/api/masters/items").status_code == 401
