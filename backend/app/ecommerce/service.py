"""E-commerce: marketplace accounts, SKU mapping, idempotent order ingestion, exception queue, stock-sync preview, settlements.

Ingestion is idempotent at two levels: unique(account, external_order_id) on external_orders, and unique(channel, external_ref)
on sales_orders. Re-importing a file or re-delivering a webhook never creates a second order. An order whose SKUs cannot be
mapped is *kept* in the EXCEPTION queue (never dropped) and can be retried after the mapping is fixed. Marketplace APIs are
not called: orders arrive as CSV/JSON import or signed webhook (honest scope until credentials/approvals exist)."""
import csv
import hashlib
import hmac
import io
import json
from datetime import date, datetime
from decimal import Decimal

from fastapi.encoders import jsonable_encoder
from psycopg import errors as pgerr
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.config import get_settings
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.core.context import request_id_var
from app.core.platform_models import WebhookEvent
from app.dispatch import service as dispatch
from app.ecommerce.models import ExternalOrder, ExternalOrderItem, MarketplaceAccount, MarketplaceSettlement, SkuMapping


class SystemActor:
    """Acting identity for unauthenticated, signature-verified inbound calls (webhooks). Records show email 'system:marketplace-webhook'.
    created_by columns need a real user id, so it is the first (bootstrap) user -- documented in TRANSACTION_INTEGRITY.md."""
    email = "system:marketplace-webhook"

    def __init__(self):
        self.id = None


def _dup(ex: IntegrityError, code: str, msg: str):
    if isinstance(ex.orig, pgerr.UniqueViolation):
        raise BusinessConflict(code, msg) from ex
    raise ex


def create_account(db: Session, cu: CurrentUser, data: dict) -> dict:
    a = MarketplaceAccount(**data)
    db.add(a)
    try:
        db.flush()
    except IntegrityError as ex:
        _dup(ex, "duplicate_account", "This marketplace seller account already exists")
    audit.record(db, user=cu, action="create", entity="marketplace_account", entity_id=a.id, after={"platform": a.platform, "name": a.name})
    return account_dict(a)


def _account(db: Session, aid: int) -> MarketplaceAccount:
    a = db.get(MarketplaceAccount, aid)
    if not a:
        raise NotFound("Marketplace account")
    return a


def upsert_mapping(db: Session, cu: CurrentUser, account_id: int, platform_sku: str, item_id: int, pack_multiplier: Decimal, safety_stock: Decimal) -> dict:
    _account(db, account_id)
    db.execute(pg_insert(SkuMapping).values(account_id=account_id, platform_sku=platform_sku, item_id=item_id, pack_multiplier=pack_multiplier, safety_stock=safety_stock)
               .on_conflict_do_update(constraint="uq_sku_mappings_sku", set_={"item_id": item_id, "pack_multiplier": pack_multiplier, "safety_stock": safety_stock}))
    audit.record(db, user=cu, action="map_sku", entity="marketplace_account", entity_id=account_id, after={"sku": platform_sku, "item_id": item_id})
    return {"account_id": account_id, "platform_sku": platform_sku, "item_id": item_id, "pack_multiplier": pack_multiplier, "safety_stock": safety_stock}


# ------------------------------------------------------------------ ingestion
def _try_create_sales_order(db: Session, cu: CurrentUser, acct: MarketplaceAccount, eo: ExternalOrder) -> None:
    """Map SKUs -> create a DRAFT sales order; otherwise park the order in the exception queue with the reason."""
    maps = {m.platform_sku: m for m in db.scalars(select(SkuMapping).where(SkuMapping.account_id == acct.id, SkuMapping.platform_sku.in_([i.platform_sku for i in eo.items])))}
    missing = sorted({i.platform_sku for i in eo.items if i.platform_sku not in maps})
    if missing:
        eo.status, eo.exception_reason = "EXCEPTION", f"Unmapped SKU(s): {', '.join(missing)}"
        return
    lines: dict[int, dict] = {}
    for i in eo.items:                                             # two listings may map to the same internal item: merge quantities
        m = maps[i.platform_sku]
        qty = Decimal(i.qty) * Decimal(m.pack_multiplier)
        unit = Decimal(i.unit_price) / Decimal(m.pack_multiplier)
        cur = lines.setdefault(m.item_id, {"item_id": m.item_id, "qty": Decimal(0), "value": Decimal(0)})
        cur["qty"] += qty
        cur["value"] += Decimal(i.qty) * Decimal(i.unit_price)
    payload_lines = [{"item_id": l["item_id"], "qty": l["qty"], "unit_price": (l["value"] / l["qty"]).quantize(Decimal("0.0001"))} for l in lines.values()]
    try:
        so = dispatch.create_order(db, cu, customer_id=acct.customer_id, warehouse_id=acct.warehouse_id, channel="MARKETPLACE",
                                   external_ref=f"{acct.platform}:{eo.external_order_id}", shipping_address=eo.shipping_address, lines=payload_lines)
    except (Unprocessable, BusinessConflict) as ex:
        eo.status, eo.exception_reason = "EXCEPTION", getattr(ex, "message", None) or str(getattr(ex, "detail", ex))
        return
    eo.status, eo.sales_order_id, eo.exception_reason = "SO_CREATED", so["id"], None


