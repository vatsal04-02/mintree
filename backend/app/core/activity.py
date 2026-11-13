"""Business activity: the committed events people see in the Live activity timeline and that drive live dashboard refreshes.

One source of truth: the transactional outbox. `emit()` writes a standard event in the caller's business transaction (atomic with the change),
so an event exists if and only if the change committed; a rolled-back operation leaves nothing to show. The event stream (`/api/events`) and the
timeline (`/api/activity`) both read the same rows, filtered per user by the permission each event type declares. PostgreSQL stays the authority:
an event only says "this changed"; the screen then re-reads authorised data through the normal API.

Payload contract (all optional but `number`): number, id, actor_id, source ("demo-live" | "demo-control" | absent for a person), plus type-specific fields.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core import outbox
from app.core.context import source_var


@dataclass(frozen=True)
class Kind:
    module: str                       # frontend module id (/m/<module>/<screen>)
    screen: str
    perm: str                         # permission needed to see the event (checked again on the backend for every viewer)
    scopes: tuple[str, ...]           # dashboard scopes to refresh (see lib/live.tsx useLiveRevision)
    title: Callable[[dict], str]
    icon: str = "activity"
    open_key: str | None = "id"       # payload key holding the record id, so the link opens that record on its screen (None: just the screen)


def _n(p: dict) -> str:
    return str(p.get("number") or p.get("ref") or p.get("id") or "")


def _money(p: dict, key: str = "total") -> str:
    v = p.get(key) or p.get("amount")
    return f" (₹{v})" if v else ""


def _by(p: dict, key: str, words: dict[str, str], default: str) -> str:
    return words.get(str(p.get(key)), default)


O = ("overview", "orders")
CATALOGUE: dict[str, Kind] = {
    # ---- sales & dispatch
    "sales.order.created": Kind("dispatch", "orders", "dispatch.order:read", O, lambda p: f"New customer order {_n(p)}{_money(p)}", "order"),
    "sales.order.confirmed": Kind("dispatch", "orders", "dispatch.order:read", O, lambda p: f"Order {_n(p)} confirmed", "order"),
    "sales.order.allocated": Kind("dispatch", "orders", "dispatch.order:read", O + ("inventory",), lambda p: f"Stock allocated to order {_n(p)}", "stock"),
    "sales.order.cancelled": Kind("dispatch", "orders", "dispatch.order:read", O + ("inventory",), lambda p: f"Order {_n(p)} cancelled", "order"),
    "sales.quotation.created": Kind("sales", "quotations", "sales.quotation:read", ("overview", "sales"), lambda p: f"Quotation {_n(p)} drafted", "quote"),
    "sales.quotation.sent": Kind("sales", "quotations", "sales.quotation:read", ("overview", "sales"), lambda p: f"Quotation {_n(p)} sent to customer", "quote"),
    "sales.quotation.accepted": Kind("sales", "quotations", "sales.quotation:read", ("overview", "sales"), lambda p: f"Quotation {_n(p)} accepted", "quote"),
    "sales.quotation.rejected": Kind("sales", "quotations", "sales.quotation:read", ("overview", "sales"), lambda p: f"Quotation {_n(p)} rejected", "quote"),
    "sales.quotation.convert": Kind("sales", "quotations", "sales.quotation:read", O + ("sales",), lambda p: f"Quotation {_n(p)} converted to an order", "quote"),
    "crm.lead.converted": Kind("sales", "leads", "crm.lead:read", ("overview", "sales"), lambda p: "Lead converted to a customer", "lead", open_key="lead_id"),
    "dispatch.order.dispatched": Kind("dispatch", "invoices", "accounts.sales_invoice:read", O + ("accounts", "inventory"),
                                      lambda p: f"Order {_n(p)} dispatched; invoice {p.get('invoice', '')} posted{_money(p)}", "invoice", open_key=None),
    "dispatch.shipment.requested": Kind("dispatch", "shipments", "dispatch.shipment:read", ("overview", "orders"), lambda p: f"Shipment booked for {p.get('order', '')} via {p.get('courier', 'courier')}", "ship", open_key="shipment_id"),
    "dispatch.shipment.delivery_attempt": Kind("dispatch", "shipments", "dispatch.shipment:read", ("overview", "orders"),
                                              lambda p: _by(p, "outcome", {"DELIVERED": "Shipment delivered", "FAILED": "Delivery attempt failed", "RTO": "Shipment returning to origin"},
                                                            f"Delivery update: {p.get('outcome', '')}"), "ship", open_key="shipment_id"),
    "dispatch.return.decided": Kind("dispatch", "returns", "dispatch.return:read", O + ("accounts", "inventory"), lambda p: f"Return decided ({p.get('decision', '')}); credit note {p.get('credit_note', '')}", "return", open_key="return_id"),
    # ---- accounts
    "accounts.einvoice.generated": Kind("dispatch", "invoices", "accounts.sales_invoice:read", ("accounts",), lambda p: f"E-invoice IRN generated for {_n(p)}" + (" (SANDBOX)" if p.get("mode") == "SANDBOX" else ""), "invoice", None),
    "accounts.einvoice.cancelled": Kind("dispatch", "invoices", "accounts.sales_invoice:read", ("accounts",), lambda p: f"E-invoice IRN cancelled for {_n(p)}", "invoice", None),
    # ---- recall, rework, returns, expiry
    "quality.recall.opened": Kind("quality", "recalls", "quality.recall:read", ("overview", "quality", "inventory", "orders"), lambda p: f"Recall {_n(p)} opened: {p.get('batches', '')} batches held, {p.get('customers', 0)} customers to contact", "qc"),
    "quality.recall.batch_resolved": Kind("quality", "recalls", "quality.recall:read", ("quality", "inventory"), lambda p: f"Recall {_n(p)}: a held batch was {'released' if p.get('decision') == 'RELEASED' else 'rejected'}", "qc"),
    "quality.recall.closed": Kind("quality", "recalls", "quality.recall:read", ("overview", "quality"), lambda p: f"Recall {_n(p)} closed", "qc"),
    "inventory.expiry.swept": Kind("inventory", "expiry-control", "inventory.batch:read", ("overview", "inventory"), lambda p: f"Expiry sweep: {p.get('held', 0)} expired batch(es) held, {p.get('soon', 0)} expiring within 30 days", "stock", None),
    "production.material.returned": Kind("production", "orders", "production.order:read", ("production", "inventory"), lambda p: f"Unused material returned to stock from {_n(p)}", "prod"),
    "production.rework.started": Kind("production", "rework", "production.rework:read", ("production", "inventory"), lambda p: f"Rework {_n(p)} started on a held batch", "prod"),
    "production.rework.completed": Kind("production", "rework", "production.rework:read", ("production", "inventory", "quality"), lambda p: f"Rework {_n(p)} completed; the new batch awaits QC", "prod"),
    "production.rework.cancelled": Kind("production", "rework", "production.rework:read", ("production", "inventory"), lambda p: f"Rework {_n(p)} cancelled; stock returned to its batch", "prod"),
    # ---- accounts (drafts)
    "accounts.invoice.drafted": Kind("dispatch", "invoice-drafts", "accounts.sales_invoice:read", ("accounts",), lambda p: f"Invoice draft created for {_n(p)}{_money(p)} (not posted)", "invoice"),
    "accounts.invoice.draft_cancelled": Kind("dispatch", "invoice-drafts", "accounts.sales_invoice:read", ("accounts",), lambda p: f"{_n(p)} cancelled; nothing was posted", "invoice"),
    "accounts.payment.posted": Kind("accounts", "payments", "accounts.payment:read", ("overview", "accounts"),
                                    lambda p: f"{'Customer payment received' if p.get('direction') == 'RECEIVED' else 'Payment posted'} {_n(p)}{_money(p)}", "payment"),
    "accounts.supplier_invoice.posted": Kind("accounts", "supplier-invoices", "accounts.supplier_invoice:read", ("overview", "accounts", "purchase"), lambda p: f"Supplier invoice {_n(p)} posted{_money(p)}", "invoice"),
    "accounts.debit_note.posted": Kind("accounts", "supplier-invoices", "accounts.debit_note:read", ("accounts",), lambda p: f"Debit note {_n(p)} posted", "invoice", open_key=None),
    # ---- opportunities
    **{f"crm.opportunity.{st}": Kind("sales", "opportunities", "crm.opportunity:read", ("overview", "sales"), (lambda label: lambda p: f"Deal {_n(p)} moved to {label}")(st.title()), "lead", None)
       for st in ("qualified", "proposal", "negotiation", "won", "lost")},
    # ---- procurement
    "procurement.pr.created": Kind("procurement", "requests", "procurement.pr:read", ("overview", "purchase"), lambda p: f"Purchase request {_n(p)} created", "request"),
    "procurement.pr.submitted": Kind("procurement", "requests", "procurement.pr:read", ("overview", "purchase"), lambda p: f"Purchase request {_n(p)} submitted for approval", "request"),
    "procurement.pr.approved": Kind("procurement", "requests", "procurement.pr:read", ("overview", "purchase"), lambda p: f"Purchase request {_n(p)} approved", "request"),
    "procurement.pr.rejected": Kind("procurement", "requests", "procurement.pr:read", ("overview", "purchase"), lambda p: f"Purchase request {_n(p)} rejected", "request"),
    "procurement.purchase_order.created": Kind("procurement", "orders", "procurement.po:read", ("overview", "purchase"), lambda p: f"Purchase order {_n(p)} drafted{_money(p)}", "po"),
    "procurement.purchase_order.submitted": Kind("procurement", "orders", "procurement.po:read", ("overview", "purchase"), lambda p: f"Purchase order {_n(p)} awaiting approval", "po"),
    "procurement.purchase_order.approved": Kind("procurement", "orders", "procurement.po:read", ("overview", "purchase"), lambda p: f"Purchase order {_n(p)} approved{_money(p)}", "po", open_key="po_id"),
    "procurement.purchase_order.rejected": Kind("procurement", "orders", "procurement.po:read", ("overview", "purchase"), lambda p: f"Purchase order {_n(p)} sent back", "po"),
    "procurement.purchase_order.cancelled": Kind("procurement", "orders", "procurement.po:read", ("overview", "purchase"), lambda p: f"Purchase order {_n(p)} cancelled", "po"),
    "procurement.purchase_order.amended": Kind("procurement", "orders", "procurement.po:read", ("purchase",), lambda p: "Purchase order amended", "po", open_key=None),
    "inventory.goods_receipt.posted": Kind("procurement", "receipts", "procurement.receipt:read", ("overview", "purchase", "inventory", "quality"),
                                           lambda p: f"Goods receipt {_n(p)} posted; incoming QC created", "receipt", open_key="gr_id"),
    "procurement.purchase_return.posted": Kind("procurement", "returns", "procurement.receipt:read", ("purchase", "inventory"), lambda p: "Goods returned to supplier", "return", open_key=None),
    # ---- inventory & quality
    "inventory.stock.transferred": Kind("inventory", "ledger", "inventory.stock:read", ("overview", "inventory"), lambda p: f"Stock transferred {_n(p)}", "stock", open_key=None),
    "inventory.batch.qc_decided": Kind("inventory", "batches", "inventory.batch:read", ("overview", "inventory", "quality"),
                                       lambda p: _by(p, "to", {"RELEASED": "Batch released to stock", "REJECTED": "Batch rejected", "HOLD": "Batch placed on hold", "QUARANTINE": "Batch returned to quarantine"}, "Batch QC status changed"), "batch", open_key="batch_id"),
    "quality.inspection.created": Kind("quality", "inspections", "quality.inspection:read", ("overview", "quality"), lambda p: f"Inspection {_n(p)} opened", "qc"),
    "quality.inspection.decided": Kind("quality", "inspections", "quality.inspection:read", ("overview", "quality", "inventory"),
                                       lambda p: _by(p, "decision", {"RELEASE": f"Inspection {_n(p)} passed; batch released", "HOLD": f"Inspection {_n(p)}: batch placed on hold", "REJECT": f"Inspection {_n(p)} failed; batch rejected"}, f"Inspection {_n(p)} decided"), "qc", open_key="inspection_id"),
    "quality.coa.issued": Kind("quality", "coa", "quality.coa:read", ("quality",), lambda p: f"Certificate of analysis {_n(p)} issued", "qc", open_key=None),
    # ---- production
    "production.order.created": Kind("production", "orders", "production.order:read", ("overview", "production"), lambda p: f"Production order {_n(p)} created", "prod"),
    "production.order.released": Kind("production", "orders", "production.order:read", ("overview", "production", "inventory"), lambda p: f"Production order {_n(p)} released; materials reserved", "prod"),
    "production.order.materials_issued": Kind("production", "orders", "production.order:read", ("overview", "production", "inventory"), lambda p: f"Materials issued to {_n(p)}", "prod"),
    "production.order.stage": Kind("production", "orders", "production.order:read", ("overview", "production"), lambda p: f"{_n(p)}: stage {p.get('stage', '')} {p.get('action', '')}", "prod"),
    "production.order.completed": Kind("production", "orders", "production.order:read", ("overview", "production", "inventory", "quality"),
                                       lambda p: f"Production {_n(p)} completed; {p.get('actual_qty', '')} produced", "prod", open_key="order_id"),
    "production.order.cancelled": Kind("production", "orders", "production.order:read", ("overview", "production", "inventory"), lambda p: f"Production order {_n(p)} cancelled", "prod"),
    # ---- e-commerce
    "ecommerce.order.imported": Kind("ecommerce", "orders", "ecommerce.order:read", O, lambda p: f"{p.get('platform', 'Marketplace')} order {p.get('external_id', '')} imported as {p.get('so', 'a sales order')}", "market"),
    "ecommerce.order.exception": Kind("ecommerce", "orders", "ecommerce.order:read", ("orders",), lambda p: f"{p.get('platform', 'Marketplace')} order {p.get('external_id', '')} needs attention: {p.get('reason', '')}", "market"),
}


def emit(db: Session, kind: str, *, entity: str, entity_id: Any, number: str | None = None, actor_id: int | None = None, **fields: Any) -> None:
    """Record a committed-with-the-transaction business event. Unknown kinds are a programming error (the stream would silently drop them)."""
    if kind not in CATALOGUE:
        raise KeyError(f"unregistered activity kind {kind!r}")
    payload = {"id": entity_id, "number": number, "actor_id": actor_id, **{k: (str(v) if not isinstance(v, (int, float, bool, type(None), str)) else v) for k, v in fields.items()}}
    src = source_var.get()
    if src:
        payload["source"] = src
    outbox.add(db, event_type=kind, aggregate_type=entity, aggregate_id=entity_id, payload={k: v for k, v in payload.items() if v is not None})


def render(row: dict, names: dict[int, str]) -> dict | None:
    """An outbox row -> the item shown in the timeline / pushed on the stream. None when the type is not a user-facing activity."""
    k = CATALOGUE.get(row["event_type"])
    if k is None:
        return None
    p = row["payload"] or {}
    actor = p.get("actor_id") or p.get("approved_by")
    src = p.get("source")
    return {"id": row["id"], "event_id": str(row["event_id"]), "type": row["event_type"], "module": k.module, "title": k.title(p), "number": p.get("number"),
            "link": f"/m/{k.module}/{k.screen}" + (f"?open={p[k.open_key]}" if k.open_key and isinstance(p.get(k.open_key), int) else ""), "icon": k.icon, "at": row["created_at"].isoformat() if row.get("created_at") else None,
            "actor": names.get(actor) if actor else None, "kind": "demo" if src else "person", "source": src, "scopes": list(k.scopes)}


def names_for(db: Session, rows: list[dict]) -> dict[int, str]:
    ids = {int(a) for r in rows for a in [(r["payload"] or {}).get("actor_id") or (r["payload"] or {}).get("approved_by")] if a}
    if not ids:
        return {}
    return {r[0]: r[1] for r in db.execute(text("SELECT id, full_name FROM users WHERE id = ANY(:i)"), {"i": sorted(ids)})}


def visible(row: dict, perms: set[str]) -> bool:
    k = CATALOGUE.get(row["event_type"])
    return bool(k and k.perm in perms)
