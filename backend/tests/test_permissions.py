"""Backend permission enforcement and privilege-escalation guards."""
import pytest

from tests.conftest import PASSWORD


def _role_id(admin, code):
    return next(r["id"] for r in admin.get("/api/admin/roles").json() if r["code"] == code)


SUPPLIER = {"code": "SUP1", "name": "Acme Chemicals"}


@pytest.mark.parametrize("role,method,url,expected", [
    ("production_operator", "get", "/api/masters/suppliers", 403),
    ("production_operator", "get", "/api/masters/items", 200),
    ("production_operator", "post", "/api/masters/items", 403),
    ("stores_manager", "post", "/api/masters/suppliers", 403),
    ("stores_manager", "get", "/api/masters/suppliers", 200),
    ("purchase_manager", "post", "/api/masters/suppliers", 201),
    ("sales_operator", "get", "/api/admin/users", 403),
    ("business_admin", "get", "/api/admin/users", 200),
    ("business_admin", "post", "/api/admin/roles", 403),
    ("management", "get", "/api/admin/audit-logs", 403),
    ("system_admin", "get", "/api/admin/audit-logs", 200),
])
def test_role_matrix(login, role, method, url, expected):
    c = login(role)
    kw = {"json": SUPPLIER} if method == "post" and "suppliers" in url else ({"json": {}} if method == "post" else {})
    assert getattr(c, method)(url, **kw).status_code == expected


def test_user_with_no_roles_can_do_nothing(login):
    c = login()
    assert c.get("/api/auth/me").status_code == 200
    for url in ("/api/masters/items", "/api/masters/suppliers", "/api/documents", "/api/admin/users"):
        assert c.get(url).status_code == 403


def test_supplier_bank_details_are_restricted(login, admin):
    body = {**SUPPLIER, "bank_name": "HDFC", "bank_account_no": "123456789012", "bank_ifsc": "HDFC0001234"}
    created = admin.post("/api/masters/suppliers", json=body).json()
    assert created["bank_account_no"] == "123456789012"

    ops = login("stores_manager")  # no bank permissions
    row = ops.get(f"/api/masters/suppliers/{created['id']}").json()
    assert row["bank_name"] is None and row["bank_account_no"] is None and row["bank_ifsc"] is None

    purchase = login("purchase_manager")  # can read, not update -> masked account number
    row = purchase.get(f"/api/masters/suppliers/{created['id']}").json()
    assert row["bank_name"] == "HDFC" and row["bank_account_no"].endswith("9012") and "12345" not in row["bank_account_no"]
    # ...and cannot write or clear bank fields.
    assert purchase.patch(f"/api/masters/suppliers/{created['id']}", json={"bank_ifsc": "ICIC0000001"}).status_code == 403
    assert purchase.patch(f"/api/masters/suppliers/{created['id']}", json={"bank_name": None}).status_code == 403
    assert purchase.post("/api/masters/suppliers", json={**SUPPLIER, "code": "SUP2", "bank_name": "X"}).status_code == 403
    assert purchase.patch(f"/api/masters/suppliers/{created['id']}", json={"city": "Pune"}).status_code == 200

    accounts = login("accounts_manager")
    assert accounts.get(f"/api/masters/suppliers/{created['id']}").json()["bank_account_no"] == "123456789012"


def test_bank_account_number_never_appears_in_audit(admin):
    admin.post("/api/masters/suppliers", json={**SUPPLIER, "bank_account_no": "999988887777"})
    logs = admin.get("/api/admin/audit-logs", params={"entity": "supplier"}).json()["items"]
    assert logs and "999988887777" not in str(logs)


def test_cannot_grant_roles_above_own_permissions(login, admin):
    # A user who may create users but is not a system administrator.
    role = admin.post("/api/admin/roles", json={"code": "hr_lead", "name": "HR Lead",
                      "permission_codes": ["admin.user:read", "admin.user:create", "admin.user:update", "admin.role:read"]})
    assert role.status_code == 201
    c = login()
    admin.patch(f"/api/admin/users/{c.user.id}", json={"role_ids": [role.json()["id"]]})
    sysadmin_id = _role_id(admin, "system_admin")
    r = c.post("/api/admin/users", json={"email": "x@example.com", "full_name": "X", "role_ids": [sysadmin_id],
                                         "temporary_password": "Temp-Passw0rd!"})
    assert r.status_code == 403
    # Cannot promote self either.
    assert c.patch(f"/api/admin/users/{c.user.id}", json={"role_ids": [sysadmin_id]}).status_code == 403


def test_cannot_take_over_a_more_privileged_account(login, admin):
    role = admin.post("/api/admin/roles", json={"code": "helpdesk", "name": "Helpdesk",
                      "permission_codes": ["admin.user:read", "admin.user:update"]}).json()
    helpdesk = login()
    admin.patch(f"/api/admin/users/{helpdesk.user.id}", json={"role_ids": [role["id"]]})
    victim = login("system_admin")
    r = helpdesk.post(f"/api/admin/users/{victim.user.id}/reset-password", json={"temporary_password": "Hacked-Passw0rd!"})
    assert r.status_code == 403
    assert helpdesk.patch(f"/api/admin/users/{victim.user.id}", json={"is_active": False}).status_code == 403


def test_role_permissions_cannot_exceed_editors_own(login, admin):
    c = login("business_admin")  # has admin.role:read only
    assert c.post("/api/admin/roles", json={"code": "x1", "name": "X", "permission_codes": ["masters.item:read"]}).status_code == 403
    role = admin.post("/api/admin/roles", json={"code": "role_mgr", "name": "Role manager",
                      "permission_codes": ["admin.role:read", "admin.role:create", "admin.role:update", "masters.item:read"]}).json()
    rm = login()
    admin.patch(f"/api/admin/users/{rm.user.id}", json={"role_ids": [role["id"]]})
    assert rm.post("/api/admin/roles", json={"code": "x2", "name": "X", "permission_codes": ["masters.item:read"]}).status_code == 201
    assert rm.post("/api/admin/roles", json={"code": "x3", "name": "X", "permission_codes": ["masters.supplier:delete"]}).status_code == 403
    sa = _role_id(admin, "system_admin")
    assert admin.patch(f"/api/admin/roles/{sa}", json={"permission_codes": []}).status_code == 403


def test_permission_changes_take_effect_immediately(login, admin):
    c = login("stores_manager")
    assert c.get("/api/masters/suppliers").status_code == 200
    rid = _role_id(admin, "stores_manager")
    perms = [p for p in next(r for r in admin.get("/api/admin/roles").json() if r["id"] == rid)["permission_codes"]
             if p != "masters.supplier:read"]
    assert admin.patch(f"/api/admin/roles/{rid}", json={"permission_codes": perms}).status_code == 200
    assert c.get("/api/masters/suppliers").status_code == 403


def test_self_deactivation_and_last_sysadmin_protection(admin, login, db):
    assert admin.patch(f"/api/admin/users/{admin.user.id}", json={"is_active": False}).status_code == 422
    from sqlalchemy import select
    from app.administration.models import Role, User
    # The seeded bootstrap admin is a second sysadmin; remove it from the picture to test the guard.
    others = [u for u in db.scalars(select(User)) if u.id != admin.user.id and any(r.code == "system_admin" for r in u.roles)]
    for u in others:
        u.is_active = False
    db.flush()
    other = login("business_admin")
    sa_role = db.scalar(select(Role).where(Role.code == "system_admin"))
    r = admin.patch(f"/api/admin/users/{admin.user.id}", json={"role_ids": []})
    assert r.status_code == 422 and "System Administrator" in r.json()["detail"]
