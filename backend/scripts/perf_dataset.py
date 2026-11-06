"""Create/refresh a dedicated performance database with a realistic dataset.

    MINTREE_DATABASE_URL=postgresql+psycopg://.../mintree_perf python -m scripts.perf_dataset \
        --users 200 --items 20000 --suppliers 500

Refuses to run unless the database name ends in `_perf` or `_staging`, so it can never touch dev/prod data.
All load-test users share the password in PERF_PASSWORD and hold the `system_admin` role (permission-load
tests for restricted roles are a separate scenario).
"""
import argparse
import random
import sys

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.administration.models import Role, User
from app.core.config import get_settings
from app.core.db import engine
from app.core.security import hash_password
from app.masters.models import Item, Supplier, UnitOfMeasure
from app.seed import seed

PERF_PASSWORD = "Perf-Load-Passw0rd!"
KINDS = ["RM", "PM", "BULK", "FG"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=200)
    ap.add_argument("--items", type=int, default=20000)
    ap.add_argument("--suppliers", type=int, default=500)
    a = ap.parse_args()

    db_name = make_url(get_settings().database_url).database or ""
    if not db_name.endswith(("_perf", "_staging")):
        sys.exit(f"Refusing to load a performance dataset into '{db_name}' (name must end in _perf or _staging)")

    random.seed(42)
    with Session(engine) as s:
        seed(s)
        s.commit()
        uom_ids = [u.id for u in s.scalars(select(UnitOfMeasure))]
        role = s.scalar(select(Role).where(Role.code == "system_admin"))
        pw = hash_password(PERF_PASSWORD)  # hash once: argon2 per user would take minutes
        have_users = s.scalar(text("select count(*) from users where email like 'perf%@example.com'")) or 0
        for i in range(have_users, a.users):
            s.add(User(email=f"perf{i:04d}@example.com", full_name=f"Perf User {i}", password_hash=pw, roles=[role]))
        have_items = s.scalar(text("select count(*) from items where item_code like 'PERF-%'")) or 0
        s.bulk_insert_mappings(Item, [
            {"item_code": f"PERF-{i:06d}", "name": f"Perf item {i} {random.choice(['serum','cream','bottle','pump','carton','glycerin','niacinamide'])}",
             "kind": random.choice(KINDS), "uom_id": random.choice(uom_ids), "is_batch_tracked": True, "is_active": True}
            for i in range(have_items, a.items)])
        have_sup = s.scalar(text("select count(*) from suppliers where code like 'PSUP-%'")) or 0
        s.bulk_insert_mappings(Supplier, [
            {"code": f"PSUP-{i:05d}", "name": f"Perf supplier {i}", "category": "RM_PM", "approval_status": "APPROVED", "is_active": True}
            for i in range(have_sup, a.suppliers)])
        s.commit()
        s.execute(text("ANALYZE"))
        s.commit()
    print(f"dataset ready in {db_name}: users={a.users} items={a.items} suppliers={a.suppliers}")


if __name__ == "__main__":
    main()
