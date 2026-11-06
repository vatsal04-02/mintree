"""Delivery attempts, proof of delivery and packages for a shipment. Courier webhooks and humans feed the same tables."""
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core import outbox
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.dispatch.ext_models import DeliveryAttempt, ProofOfDelivery, ShipmentPackage
from app.dispatch.models import Shipment
from app.dispatch.service import _maybe_deliver_order
from app.documents.models import Document
from app.notifications.service import notify

IN_FLIGHT = ("BOOKED", "PICKED_UP", "IN_TRANSIT", "OUT_FOR_DELIVERY", "FAILED_DELIVERY")


def _ship(db: Session, sid: int) -> Shipment:
    s = db.scalar(select(Shipment).where(Shipment.id == sid).with_for_update())
    if not s:
        raise NotFound("Shipment")
    return s


def record_attempt(db: Session, cu: CurrentUser, *, shipment_id: int, outcome: str, attempted_at: datetime | None, reason: str | None,
                   received_by: str | None, document_id: int | None, remarks: str | None) -> dict:
    s = _ship(db, shipment_id)
    if s.status not in IN_FLIGHT:
        raise BusinessConflict("shipment_not_in_transit", f"Delivery attempts are recorded for shipments that are on the road (this one is {s.status})")
    at = attempted_at or datetime.now(timezone.utc)
    if at.tzinfo is None:
        raise Unprocessable("attempted_at needs a timezone offset")
    if at > datetime.now(timezone.utc):
        raise Unprocessable("A delivery attempt cannot be in the future")
    if outcome != "DELIVERED" and not reason:
        raise Unprocessable("A reason is required for an attempt that did not deliver")
    if outcome == "DELIVERED" and not received_by:
        raise Unprocessable("Who received the shipment is required")
    if document_id and not db.get(Document, document_id):
        raise Unprocessable("Proof-of-delivery document does not exist")
    n = (db.scalar(text("SELECT COALESCE(max(attempt_no),0) FROM delivery_attempts WHERE shipment_id = :s"), {"s": s.id}) or 0) + 1
    att = DeliveryAttempt(shipment_id=s.id, attempt_no=n, outcome=outcome, reason=reason, attempted_at=at, source="MANUAL", recorded_by=cu.id)
    db.add(att)
    db.flush()
    pod = None
    if outcome == "DELIVERED":
        s.status, s.delivered_at = "DELIVERED", at
        pod = ProofOfDelivery(shipment_id=s.id, delivery_attempt_id=att.id, received_by=received_by, document_id=document_id, remarks=remarks, recorded_by=cu.id)
        db.add(pod)
        _maybe_deliver_order(db, s.so_id)
    elif outcome in ("FAILED", "REFUSED"):
        s.status = "FAILED_DELIVERY"
        notify(db, kind="dispatch.delivery_failed", title=f"Shipment {s.awb or s.id}: delivery {outcome.lower()}", body=reason, link=f"/dispatch/shipments/{s.id}", permission="dispatch.shipment:read", exclude_user_id=cu.id, priority="HIGH")
    db.flush()
    audit.record(db, user=cu, action=f"delivery_{outcome.lower()}", entity="shipment", entity_id=s.id, after={"attempt_no": n, "status": s.status}, reason=reason)
    outbox.add(db, event_type="dispatch.shipment.delivery_attempt", aggregate_type="shipment", aggregate_id=s.id, payload={"shipment_id": s.id, "attempt_no": n, "outcome": outcome})
    return attempt_dict(att) | {"shipment_status": s.status, "proof_of_delivery": pod_dict(pod) if pod else None}


