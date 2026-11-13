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
from app.accounts.models import Account, AccountingPeriod
from app.masters.models import UnitOfMeasure, Warehouse
from app.core.clock import business_today

DEPARTMENTS = [("MGMT", "Management"), ("PROD", "Production"), ("PURCH", "Purchase"), ("STORES", "Stores & Warehouse"),
               ("QA", "Quality"), ("RND", "R&D"), ("MAINT", "Maintenance"), ("DISP", "Dispatch"),
               ("ACCTS", "Accounts"), ("MKTG", "Marketing"), ("SALES", "Sales & E-commerce"), ("IT", "IT / Administration")]
UOMS = [("KG", "Kilogram"), ("G", "Gram"), ("L", "Litre"), ("ML", "Millilitre"), ("PCS", "Pieces"),
        ("NOS", "Numbers"), ("BOX", "Box"), ("CTN", "Carton")]
# Proposed defaults from the PRD; must be reconciled with the physical warehouse structure.
WAREHOUSES = [("RM-WH", "RM Warehouse", "RM"), ("PM-WH", "PM Warehouse", "PM"), ("QC-QRN", "QC Quarantine", "QC_QUARANTINE"),
              ("REJ", "Rejected Materials", "REJECTED"), ("BULK", "Bulk Storage", "BULK"), ("WIP", "Work in Progress", "WIP"),
              ("FG-WH", "Finished Goods", "FG"), ("DISP-WH", "Dispatch Warehouse", "DISPATCH")]


COA = [("1000", "Cash", "ASSET"), ("1100", "Bank", "ASSET"), ("1200", "Accounts Receivable", "ASSET"), ("1300", "Inventory - Raw & Packaging Materials", "ASSET"),
       ("1310", "Inventory - Work in Progress / Bulk", "ASSET"), ("1320", "Inventory - Finished Goods", "ASSET"), ("1500", "Input GST", "ASSET"),
       ("2000", "Accounts Payable", "LIABILITY"), ("2100", "Goods Received Not Invoiced (GRNI)", "LIABILITY"), ("2200", "Output GST", "LIABILITY"),
       ("3000", "Owner's Equity", "EQUITY"), ("4000", "Sales Revenue", "REVENUE"), ("4100", "Sales Returns", "REVENUE"),
       ("5000", "Cost of Goods Sold", "EXPENSE"), ("5100", "Production Wastage & Variance", "EXPENSE"), ("5200", "Inventory Adjustments", "EXPENSE"),
       ("5300", "Purchase Price Variance", "EXPENSE"), ("5400", "Bank Charges", "EXPENSE"), ("5500", "Marketplace Fees & Settlement Adjustments", "EXPENSE")]


def _fiscal_periods(today):
    import calendar
    from datetime import date
    y = today.year if today.month >= 4 else today.year - 1
    for k in range(12):
        m = 4 + k
        yy, mm = (y, m) if m <= 12 else (y + 1, m - 12)
        yield f"{yy}-{mm:02d}", date(yy, mm, 1), date(yy, mm, calendar.monthrange(yy, mm)[1])


# Permissions introduced after a database was created, granted additively to the roles that carry them by default (never removed, never re-added once an administrator took them away
# from a role that already had the rest). Customer invoice drafts: drafting and posting.
UPGRADE_GRANTS = {"accounts.sales_invoice:draft", "accounts.sales_invoice:post", "accounts.einvoice:read", "accounts.einvoice:submit", "accounts.einvoice:cancel",
                  "quality.recall:read", "quality.recall:create", "quality.recall:notify", "quality.recall:close", "production.rework:read", "production.rework:create", "production.rework:complete"}


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
    # Databases created before the AI layer have the system roles without the ai.* permissions. Add those (only those, only ever additively), so
    # upgrading does not leave the feature invisible. Other permissions an administrator removed from a role are never re-added.
    for code, role in roles.items():
        if code in ROLES and role.is_system and code != "system_admin":
            have_codes = {p.code for p in role.permissions}
            role.permissions = list(role.permissions) + [perms[c] for c in sorted(role_permission_codes(code)) if (c.startswith("ai.") or c in UPGRADE_GRANTS) and c not in have_codes]

    have = {d.code for d in db.scalars(select(Department))}
    db.add_all(Department(code=c, name=n) for c, n in DEPARTMENTS if c not in have)
    have = {u.code for u in db.scalars(select(UnitOfMeasure))}
    db.add_all(UnitOfMeasure(code=c, name=n) for c, n in UOMS if c not in have)
    have = {w.code for w in db.scalars(select(Warehouse))}
    db.add_all(Warehouse(code=c, name=n, kind=k) for c, n, k in WAREHOUSES if c not in have)
    have = {a.code for a in db.scalars(select(Account))}
    db.add_all(Account(code=c, name=n, type=t) for c, n, t in COA if c not in have)
    from datetime import date as _d
    have = {p.name for p in db.scalars(select(AccountingPeriod))}
    for fy in (business_today(), _d(business_today().year - 1, business_today().month, 1)):   # current and previous fiscal year
        db.add_all(AccountingPeriod(name=n, start_date=a, end_date=b) for n, a, b in _fiscal_periods(fy) if n not in have and not have.add(n))
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
