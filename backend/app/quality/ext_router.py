from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crud import Resource, build_router
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import NotFound
from app.core.uow import run_tx
from app.masters.schemas import Code, Name, _Audited, _Base
from app.quality import ext_service as svc
from app.quality.ext_models import CertificateOfAnalysis, SamplingPlan

router = APIRouter(prefix="/quality", tags=["quality"])
DB = Depends(get_db, scope="function")
Method = Literal["ALL", "FIXED", "PERCENT", "SQRT_PLUS_ONE"]
Pct = Annotated[Decimal, Field(gt=0, le=100, max_digits=5, decimal_places=2)]


class PlanIn(_Base):
    code: Code
    name: Name
    method: Method
    fixed_qty: int | None = Field(None, gt=0)
    percent: Pct | None = None
    min_samples: int = Field(1, ge=1)
    max_samples: int | None = Field(None, ge=1)
    is_active: bool = True

    @model_validator(mode="after")
    def _consistent(self):
        if self.method == "FIXED" and not self.fixed_qty:
            raise ValueError("FIXED sampling needs fixed_qty")
        if self.method == "PERCENT" and not self.percent:
            raise ValueError("PERCENT sampling needs percent")
        if self.max_samples is not None and self.max_samples < self.min_samples:
            raise ValueError("max_samples cannot be below min_samples")
        return self


class PlanUpdate(_Base):
    name: Name | None = None
    is_active: bool | None = None


class PlanOut(_Audited, PlanIn):
    pass


router.include_router(build_router(Resource("sampling_plan", SamplingPlan, "quality.sampling_plan", PlanIn, PlanUpdate, PlanOut, ["code", "name"], ["method"], default_sort="code"),
                                   prefix="/sampling-plans", tags=["quality"]))


@router.get("/sampling-plans/{pid}/calculate")
def calculate(pid: int, lot_qty: Decimal = Query(gt=0), db: Session = DB, _: CurrentUser = Depends(require("quality.sampling_plan:read"))):
    p = db.get(SamplingPlan, pid)
    if not p:
        raise NotFound("Sampling plan")
    return {"plan": p.code, "method": p.method, "lot_qty": str(lot_qty), "sample_size": svc.sample_size(p, lot_qty)}


@router.post("/inspections/{iid}/coa", status_code=201)
def issue(iid: int, db: Session = DB, cu: CurrentUser = Depends(require("quality.coa:issue"))):
    return run_tx(db, lambda d: svc.issue_coa(d, cu, iid), name="quality.coa.issue")


@router.post("/coa/{cid}/revoke")
def revoke(cid: int, body: dict, db: Session = DB, cu: CurrentUser = Depends(require("quality.coa:revoke"))):
    return run_tx(db, lambda d: svc.revoke_coa(d, cu, cid, str(body.get("reason") or "")), name="quality.coa.revoke")


@router.get("/coa")
def list_coa(batch_id: int | None = None, status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
             db: Session = DB, _: CurrentUser = Depends(require("quality.coa:read"))):
    q = select(CertificateOfAnalysis)
    if batch_id:
        q = q.where(CertificateOfAnalysis.batch_id == batch_id)
    if status:
        q = q.where(CertificateOfAnalysis.status == status)
    rows = db.scalars(q.order_by(CertificateOfAnalysis.id.desc()).limit(page_size).offset((page - 1) * page_size)).all()
    return {"items": [{k: v for k, v in svc.coa_dict(c).items() if k != "snapshot"} for c in rows], "page": page}


@router.get("/coa/{cid}")
def get_coa(cid: int, db: Session = DB, _: CurrentUser = Depends(require("quality.coa:read"))):
    c = db.get(CertificateOfAnalysis, cid)
    if not c:
        raise NotFound("Certificate")
    return svc.coa_dict(c)