def ingest_order(db: Session, cu: CurrentUser, acct: MarketplaceAccount, o: dict) -> str:
    """-> 'created' | 'exception' | 'duplicate'. One savepoint per order so one bad row never aborts a batch."""
    with db.begin_nested():
        ins = db.execute(pg_insert(ExternalOrder).values(account_id=acct.id, external_order_id=str(o["order_id"]), order_date=o.get("order_date"),
                                                         customer_name=o.get("customer_name"), shipping_address=o.get("shipping_address"), total=o.get("total"), payload=jsonable_encoder(o))
                         .on_conflict_do_nothing(constraint="uq_external_orders_ext").returning(ExternalOrder.id)).scalar()
        if ins is None:
            return "duplicate"
        for it in o["items"]:
            db.add(ExternalOrderItem(external_order_pk=ins, platform_sku=it["sku"], qty=it["qty"], unit_price=it["unit_price"]))
        db.flush()
        eo = db.get(ExternalOrder, ins)
        db.refresh(eo)
        _try_create_sales_order(db, cu, acct, eo)
        return "created" if eo.status == "SO_CREATED" else "exception"


def ingest_orders(db: Session, cu: CurrentUser, account_id: int, orders: list[dict]) -> dict:
    a = _account(db, account_id)
    if a.status != "ACTIVE":
        raise BusinessConflict("account_paused", "This marketplace account is paused")
    res = {"created": 0, "exception": 0, "duplicate": 0}
    for o in orders:
        res[ingest_order(db, cu, a, o)] += 1
    db.execute(text("UPDATE marketplace_accounts SET last_sync_at = now() WHERE id = :i"), {"i": a.id})
    audit.record(db, user=cu, action="ingest", entity="marketplace_account", entity_id=a.id, after=res)
    return res


def parse_csv(content: str) -> list[dict]:
    """Columns: order_id, order_date, customer_name, shipping_address, sku, qty, unit_price. Rows sharing order_id are one order."""
    orders: dict[str, dict] = {}
    reader = csv.DictReader(io.StringIO(content))
    missing = {"order_id", "sku", "qty", "unit_price"} - set(reader.fieldnames or [])
    if missing:
        raise Unprocessable(f"Invalid import file: missing column(s) {', '.join(sorted(missing))}")
    try:
        for n, row in enumerate(reader, 2):
            oid = (row.get("order_id") or "").strip()
            if not oid:
                raise ValueError(f"row {n}: order_id is required")
            o = orders.setdefault(oid, {"order_id": oid, "order_date": date.fromisoformat(row["order_date"]) if row.get("order_date") else None,
                                        "customer_name": row.get("customer_name") or None, "shipping_address": row.get("shipping_address") or None, "items": []})
            qty, price = Decimal(row["qty"]), Decimal(row["unit_price"])
            if qty <= 0 or price < 0:
                raise ValueError(f"row {n}: qty must be > 0 and price >= 0")
            o["items"].append({"sku": row["sku"].strip(), "qty": qty, "unit_price": price})
    except (KeyError, ValueError, ArithmeticError) as ex:
        raise Unprocessable(f"Invalid import file: {ex}") from ex
    if not orders:
        raise Unprocessable("Invalid import file: no orders found")
    for o in orders.values():
        o["total"] = sum(i["qty"] * i["unit_price"] for i in o["items"])
    return list(orders.values())


