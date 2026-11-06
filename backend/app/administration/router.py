from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.administration.models import Department, Permission, Role, SessionRecord, User, user_roles
from app.administration import schemas as s
from app.audit import service as audit
from app.audit.models import AuditLog
from app.core.crud import Page, Resource, build_router, translate_integrity_error
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import Conflict, Forbidden, NotFound, Unprocessable
from app.core.security import hash_password, validate_password_strength
from sqlalchemy.exc import IntegrityError

router = APIRouter(prefix="/admin", tags=["administration"])

SYSTEM_ADMIN = "system_admin"


def _perms_of(roles: list[Role]) -> set[str]:
    return {p.code for r in roles for p in r.permissions}


def _flush(db: Session):
    try:
        db.flush()
    except IntegrityError as e:
        raise translate_integrity_error(e) from e


# ---------------- Users ----------------
def _assert_can_manage(cu: CurrentUser, target: User) -> None:
    """Privilege-escalation guard: you cannot modify an account holding rights you don't have yourself."""
    if not _perms_of(target.roles) <= cu.permissions:
        raise Forbidden("This account has permissions above your own and cannot be modified by you")


def _roles_by_ids(db: Session, ids: list[int]) -> list[Role]:
    roles = list(db.scalars(select(Role).where(Role.id.in_(ids)))) if ids else []
    if len(roles) != len(set(ids)):
        raise Unprocessable("One or more roles do not exist")
    return roles


def _assert_can_grant(cu: CurrentUser, roles: list[Role]) -> None:
    if not _perms_of(roles) <= cu.permissions:
        raise Forbidden("You cannot assign roles with permissions you do not hold yourself")


def _active_sysadmins(db: Session, excluding: int | None = None) -> int:
    q = (select(func.count(func.distinct(User.id))).join(user_roles, user_roles.c.user_id == User.id)
         .join(Role, Role.id == user_roles.c.role_id).where(Role.code == SYSTEM_ADMIN, User.is_active.is_(True)))
    if excluding:
        q = q.where(User.id != excluding)
    return db.scalar(q) or 0


def _user_snap(u: User) -> dict:
    return {"email": u.email, "full_name": u.full_name, "department_id": u.department_id, "is_active": u.is_active,
            "roles": sorted(r.code for r in u.roles)}


@router.get("/users", response_model=Page[s.UserOut])
def list_users(q: str | None = None, is_active: bool | None = None, page: int = Query(1, ge=1),
               page_size: int = Query(50, ge=1, le=200), db: Session = Depends(get_db, scope="function"),
               _: CurrentUser = Depends(require("admin.user:read"))):
    stmt = select(User)
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.email.ilike(like), User.full_name.ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(User.full_name, User.id).limit(page_size).offset((page - 1) * page_size)).unique().all()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.get("/users/{user_id}", response_model=s.UserOut)
def get_user(user_id: int, db: Session = Depends(get_db, scope="function"), _: CurrentUser = Depends(require("admin.user:read"))):
    u = db.get(User, user_id)
    if not u:
        raise NotFound("User")
    return u


@router.post("/users", response_model=s.UserOut, status_code=201)
def create_user(body: s.UserCreate, db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(require("admin.user:create"))):
    if err := validate_password_strength(body.temporary_password):
        raise Unprocessable(err)
    roles = _roles_by_ids(db, body.role_ids)
    _assert_can_grant(cu, roles)
    u = User(email=body.email.lower(), full_name=body.full_name, department_id=body.department_id,
             password_hash=hash_password(body.temporary_password), must_change_password=True, roles=roles)
    db.add(u)
    _flush(db)
    audit.record(db, user=cu.user, action="create", entity="user", entity_id=u.id, after=_user_snap(u))
    return u


@router.patch("/users/{user_id}", response_model=s.UserOut)
def update_user(user_id: int, body: s.UserUpdate, db: Session = Depends(get_db, scope="function"),
                cu: CurrentUser = Depends(require("admin.user:update"))):
    u = db.get(User, user_id)
    if not u:
        raise NotFound("User")
    _assert_can_manage(cu, u)
    data = body.model_dump(exclude_unset=True)
    before = _user_snap(u)
    is_sysadmin = any(r.code == SYSTEM_ADMIN for r in u.roles)

    if data.get("is_active") is False:
        if u.id == cu.id:
            raise Unprocessable("You cannot deactivate your own account")
    if "role_ids" in data and data["role_ids"] is not None:
        roles = _roles_by_ids(db, data["role_ids"])
        _assert_can_grant(cu, roles)
        u.roles = roles
    for k in ("full_name", "department_id", "is_active"):
        if k in data and data[k] is not None or (k == "department_id" and k in data):
            setattr(u, k, data[k])
    still_admin = u.is_active and any(r.code == SYSTEM_ADMIN for r in u.roles)
    if is_sysadmin and not still_admin and _active_sysadmins(db, excluding=u.id) == 0:
        raise Unprocessable("At least one active System Administrator must remain")
    if not u.is_active:
        now = datetime.now(timezone.utc)
        for sess in db.scalars(select(SessionRecord).where(SessionRecord.user_id == u.id, SessionRecord.revoked_at.is_(None))):
            sess.revoked_at = now
    _flush(db)
    b, a = audit.diff(before, _user_snap(u))
    if a:
        audit.record(db, user=cu.user, action="update", entity="user", entity_id=u.id, before=b, after=a)
    return u


