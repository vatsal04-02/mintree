"""Idempotent seed: `python -m app.seed`.

* Syncs the permission registry into the DB (system_admin always holds everything).
* Creates the default system roles once (later edits to their permissions are preserved).
* Creates proposed-default departments / warehouses / units (to be reconciled with Mintree).
* Creates the first System Administrator only when no users exist.
"""
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.administration.models import Department, Permission, Role, User
from app.audit import service as audit
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.permissions import RESOURCES, ROLES, role_permission_codes
from app.core.security import hash_password
from app.masters.models import UnitOfMeasure, Warehouse

DEPARTMENTS = [("MGMT", "Management"), ("PROD", "Production"), ("PURCH", "Purchase"), ("STORES", "Stores & Warehouse"),
               ("QA", "Quality"), ("RND", "R&D"), ("MAINT", "Maintenance"), ("DISP", "Dispatch"),
               ("ACCTS", "Accounts"), ("MKTG", "Marketing"), ("SALES", "Sales & E-commerce"), ("IT", "IT / Administration")]
UOMS = [("KG", "Kilogram"), ("G", "Gram"), ("L", "Litre"), ("ML", "Millilitre"), ("PCS", "Pieces"),
        ("NOS", "Numbers"), ("BOX", "Box"), ("CTN", "Carton")]
# Proposed defaults from the PRD; must be reconciled with the physical warehouse structure.
WAREHOUSES = [("RM-WH", "RM Warehouse", "RM"), ("PM-WH", "PM Warehouse", "PM"), ("QC-QRN", "QC Quarantine", "QC_QUARANTINE"),
              ("REJ", "Rejected Materials", "REJECTED"), ("BULK", "Bulk Storage", "BULK"), ("WIP", "Work in Progress", "WIP"),
              ("FG-WH", "Finished Goods", "FG"), ("DISP-WH", "Dispatch Warehouse", "DISPATCH")]


def sync_permissions(db: Session) -> dict[str, Permission]:
    existing = {p.code: p for p in db.scalars(select(Permission))}
    for res, actions in RESOURCES.items():
        for act in actions:
            code = f"{res}:{act}"
            if code not in existing:
                p = Permission(code=code, resource=res, action=act)
                db.add(p)
                existing[code] = p
    db.flush()
    return existing


def seed(db: Session) -> str | None:
    perms = sync_permissions(db)
    roles = {r.code: r for r in db.scalars(select(Role))}
    for code, (name, desc, _) in ROLES.items():
        if code not in roles:
            roles[code] = Role(code=code, name=name, description=desc, is_system=True,
                               permissions=[perms[c] for c in sorted(role_permission_codes(code))])
            db.add(roles[code])
    roles["system_admin"].permissions = list(perms.values())  # always everything

    have = {d.code for d in db.scalars(select(Department))}
    db.add_all(Department(code=c, name=n) for c, n in DEPARTMENTS if c not in have)
    have = {u.code for u in db.scalars(select(UnitOfMeasure))}
    db.add_all(UnitOfMeasure(code=c, name=n) for c, n in UOMS if c not in have)
    have = {w.code for w in db.scalars(select(Warehouse))}
    db.add_all(Warehouse(code=c, name=n, kind=k) for c, n, k in WAREHOUSES if c not in have)
    db.flush()

    generated = None
    if db.scalar(select(User.id).limit(1)) is None:
        s = get_settings()
        pw = s.bootstrap_admin_password
        if not pw:
            pw = generated = secrets.token_urlsafe(12) + "aA1"
        admin = User(email=s.bootstrap_admin_email.lower(), full_name="System Administrator",
                     password_hash=hash_password(pw), must_change_password=True,
                     department_id=db.scalar(select(Department.id).where(Department.code == "IT")),
                     roles=[roles["system_admin"]])
        db.add(admin)
        db.flush()
        audit.record(db, user=None, user_email="system", action="bootstrap_admin", entity="user", entity_id=admin.id,
                     source="seed")
    return generated


if __name__ == "__main__":
    with SessionLocal() as db:
        pw = seed(db)
        db.commit()
    print("Seed complete.")
    if pw:
        print(f"Bootstrap admin: {get_settings().bootstrap_admin_email}  temporary password: {pw}")
        print("You will be required to change it at first login.")
