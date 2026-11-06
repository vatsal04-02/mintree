from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.audit.models import AuditLog
from tests.conftest import PASSWORD, new_client


def test_unauthenticated_requests_are_rejected(anon):
    assert anon.get("/api/masters/items").status_code == 401
    assert anon.get("/api/auth/me").status_code == 401
    assert anon.get("/api/health").status_code == 200


def test_login_sets_httponly_cookie_and_returns_permissions(make_user, anon):
    u = make_user("stores_manager")
    r = anon.post("/api/auth/login", json={"email": u.email, "password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert "masters.item:read" in body["permissions"] and "admin.user:create" not in body["permissions"]
    assert "httponly" in r.headers["set-cookie"].lower()
    assert "samesite=lax" in r.headers["set-cookie"].lower()


def test_wrong_password_and_unknown_user_give_same_error(make_user, anon, db):
    u = make_user("stores_manager")
    a = anon.post("/api/auth/login", json={"email": u.email, "password": "nope"})
    b = anon.post("/api/auth/login", json={"email": "ghost@example.com", "password": "nope"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()
    assert db.scalar(select(AuditLog).where(AuditLog.action == "login_failed", AuditLog.user_id == u.id))


def test_account_locks_after_repeated_failures(make_user, anon):
    u = make_user("stores_manager")
    for _ in range(5):
        assert anon.post("/api/auth/login", json={"email": u.email, "password": "bad"}).status_code == 401
    # Even the correct password is refused while locked.
    assert anon.post("/api/auth/login", json={"email": u.email, "password": PASSWORD}).status_code == 423


def test_lock_expires(make_user, anon, db):
    u = make_user("stores_manager", locked_until=datetime.now(timezone.utc) - timedelta(minutes=1))
    assert anon.post("/api/auth/login", json={"email": u.email, "password": PASSWORD}).status_code == 200


def test_csrf_token_required_for_state_changes(login):
    c = login("system_admin")
    token = c.headers.pop("X-CSRF-Token")
    body = {"code": "KG2", "name": "Kilo 2"}
    assert c.post("/api/masters/units-of-measure", json=body).status_code == 403
    c.headers["X-CSRF-Token"] = "wrong"
    assert c.post("/api/masters/units-of-measure", json=body).status_code == 403
    c.headers["X-CSRF-Token"] = token
    assert c.post("/api/masters/units-of-measure", json=body).status_code == 201


def test_logout_revokes_session(login):
    c = login("stores_manager")
    assert c.get("/api/masters/items").status_code == 200
    assert c.post("/api/auth/logout").status_code == 204
    assert c.get("/api/masters/items").status_code == 401


def test_deactivated_user_loses_access_immediately(login, admin, db):
    c = login("stores_manager")
    assert c.get("/api/masters/items").status_code == 200
    assert admin.patch(f"/api/admin/users/{c.user.id}", json={"is_active": False}).status_code == 200
    assert c.get("/api/masters/items").status_code == 401


def test_forced_password_change_blocks_everything_else(make_user):
    u = make_user("system_admin", must_change_password=True)
    c = new_client(u.email)
    assert c.get("/api/auth/me").json()["must_change_password"] is True
    assert c.get("/api/masters/items").status_code == 403
    assert c.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "short"}).status_code == 422
    r = c.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "An0ther-Good-One"})
    assert r.status_code == 204
    assert c.get("/api/masters/items").status_code == 200


def test_password_change_revokes_other_sessions(make_user):
    u = make_user("stores_manager")
    c1, c2 = new_client(u.email), new_client(u.email)
    assert c1.post("/api/auth/change-password",
                   json={"current_password": PASSWORD, "new_password": "An0ther-Good-One"}).status_code == 204
    assert c1.get("/api/auth/me").status_code == 200
    assert c2.get("/api/auth/me").status_code == 401


def test_passwords_are_hashed_and_never_audited(make_user, admin, db):
    r = admin.post("/api/admin/users", json={"email": "new@example.com", "full_name": "New", "role_ids": [],
                                             "temporary_password": "Temp-Passw0rd!"})
    assert r.status_code == 201
    from app.administration.models import User
    u = db.scalar(select(User).where(User.email == "new@example.com"))
    assert u.password_hash.startswith("$argon2") and "Temp-Passw0rd" not in u.password_hash
    for row in db.scalars(select(AuditLog)):
        assert "Temp-Passw0rd" not in str(row.after) and "argon2" not in str(row.after)