@router.post("/users/{user_id}/reset-password", status_code=204)
def reset_password(user_id: int, body: s.ResetPasswordIn, db: Session = Depends(get_db, scope="function"),
                   cu: CurrentUser = Depends(require("admin.user:update"))):
    u = db.get(User, user_id)
    if not u:
        raise NotFound("User")
    _assert_can_manage(cu, u)
    if err := validate_password_strength(body.temporary_password):
        raise Unprocessable(err)
    u.password_hash = hash_password(body.temporary_password)
    u.must_change_password = True
    u.failed_login_count = 0
    u.locked_until = None
    now = datetime.now(timezone.utc)
    for sess in db.scalars(select(SessionRecord).where(SessionRecord.user_id == u.id, SessionRecord.revoked_at.is_(None))):
        sess.revoked_at = now
    audit.record(db, user=cu.user, action="password_reset", entity="user", entity_id=u.id)


# ---------------- Roles & permissions ----------------
def _role_out(r: Role, counts: dict[int, int]) -> s.RoleOut:
    return s.RoleOut(id=r.id, code=r.code, name=r.name, description=r.description, is_system=r.is_system,
                     permission_codes=sorted(p.code for p in r.permissions), user_count=counts.get(r.id, 0))


def _counts(db: Session) -> dict[int, int]:
    return dict(db.execute(select(user_roles.c.role_id, func.count()).group_by(user_roles.c.role_id)).all())


def _perms_by_codes(db: Session, codes: list[str]) -> list[Permission]:
    perms = list(db.scalars(select(Permission).where(Permission.code.in_(codes)))) if codes else []
    if len(perms) != len(set(codes)):
        raise Unprocessable("Unknown permission code")
    return perms


@router.get("/permissions", response_model=list[s.PermissionOut])
def list_permissions(db: Session = Depends(get_db, scope="function"), _: CurrentUser = Depends(require("admin.role:read"))):
    return db.scalars(select(Permission).order_by(Permission.resource, Permission.action)).all()


@router.get("/roles", response_model=list[s.RoleOut])
def list_roles(db: Session = Depends(get_db, scope="function"), _: CurrentUser = Depends(require("admin.role:read"))):
    counts = _counts(db)
    return [_role_out(r, counts) for r in db.scalars(select(Role).order_by(Role.name))]


@router.post("/roles", response_model=s.RoleOut, status_code=201)
def create_role(body: s.RoleCreate, db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(require("admin.role:create"))):
    perms = _perms_by_codes(db, body.permission_codes)
    if not {p.code for p in perms} <= cu.permissions:
        raise Forbidden("You cannot grant permissions you do not hold yourself")
    r = Role(code=body.code, name=body.name, description=body.description, permissions=perms)
    db.add(r)
    _flush(db)
    audit.record(db, user=cu.user, action="create", entity="role", entity_id=r.id,
                 after={"code": r.code, "name": r.name, "permissions": sorted(body.permission_codes)})
    return _role_out(r, {})


@router.patch("/roles/{role_id}", response_model=s.RoleOut)
def update_role(role_id: int, body: s.RoleUpdate, db: Session = Depends(get_db, scope="function"),
                cu: CurrentUser = Depends(require("admin.role:update"))):
    r = db.get(Role, role_id)
    if not r:
        raise NotFound("Role")
    data = body.model_dump(exclude_unset=True)
    if r.code == SYSTEM_ADMIN and "permission_codes" in data:
        raise Forbidden("The System Administrator role always holds every permission")
    before = {"name": r.name, "description": r.description, "permissions": sorted(p.code for p in r.permissions)}
    if not {p.code for p in r.permissions} <= cu.permissions:
        raise Forbidden("This role has permissions above your own and cannot be modified by you")
    if data.get("permission_codes") is not None:
        perms = _perms_by_codes(db, data["permission_codes"])
        if not {p.code for p in perms} <= cu.permissions:
            raise Forbidden("You cannot grant permissions you do not hold yourself")
        r.permissions = perms
    for k in ("name", "description"):
        if data.get(k) is not None:
            setattr(r, k, data[k])
    _flush(db)
    after = {"name": r.name, "description": r.description, "permissions": sorted(p.code for p in r.permissions)}
    b, a = audit.diff(before, after)
    if a:
        audit.record(db, user=cu.user, action="update", entity="role", entity_id=r.id, before=b, after=a)
    return _role_out(r, _counts(db))


# ---------------- Departments (generic CRUD) ----------------
router.include_router(build_router(
    Resource("department", Department, "admin.department", s.DepartmentIn, s.DepartmentUpdate, s.DepartmentOut,
             ["code", "name"], default_sort="code"),
    prefix="/departments", tags=["administration"]))


# ---------------- Audit log (read-only) ----------------
@router.get("/audit-logs", response_model=Page[s.AuditOut])
def list_audit(
    entity: str | None = None, entity_id: str | None = None, action: str | None = None, user_id: int | None = None,
    date_from: datetime | None = None, date_to: datetime | None = None, q: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db, scope="function"), _: CurrentUser = Depends(require("admin.audit:read")),
):
    stmt = select(AuditLog)
    for col, val in (("entity", entity), ("entity_id", entity_id), ("action", action), ("user_id", user_id)):
        if val is not None:
            stmt = stmt.where(getattr(AuditLog, col) == val)
    if date_from:
        stmt = stmt.where(AuditLog.occurred_at >= date_from)
    if date_to:
        stmt = stmt.where(AuditLog.occurred_at <= date_to)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(AuditLog.user_email.ilike(like), AuditLog.entity.ilike(like),
                              AuditLog.action.ilike(like), cast(AuditLog.entity_id, String).ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(AuditLog.id.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}
