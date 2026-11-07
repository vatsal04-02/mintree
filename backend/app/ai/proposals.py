"""Proposals: deterministic suggestions that wait for a person. The rules are plain SQL/Decimal, not the model's judgement, so every
suggestion can be explained and reproduced. Accepting runs the *normal* service as the accepting user (their permissions, their audit trail);
rejecting records the decision. Nothing here is ever applied without a click."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.ai import assistant
from app.ai.models import AiProposal
from app.audit import service as audit
from app.core.clock import business_today
from app.core.deps import CurrentUser
from app.core.errors import BusinessConflict, Forbidden, NotFound
from app.core.uow import run_tx

ORDER_UP_TO = Decimal(2)         # reorder rule: bring stock up to twice the reorder level
FOLLOW_UP_DAYS = 2
EXPIRES_AFTER_DAYS = 7
MAX_PER_KIND = 20


def _reorder_candidates(db: Session) -> list[dict]:
    return [dict(r) for r in db.execute(text("""
        SELECT i.id, i.item_code, i.name, i.reorder_level, COALESCE(sum(sb.qty_available),0) AS available, u.code AS uom
          FROM items i JOIN units_of_measure u ON u.id = i.uom_id LEFT JOIN stock_balances sb ON sb.item_id = i.id
         WHERE i.is_active AND i.kind IN ('RM','PM') AND i.reorder_level > 0
           AND NOT EXISTS (SELECT 1 FROM purchase_request_items pri JOIN purchase_requests pr ON pr.id = pri.pr_id
                            WHERE pri.item_id = i.id AND pr.status IN ('DRAFT','SUBMITTED','APPROVED'))
           AND NOT EXISTS (SELECT 1 FROM purchase_order_items poi JOIN purchase_orders po ON po.id = poi.po_id
                            WHERE poi.item_id = i.id AND po.status IN ('PENDING_APPROVAL','APPROVED','PARTIALLY_RECEIVED'))
         GROUP BY i.id, u.code HAVING COALESCE(sum(sb.qty_available),0) < i.reorder_level ORDER BY i.item_code LIMIT :n"""), {"n": MAX_PER_KIND}).mappings()]


def _overdue_candidates(db: Session) -> list[dict]:
    return [dict(r) for r in db.execute(text("""
        SELECT inv.id, inv.number, c.id AS customer_id, c.name AS customer, (:t - (inv.invoice_date + c.credit_days)) AS days_overdue,
               (inv.total - inv.amount_paid - inv.credited_amount) AS outstanding
          FROM sales_invoices inv JOIN customers c ON c.id = inv.customer_id
         WHERE inv.total - inv.amount_paid - inv.credited_amount > 0 AND inv.invoice_date + c.credit_days < :t
           AND NOT EXISTS (SELECT 1 FROM followups f WHERE f.customer_id = c.id AND f.status = 'OPEN')
         ORDER BY days_overdue DESC, inv.id LIMIT :n"""), {"t": business_today(), "n": MAX_PER_KIND}).mappings()]


def generate(db: Session, cu: CurrentUser) -> dict:
    def work(d: Session):
        made = {"REORDER": 0, "FOLLOW_UP": 0}
        d.execute(update(AiProposal).where(AiProposal.status == "PROPOSED", AiProposal.created_at < text(f"now() - interval '{EXPIRES_AFTER_DAYS} days'")).values(status="EXPIRED"))
        rows: list[dict] = []
        if cu.can("procurement.pr:create"):
            for c in _reorder_candidates(d):
                qty = (c["reorder_level"] * ORDER_UP_TO - c["available"]).quantize(Decimal("0.001"))
                rows.append({"kind": "REORDER", "dedupe_key": c["item_code"], "title": f"Raise a purchase request for {c['item_code']}",
                             "rationale": f"{c['name']}: available {c['available']} {c['uom']} is below the reorder level {c['reorder_level']}. Rule: order up to twice the reorder level, i.e. {qty} {c['uom']}. No open request or order covers it.",
                             "payload": {"item_id": c["id"], "qty": str(qty)}, "required_permission": "procurement.pr:create"})
        if cu.can("sales.followup:create") and cu.can("accounts.sales_invoice:read"):
            for c in _overdue_candidates(d):
                rows.append({"kind": "FOLLOW_UP", "dedupe_key": c["number"], "title": f"Follow up {c['customer']} on {c['number']}",
                             "rationale": f"{c['number']} has {c['outstanding']} outstanding and is {c['days_overdue']} days past its credit period. The customer has no open follow-up.",
                             "payload": {"customer_id": c["customer_id"], "subject": f"Payment follow-up for {c['number']} ({c['days_overdue']} days overdue)"}, "required_permission": "sales.followup:create"})
        for r in rows:
            res = d.execute(pg_insert(AiProposal).values(**r).on_conflict_do_nothing().returning(AiProposal.id)).first()      # the partial unique index makes this safe to repeat
            if res:
                made[r["kind"]] += 1
        audit.record(d, user=cu, action="ai.proposals_generated", entity="ai_proposal", after=made)
        return made
    made = run_tx(db, work, name="ai.proposals.generate")
    return {"created": made}


def view(p: AiProposal) -> dict:
    return {"id": p.id, "kind": p.kind, "title": p.title, "rationale": p.rationale, "payload": p.payload, "status": p.status, "result": p.result,
            "required_permission": p.required_permission, "created_at": p.created_at, "decided_at": p.decided_at}


def visible(db: Session, cu: CurrentUser, status: str | None = "PROPOSED") -> list[dict]:
    q = select(AiProposal).order_by(AiProposal.id.desc()).limit(100)
    if status:
        q = q.where(AiProposal.status == status)
    return [view(p) for p in db.scalars(q) if cu.can(p.required_permission)]       # you only see what you could do yourself


def _lock(d: Session, cu: CurrentUser, pid: int) -> AiProposal:
    p = d.scalar(select(AiProposal).where(AiProposal.id == pid).with_for_update())
    if p is None or not cu.can(p.required_permission):          # not found and not permitted look the same
        raise NotFound("Proposal")
    if p.status != "PROPOSED":
        raise BusinessConflict("proposal_decided", f"This proposal was already {p.status.lower()}.")
    return p


class StaleProposal(Exception):
    def __init__(self, pid: int):
        self.pid = pid


def _withdraw(d: Session, pid: int) -> None:
    d.execute(update(AiProposal).where(AiProposal.id == pid, AiProposal.status == "PROPOSED").values(status="EXPIRED", decided_at=text("now()")))


def accept(db: Session, cu: CurrentUser, pid: int) -> dict:
    try:
        return _accept(db, cu, pid)
    except StaleProposal:        # the failed attempt rolled back; record the withdrawal in its own transaction, then tell the caller
        run_tx(db, lambda d: _withdraw(d, pid), name="ai.proposals.withdraw")
        raise BusinessConflict("proposal_stale", "Stock is no longer below the reorder level; the suggestion was withdrawn.") from None


def _accept(db: Session, cu: CurrentUser, pid: int) -> dict:
    def work(d: Session):
        p = _lock(d, cu, pid)
        if p.kind == "REORDER":
            from app.procurement.service import create_pr
            now = d.execute(text("""SELECT COALESCE(sum(sb.qty_available),0) FROM stock_balances sb WHERE sb.item_id = :i"""), {"i": p.payload["item_id"]}).scalar()
            lvl = d.execute(text("SELECT reorder_level FROM items WHERE id = :i"), {"i": p.payload["item_id"]}).scalar()
            if lvl is None or now >= lvl:
                raise StaleProposal(p.id)
            r = create_pr(d, cu, items=[{"item_id": p.payload["item_id"], "qty": Decimal(p.payload["qty"])}], required_by=None, notes=f"Created from AI suggestion #{p.id}: {p.rationale}"[:500], department_id=None)
            p.result = {"entity": "purchase_request", "id": r["id"], "number": r["number"]}
        elif p.kind == "FOLLOW_UP":
            from app.sales.service import add_followup
            r = add_followup(d, cu, {"subject": p.payload["subject"], "due_date": business_today() + timedelta(days=FOLLOW_UP_DAYS), "customer_id": p.payload["customer_id"],
                                     "lead_id": None, "assigned_to": cu.id, "notes": f"Created from AI suggestion #{p.id}"})
            p.result = {"entity": "followup", "id": r["id"]}
        else:
            raise BusinessConflict("unknown_proposal_kind", "Unsupported proposal.")
        p.status, p.decided_by, p.decided_at = "ACCEPTED", cu.id, d.scalar(text("SELECT now()"))
        audit.record(d, user=cu, action="ai.proposal_accepted", entity="ai_proposal", entity_id=p.id, after={"kind": p.kind, "result": p.result})
        return view(p)
    return run_tx(db, work, name="ai.proposals.accept")


def reject(db: Session, cu: CurrentUser, pid: int) -> dict:
    def work(d: Session):
        p = _lock(d, cu, pid)
        p.status, p.decided_by, p.decided_at = "REJECTED", cu.id, d.scalar(text("SELECT now()"))
        audit.record(d, user=cu, action="ai.proposal_rejected", entity="ai_proposal", entity_id=p.id, after={"kind": p.kind})
        return view(p)
    return run_tx(db, work, name="ai.proposals.reject")