def verify_signature(body: bytes, signature: str | None) -> bool:
    expected = hmac.new(get_settings().marketplace_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


def handle_order_webhook(db: Session, cu: CurrentUser, account_id: int, event: dict) -> dict:
    ev = db.execute(pg_insert(WebhookEvent).values(source=f"marketplace:{account_id}", external_event_id=str(event["event_id"]), payload=json.loads(json.dumps(event, default=str)),
                                                   request_id=request_id_var.get()).on_conflict_do_nothing(constraint="uq_webhook_events_ext").returning(WebhookEvent.id)).scalar()
    if ev is None:
        return {"result": "duplicate"}
    cu.id = db.scalar(text("SELECT min(id) FROM users"))
    r = ingest_orders(db, cu, account_id, [event["order"]])
    db.execute(text("UPDATE webhook_events SET status = 'PROCESSED' WHERE id = :i"), {"i": ev})
    return {"result": "processed", **r}


def retry_exception(db: Session, cu: CurrentUser, ext_id: int) -> dict:
    eo = db.scalar(select(ExternalOrder).where(ExternalOrder.id == ext_id).with_for_update())
    if not eo:
        raise NotFound("External order")
    if eo.status != "EXCEPTION":
        raise BusinessConflict("not_in_exception", "Only orders in the exception queue can be retried")
    _try_create_sales_order(db, cu, _account(db, eo.account_id), eo)
    audit.record(db, user=cu, action="retry", entity="external_order", entity_id=eo.id, after={"status": eo.status})
    return order_dict(eo)


# ------------------------------------------------------------------ stock-sync preview (read-only: nothing is pushed to marketplaces)
def inventory_sync_preview(db: Session, account_id: int) -> dict:
    a = _account(db, account_id)
    rows = db.execute(text("""
        SELECT m.platform_sku, m.item_id, m.pack_multiplier, m.safety_stock, COALESCE(sum(sb.qty_available), 0) AS available
          FROM sku_mappings m
          LEFT JOIN stock_balances sb ON sb.item_id = m.item_id AND sb.warehouse_id = :w
         WHERE m.account_id = :a GROUP BY m.platform_sku, m.item_id, m.pack_multiplier, m.safety_stock ORDER BY m.platform_sku"""), {"a": a.id, "w": a.warehouse_id}).mappings().all()
    out = []
    for r in rows:
        sellable = max(Decimal(0), Decimal(r["available"]) - Decimal(r["safety_stock"]))
        listed = (sellable / Decimal(r["pack_multiplier"])).to_integral_value(rounding="ROUND_FLOOR")
        out.append({"platform_sku": r["platform_sku"], "item_id": r["item_id"], "available": r["available"], "safety_stock": r["safety_stock"],
                    "pack_multiplier": r["pack_multiplier"], "quantity_to_list": listed})
    return {"account_id": a.id, "mode": "PREVIEW_ONLY", "items": out}


# ------------------------------------------------------------------ settlements
def record_settlement(db: Session, cu: CurrentUser, account_id: int, data: dict) -> dict:
    _account(db, account_id)
    net = data["gross_sales"] - data["commission"] - data["shipping_fees"] - data["ad_fees"] - data["returns_refunds"] - data["other_deductions"]
    s = MarketplaceSettlement(account_id=account_id, net_settlement=net, **data)
    db.add(s)
    try:
        db.flush()
    except IntegrityError as ex:
        _dup(ex, "duplicate_settlement", "A settlement with this reference already exists for the account")
    audit.record(db, user=cu, action="create", entity="marketplace_settlement", entity_id=s.id, after={"net": str(net)})
    return settlement_dict(s, db)


def settlement_dict(s: MarketplaceSettlement, db: Session) -> dict:
    expected = db.execute(text("""SELECT COALESCE(sum(eo.total), 0) FROM external_orders eo WHERE eo.account_id = :a AND eo.status = 'SO_CREATED'
                                    AND eo.order_date BETWEEN :s AND :e"""), {"a": s.account_id, "s": s.period_start, "e": s.period_end}).scalar()
    diff = None if s.received_amount is None else s.received_amount - s.net_settlement
    return {"id": s.id, "reference": s.reference, "gross_sales": s.gross_sales, "net_settlement": s.net_settlement, "received_amount": s.received_amount,
            "unreconciled_difference": diff, "reconciled": diff == 0 if diff is not None else False,
            "orders_value_in_period": expected, "gross_vs_orders_difference": s.gross_sales - expected}


def set_received(db: Session, cu: CurrentUser, sid: int, amount: Decimal) -> dict:
    s = db.get(MarketplaceSettlement, sid)
    if not s:
        raise NotFound("Settlement")
    s.received_amount = amount
    audit.record(db, user=cu, action="reconcile", entity="marketplace_settlement", entity_id=s.id, after={"received": str(amount)})
    return settlement_dict(s, db)


def account_dict(a: MarketplaceAccount) -> dict:
    return {"id": a.id, "platform": a.platform, "name": a.name, "seller_id": a.seller_id, "customer_id": a.customer_id, "warehouse_id": a.warehouse_id, "status": a.status, "last_sync_at": a.last_sync_at}


def order_dict(e: ExternalOrder) -> dict:
    return {"id": e.id, "account_id": e.account_id, "external_order_id": e.external_order_id, "status": e.status, "exception_reason": e.exception_reason,
            "sales_order_id": e.sales_order_id, "total": e.total, "items": [{"sku": i.platform_sku, "qty": i.qty, "unit_price": i.unit_price} for i in e.items]}
