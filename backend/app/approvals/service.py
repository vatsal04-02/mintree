"""Approval support shared by documents: settings lookup, delegation, decision history, and a unified pending-approvals inbox."""
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.approvals.models import ApprovalDelegation, ApprovalRecord, CompanySetting
from app.core.deps import CurrentUser

DEFAULT_SETTINGS = {
    "company.name": ("Mintree", "Legal/trading name shown on documents"),
    "company.gstin": (None, "Company GSTIN printed on invoices"),
    "company.state_code": ("27", "Place-of-supply state code of the company (intra/inter-state GST)"),
    "company.address": (None, "Registered address printed on invoices"),
    "finance.fiscal_year_start_month": (4, "1-12; Indian fiscal year starts in April"),
    "procurement.two_level_threshold": (None, "PO total at or above which two distinct approvers are required; empty = single level"),
    "inventory.rotation_policy": ("FEFO", "FEFO or FIFO for batch allocation"),
    "costing.overhead_pct": (0, "Reporting-only overhead allocation as a percentage of COGS (not posted to the ledger)"),
    "quality.near_expiry_days": (90, "Batches expiring within this many days are flagged"),
}


def get_setting(db: Session, key: str):
    row = db.get(CompanySetting, key)
    return row.value if row else DEFAULT_SETTINGS.get(key, (None,))[0]


def effective_roles(db: Session, cu: CurrentUser, doc_type: str) -> tuple[set[str], dict[str, int]]:
    """Roles the user can act with today: their own plus those of users who delegated `doc_type` approval to them. -> (roles, role->delegator id)"""
    roles = set(cu.role_codes)
    via: dict[str, int] = {}
    for d in db.scalars(select(ApprovalDelegation).where(ApprovalDelegation.to_user_id == cu.id, ApprovalDelegation.doc_type.in_([doc_type, "*"]),
                                                          ApprovalDelegation.revoked_at.is_(None), ApprovalDelegation.valid_from <= date.today(),
                                                          ApprovalDelegation.valid_to >= date.today())):
        for (code,) in db.execute(text("SELECT r.code FROM roles r JOIN user_roles ur ON ur.role_id = r.id WHERE ur.user_id = :u"), {"u": d.from_user_id}):
            if code not in roles:
                via[code] = d.from_user_id
            roles.add(code)
    return roles, via


def history(db: Session, doc_type: str, doc_id: int, cycle: int | None = None) -> list[ApprovalRecord]:
    q = select(ApprovalRecord).where(ApprovalRecord.doc_type == doc_type, ApprovalRecord.doc_id == doc_id)
    if cycle is not None:
        q = q.where(ApprovalRecord.cycle == cycle)
    return list(db.scalars(q.order_by(ApprovalRecord.id)))


def current_cycle(db: Session, doc_type: str, doc_id: int) -> int:
    return 1 + db.scalar(text("SELECT count(*) FROM approval_records WHERE doc_type = :t AND doc_id = :i AND decision = 'REJECTED'"), {"t": doc_type, "i": doc_id})


def pending_for(db: Session, cu: CurrentUser) -> list[dict]:
    """Everything awaiting THIS user's decision (by permission), oldest first. Each row links to the record it concerns."""
    out: list[dict] = []
    has = cu.permissions.__contains__

    def add(kind, rows, link, label):
        for r in rows:
            out.append({"type": kind, "id": r[0], "number": r[1], "amount": None if r[2] is None else str(r[2]), "since": r[3], "link": f"{link}/{r[0]}", "summary": label})

    q = lambda sql, **p: db.execute(text(sql), {"u": cu.id, **p}).all()
    if has("procurement.pr:approve"):
        add("purchase_request", q("SELECT id, number, NULL::numeric, created_at FROM purchase_requests WHERE status = 'SUBMITTED' AND requested_by <> :u"), "/procurement/purchase-requests", "Purchase request awaiting approval")
    if has("procurement.po:approve"):
        add("purchase_order", q("""SELECT p.id, p.number, p.total_amount, p.created_at FROM purchase_orders p WHERE p.status = 'PENDING_APPROVAL' AND p.created_by <> :u
                                    AND NOT EXISTS (SELECT 1 FROM approval_records a WHERE a.doc_type = 'purchase_order' AND a.doc_id = p.id AND a.user_id = :u AND a.decision = 'APPROVED'
                                          AND a.cycle = 1 + (SELECT count(*) FROM approval_records x WHERE x.doc_type = 'purchase_order' AND x.doc_id = p.id AND x.decision = 'REJECTED'))"""), "/procurement/purchase-orders", "Purchase order awaiting approval")
    if has("procurement.rfq:award"):
        add("rfq", q("SELECT id, number, NULL::numeric, created_at FROM rfqs WHERE status = 'SENT' AND EXISTS (SELECT 1 FROM quotations q WHERE q.rfq_id = rfqs.id AND q.status = 'SUBMITTED')"), "/procurement/rfqs", "RFQ has quotations ready to compare and award")
    if has("rnd.formula:approve"):
        add("formulation_version", q("SELECT id, 'v' || version_no::text, NULL::numeric, created_at FROM formulation_versions WHERE status = 'SUBMITTED' AND created_by <> :u"), "/rnd/versions", "Formulation version awaiting approval")
    if has("production.bom:approve"):
        add("bom", q("SELECT id, 'BOM-' || id::text, NULL::numeric, created_at FROM boms WHERE status = 'DRAFT' AND created_by <> :u"), "/production/boms", "Manual BOM awaiting approval")
    if has("quality.inspection:decide"):
        add("inspection", q("SELECT id, number, NULL::numeric, created_at FROM inspections WHERE status IN ('PENDING','IN_PROGRESS') AND EXISTS (SELECT 1 FROM inspection_results r WHERE r.inspection_id = inspections.id)"), "/quality/inspections", "Inspection with results awaiting a release decision")
    if has("quality.deviation:close"):
        add("deviation", q("SELECT id, number, NULL::numeric, created_at FROM deviations WHERE status = 'INVESTIGATION'"), "/quality/deviations", "Deviation under investigation")
    if has("branding.content:approve"):
        add("content", q("SELECT id, title, NULL::numeric, created_at FROM content_calendar WHERE status = 'IN_REVIEW' AND created_by <> :u"), "/branding/content", "Content awaiting approval")
    if has("maintenance.workorder:verify"):
        add("maintenance_work_order", q("SELECT id, number, NULL::numeric, created_at FROM maintenance_work_orders WHERE status = 'DONE' AND assigned_to IS DISTINCT FROM :u"), "/maintenance/work-orders", "Completed maintenance awaiting verification")
    out.sort(key=lambda r: r["since"])
    return out
