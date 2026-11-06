"""Create/refresh a dedicated performance database with a realistic dataset.

    MINTREE_DATABASE_URL=postgresql+psycopg://.../mintree_perf python -m scripts.perf_dataset \
        --users 200 --items 20000 --suppliers 500

Refuses to run unless the database name ends in `_perf` or `_staging`, so it can never touch dev/prod data.
All load-test users share the password in PERF_PASSWORD and hold the `system_admin` role (permission-load
tests for restricted roles are a separate scenario).
"""
import argparse
import json
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
from app.core.clock import business_today  # noqa: E402

PERF_PASSWORD = "Perf-Load-Passw0rd!"
KINDS = ["RM", "PM", "BULK", "FG"]


def _opening_stock(s: Session, start: int, end: int, qty: int, uom_id: int) -> None:
    """Released opening-balance stock, set-based (fast): item + RELEASED batch + balance + RECEIPT ledger row per item.
    source_type='opening_balance' keeps it outside the goods-receipt reconciliation but inside every stock invariant."""
    s.execute(text("""
        WITH new_items AS (
          INSERT INTO items(item_code, name, kind, uom_id, is_batch_tracked, is_active, shelf_life_days)
          SELECT 'STK-' || lpad(g::text, 5, '0'), 'Stock item ' || g, 'RM', CAST(:uom AS int), true, true, 365 FROM generate_series(CAST(:a AS int), CAST(:b AS int)) g
          RETURNING id, item_code),
        wh AS (SELECT id FROM warehouses WHERE code = 'RM-WH'),
        batches AS (
          INSERT INTO inventory_batches(item_id, batch_no, qc_status, expiry_date, source_type, source_id)
          SELECT id, 'OB-' || item_code, 'RELEASED', business_today() + 365, 'opening_balance', 1 FROM new_items RETURNING id, item_id),
        bal AS (
          INSERT INTO stock_balances(item_id, warehouse_id, batch_id, qty_physical)
          SELECT b.item_id, (SELECT id FROM wh), b.id, CAST(:q AS numeric) FROM batches b RETURNING id, item_id, warehouse_id, batch_id)
        INSERT INTO stock_ledger(balance_id, item_id, warehouse_id, batch_id, qty_delta, movement_type, source_type, source_id, source_line)
        SELECT id, item_id, warehouse_id, batch_id, CAST(:q AS numeric), 'RECEIPT', 'opening_balance', 1, item_id FROM bal"""),
        {"uom": uom_id, "a": start + 1, "b": end, "q": qty})


def _hot_item(s: Session, qty: int, uom_id: int) -> dict:
    tag = int(__import__("time").time()) % 10_000_000
    row = s.execute(text("""
        WITH i AS (INSERT INTO items(item_code, name, kind, uom_id, is_batch_tracked, is_active, shelf_life_days)
                   VALUES ('HOT-' || CAST(:t AS text), 'Hot stock item ' || CAST(:t AS text), 'RM', CAST(:uom AS int), true, true, 365) RETURNING id),
        wh AS (SELECT id FROM warehouses WHERE code = 'RM-WH'),
        b AS (INSERT INTO inventory_batches(item_id, batch_no, qc_status, expiry_date, source_type, source_id)
              SELECT id, 'OB-HOT-' || CAST(:t AS text), 'RELEASED', business_today() + 365, 'opening_balance', 1 FROM i RETURNING id, item_id),
        bal AS (INSERT INTO stock_balances(item_id, warehouse_id, batch_id, qty_physical)
                SELECT b.item_id, (SELECT id FROM wh), b.id, CAST(:q AS numeric) FROM b RETURNING id, item_id, warehouse_id, batch_id),
        l AS (INSERT INTO stock_ledger(balance_id, item_id, warehouse_id, batch_id, qty_delta, movement_type, source_type, source_id, source_line)
              SELECT id, item_id, warehouse_id, batch_id, CAST(:q AS numeric), 'RECEIPT', 'opening_balance', 1, item_id FROM bal)
        SELECT (SELECT id FROM i) item_id, (SELECT id FROM wh) warehouse_id, (SELECT id FROM b) batch_id, (SELECT id FROM bal) balance_id"""),
        {"t": str(tag), "uom": uom_id, "q": qty}).mappings().one()
    return {"HOT_ITEM_ID": row["item_id"], "HOT_WAREHOUSE_ID": row["warehouse_id"], "HOT_BATCH_ID": row["batch_id"],
            "HOT_BALANCE_ID": row["balance_id"], "HOT_QTY": qty}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=200)
    ap.add_argument("--items", type=int, default=20000)
    ap.add_argument("--suppliers", type=int, default=500)
    ap.add_argument("--stock-items", type=int, default=300, help="items with released opening-balance stock (STK-*)")
    ap.add_argument("--stock-qty", type=int, default=1_000_000, help="opening quantity per stock item")
    ap.add_argument("--hot-qty", type=int, default=0, help="if >0: create a NEW dedicated hot item with exactly this stock")
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
        have_stk = s.scalar(text("select count(*) from items where item_code like 'STK-%'")) or 0
        if a.stock_items > have_stk:
            _opening_stock(s, have_stk, a.stock_items, a.stock_qty, uom_ids[0])
        if a.hot_qty:
            print(json.dumps(_hot_item(s, a.hot_qty, uom_ids[0])))
        s.commit()
        s.execute(text("ANALYZE"))
        s.commit()
    print(f"dataset ready in {db_name}: users={a.users} items={a.items} suppliers={a.suppliers} stock_items={a.stock_items}", file=sys.stderr)


if __name__ == "__main__":
    main()
