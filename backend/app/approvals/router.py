from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.approvals import service as svc
from app.approvals.models import ApprovalDelegation, CompanySetting
from app.audit import service as audit
from app.core.db import get_db
from app.core.deps import CurrentUser, current_user, require
from app.core.errors import BusinessConflict, NotFound, Unprocessable
from app.core.uow import run_tx

router = APIRouter(tags=["approvals"])
DB = Depends(get_db, scope="function")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DelegationIn(_In):
    doc_type: str = Field(min_length=1, max_length=40)
    to_user_id: int
    valid_from: date
    valid_to: date
    reason: str | None = None


class SettingIn(_In):
    value: dict | list | str | int | float | bool | None


@router.get("/approvals/pending")
def pending(db: Session = DB, cu: CurrentUser = Depends(current_user)):
    items = svc.pending_for(db, cu)
    return {"count": len(items), "items": items}


@router.get("/approvals/history/{doc_type}/{doc_id}")
def history(doc_type: str, doc_id: int, db: Session = DB, _: CurrentUser = Depends(current_user)):
    return [{"level": r.level, "user_id": r.user_id, "acting_for_user_id": r.acting_for_user_id, "decision": r.decision, "reason": r.reason, "at": r.created_at}
            for r in svc.history(db, doc_type, doc_id)]


@router.post("/approvals/delegations", status_code=201)
def delegate(body: DelegationIn, db: Session = DB, cu: CurrentUser = Depends(require("approvals.delegation:create"))):
    """Delegates the caller's own approval authority; cannot delegate to oneself or backwards in time."""
    def work(d: Session):
        if body.to_user_id == cu.id:
            raise Unprocessable("You cannot delegate to yourself")
        if body.valid_to < body.valid_from:
            raise Unprocessable("valid_to must not be before valid_from")
        row = ApprovalDelegation(**body.model_dump(), from_user_id=cu.id, created_by=cu.id)
        d.add(row)
        d.flush()
        audit.record(d, user=cu, action="delegate", entity="approval_delegation", entity_id=row.id, after=body.model_dump(mode="json"))
        return {"id": row.id, **body.model_dump(mode="json"), "from_user_id": cu.id}
    return run_tx(db, work, name="approvals.delegate")


@router.get("/approvals/delegations")
def delegations(db: Session = DB, cu: CurrentUser = Depends(require("approvals.delegation:create"))):
    rows = db.scalars(select(ApprovalDelegation).where((ApprovalDelegation.from_user_id == cu.id) | (ApprovalDelegation.to_user_id == cu.id)).order_by(ApprovalDelegation.id.desc()))
    return [{"id": r.id, "doc_type": r.doc_type, "from_user_id": r.from_user_id, "to_user_id": r.to_user_id, "valid_from": r.valid_from, "valid_to": r.valid_to, "revoked": r.revoked_at is not None} for r in rows]


@router.post("/approvals/delegations/{did}/revoke")
def revoke(did: int, db: Session = DB, cu: CurrentUser = Depends(require("approvals.delegation:create"))):
    def work(d: Session):
        r = d.scalar(select(ApprovalDelegation).where(ApprovalDelegation.id == did).with_for_update())
        if not r or r.from_user_id != cu.id:
            raise NotFound("Delegation")
        if r.revoked_at:
            raise BusinessConflict("already_revoked", "Delegation already revoked")
        from sqlalchemy import func
        r.revoked_at = func.now()
        audit.record(d, user=cu, action="revoke", entity="approval_delegation", entity_id=r.id)
        return {"id": r.id, "revoked": True}
    return run_tx(db, work, name="approvals.revoke")


@router.get("/admin/settings")
def settings(db: Session = DB, _: CurrentUser = Depends(require("admin.settings:read"))):
    stored = {s.key: s.value for s in db.scalars(select(CompanySetting))}
    return [{"key": k, "value": stored.get(k, default), "default": default, "description": desc, "customised": k in stored} for k, (default, desc) in svc.DEFAULT_SETTINGS.items()]


@router.put("/admin/settings/{key}")
def put_setting(key: str, body: SettingIn, db: Session = DB, cu: CurrentUser = Depends(require("admin.settings:update"))):
    def work(d: Session):
        if key not in svc.DEFAULT_SETTINGS:
            raise NotFound("Setting")
        if key == "procurement.two_level_threshold" and body.value is not None and (not isinstance(body.value, (int, float)) or body.value < 0):
            raise Unprocessable("Threshold must be a non-negative number or empty")
        if key == "finance.fiscal_year_start_month" and body.value not in range(1, 13):
            raise Unprocessable("Month must be 1-12")
        if key == "inventory.rotation_policy" and body.value not in ("FEFO", "FIFO"):
            raise Unprocessable("Policy must be FEFO or FIFO")
        row = d.get(CompanySetting, key)
        before = None if row is None else row.value
        if row is None:
            d.add(CompanySetting(key=key, value=body.value, description=svc.DEFAULT_SETTINGS[key][1], updated_by=cu.id))
        else:
            row.value, row.updated_by = body.value, cu.id
        audit.record(d, user=cu, action="update", entity="company_setting", entity_id=key, before={"value": before}, after={"value": body.value})
        return {"key": key, "value": body.value}
    return run_tx(db, work, name="admin.settings.update")
