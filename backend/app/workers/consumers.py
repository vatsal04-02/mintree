"""Event consumers. Each consumer is idempotent by construction: the de-dup marker (processed_events) and the
consumer's effects commit in ONE transaction, so redelivery (at-least-once) cannot duplicate an effect."""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.platform_models import Notification


@dataclass
class Event:
    event_id: str
    event_type: str
    payload: dict[str, Any]
    request_id: str | None


@dataclass
class Consumer:
    name: str
    event_type: str
    queue: str
    handler: Callable[..., None]
    transactional: bool = True    # False: handler does its own short transactions and external I/O (never inside the dedupe tx)


REGISTRY: dict[str, Consumer] = {}


def consumer(event_type: str, *, name: str, queue: str = "default", transactional: bool = True):
    def deco(fn):
        if name in REGISTRY:
            raise RuntimeError(f"duplicate consumer name {name}")
        REGISTRY[name] = Consumer(name, event_type, queue, fn, transactional)
        return fn
    return deco


def consumers_for(event_type: str) -> list[Consumer]:
    return [c for c in REGISTRY.values() if c.event_type == event_type]


@consumer("inventory.goods_receipt.posted", name="notify.goods_receipt_posted", queue="notifications")
def notify_goods_receipt(db: Session, ev: Event) -> None:
    db.execute(insert(Notification).values(
        event_id=ev.event_id, kind="goods_receipt_posted", title=f"Goods receipt {ev.payload.get('number', '')} posted",
        body=ev.payload, request_id=ev.request_id).on_conflict_do_nothing(index_elements=["event_id", "kind"]))


@consumer("procurement.purchase_order.approved", name="notify.po_approved", queue="notifications")
def notify_po_approved(db: Session, ev: Event) -> None:
    db.execute(insert(Notification).values(
        event_id=ev.event_id, kind="purchase_order_approved", title=f"Purchase order {ev.payload.get('number', '')} approved",
        body=ev.payload, request_id=ev.request_id).on_conflict_do_nothing(index_elements=["event_id", "kind"]))


@consumer("dispatch.shipment.requested", name="dispatch.book_shipment", queue="integrations", transactional=False)
def book_shipment(ev: Event) -> None:
    """Courier booking: network call happens OUTSIDE any DB transaction; book_shipment() is idempotent (claim -> call -> record)."""
    from app.dispatch.service import book_shipment as book
    book(int(ev.payload["shipment_id"]))


@consumer("dispatch.order.dispatched", name="notify.order_dispatched", queue="notifications")
def notify_dispatched(db: Session, ev: Event) -> None:
    db.execute(insert(Notification).values(event_id=ev.event_id, kind="order_dispatched", title=f"Order {ev.payload.get('number', '')} dispatched",
                                           body=ev.payload, request_id=ev.request_id).on_conflict_do_nothing(index_elements=["event_id", "kind"]))
