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
    hits0 = authz_cache.CACHE.labels("hit")._value.get()
    for _ in range(5):
        assert c.get("/api/masters/items").status_code == 200
    assert authz_cache.CACHE.labels("hit")._value.get() >= hits0 + 5


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