def add_proof(db: Session, cu: CurrentUser, *, shipment_id: int, received_by: str, document_id: int | None, remarks: str | None) -> dict:
    """Attach proof to a shipment the courier already marked delivered (webhook) -- the courier gives no signature, the warehouse adds it."""
    s = _ship(db, shipment_id)
    if s.status != "DELIVERED":
        raise BusinessConflict("not_delivered", "Proof of delivery can only be added to a delivered shipment")
    if db.scalar(select(ProofOfDelivery.id).where(ProofOfDelivery.shipment_id == s.id)):
        raise BusinessConflict("pod_exists", "This shipment already has a proof of delivery")
    if document_id and not db.get(Document, document_id):
        raise Unprocessable("Proof-of-delivery document does not exist")
    att = db.scalar(select(DeliveryAttempt).where(DeliveryAttempt.shipment_id == s.id, DeliveryAttempt.outcome == "DELIVERED").order_by(DeliveryAttempt.attempt_no.desc()))
    if not att:
        raise BusinessConflict("no_delivery_attempt", "There is no delivered attempt on record to attach proof to")
    pod = ProofOfDelivery(shipment_id=s.id, delivery_attempt_id=att.id, received_by=received_by, document_id=document_id, remarks=remarks, recorded_by=cu.id)
    db.add(pod)
    db.flush()
    audit.record(db, user=cu, action="proof_of_delivery", entity="shipment", entity_id=s.id, after={"received_by": received_by})
    return pod_dict(pod)


def set_packages(db: Session, cu: CurrentUser, *, shipment_id: int, packages: list[dict]) -> dict:
    s = _ship(db, shipment_id)
    if s.status in ("DELIVERED", "RTO"):
        raise BusinessConflict("shipment_closed", f"Packages of a {s.status} shipment cannot be changed")
    if s.handed_over_at:
        raise BusinessConflict("already_handed_over", "The shipment has been handed to the courier; its packages are final")
    db.execute(text("DELETE FROM shipment_packages WHERE shipment_id = :s"), {"s": s.id})
    for n, pk in enumerate(packages, 1):
        db.add(ShipmentPackage(shipment_id=s.id, package_no=n, **pk))
    db.flush()
    audit.record(db, user=cu, action="packages", entity="shipment", entity_id=s.id, after={"count": len(packages)})
    return shipment_detail(db, s.id)


def shipment_detail(db: Session, sid: int) -> dict:
    s = db.get(Shipment, sid)
    if not s:
        raise NotFound("Shipment")
    pk = db.scalars(select(ShipmentPackage).where(ShipmentPackage.shipment_id == sid).order_by(ShipmentPackage.package_no)).all()
    at = db.scalars(select(DeliveryAttempt).where(DeliveryAttempt.shipment_id == sid).order_by(DeliveryAttempt.attempt_no)).all()
    pod = db.scalar(select(ProofOfDelivery).where(ProofOfDelivery.shipment_id == sid))
    ev = db.execute(text("SELECT event_type, occurred_at FROM courier_events WHERE shipment_id = :s ORDER BY occurred_at, id"), {"s": sid}).mappings().all()
    return {"id": s.id, "so_id": s.so_id, "shipment_no": s.shipment_no, "status": s.status, "courier": s.courier, "awb": s.awb, "delivered_at": s.delivered_at,
            "packages": [{"package_no": p.package_no, "weight_kg": None if p.weight_kg is None else str(p.weight_kg), "contents": p.contents,
                          "dimensions_cm": [None if v is None else str(v) for v in (p.length_cm, p.width_cm, p.height_cm)]} for p in pk],
            "delivery_attempts": [attempt_dict(a) for a in at], "proof_of_delivery": pod_dict(pod) if pod else None, "tracking_events": [dict(e) for e in ev]}


def attempt_dict(a: DeliveryAttempt) -> dict:
    return {"id": a.id, "attempt_no": a.attempt_no, "outcome": a.outcome, "reason": a.reason, "attempted_at": a.attempted_at, "source": a.source}


def pod_dict(p: ProofOfDelivery) -> dict:
    return {"id": p.id, "received_by": p.received_by, "document_id": p.document_id, "remarks": p.remarks, "recorded_at": p.recorded_at}
