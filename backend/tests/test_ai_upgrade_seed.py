"""Upgrading a database that predates the AI layer must grant the new ai.* permissions to the system roles, additively."""
from sqlalchemy import select

from app.administration.models import Role
from app.seed import seed


def test_seed_adds_missing_ai_permissions_to_existing_system_roles_only_additively(db):
    r = db.scalar(select(Role).where(Role.code == "purchase_manager"))
    ai = [p for p in r.permissions if p.code.startswith("ai.")]
    other = next(p for p in r.permissions if not p.code.startswith("ai."))
    r.permissions = [p for p in r.permissions if not p.code.startswith("ai.") and p is not other]      # simulate an old database + an admin who removed one permission
    db.flush()
    seed(db)
    codes = {p.code for p in db.scalar(select(Role).where(Role.code == "purchase_manager")).permissions}
    assert {p.code for p in ai} <= codes and other.code not in codes
