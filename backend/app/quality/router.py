from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import NotFound
from app.core.idempotency import require_idempotency_key
from app.core.posting import idempotent_post
from app.core.uow import run_tx
from app.quality import service as svc
from app.quality.models import Deviation, Inspection, QualitySpec

router = APIRouter(prefix="/quality", tags=["quality"])
DB = Depends(get_db, scope="function")
Num = Annotated[Decimal, Field(max_digits=15, decimal_places=4)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TestIn(_In):
    test_name: str = Field(min_length=1, max_length=80)
    method: str | None = None
    unit: str | None = None
    min_value: Num | None = None
    max_value: Num | None = None
    is_pass_fail: bool = False
    mandatory: bool = True


class SpecIn(_In):
    item_id: int
    stage: Literal["INCOMING", "IN_PROCESS", "FINISHED"]
    tests: list[TestIn] = Field(min_length=1, max_length=100)
    sampling_plan_id: int | None = None


class InspectionIn(_In):
    batch_id: int
    stage: Literal["INCOMING", "IN_PROCESS", "FINISHED"]


class ResultIn(_In):
    test_name: str
    value: Num | None = None
    passed: bool | None = None
    observation: str | None = Field(None, max_length=500)


class DecisionIn(_In):
    decision: Literal["RELEASE", "REJECT", "HOLD"]
    reason: str | None = Field(None, max_length=500)


class DeviationIn(_In):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(min_length=3)
    classification: Literal["MINOR", "MAJOR", "CRITICAL"]
    batch_id: int | None = None


class RootCause(_In):
    root_cause: str = Field(min_length=3)


class CapaIn(_In):
    kind: Literal["CORRECTIVE", "PREVENTIVE"]
    description: str = Field(min_length=3)
    responsible_id: int
    due_date: date


@router.post("/specs", status_code=201)
def create_spec(body: SpecIn, db: Session = DB, cu: CurrentUser = Depends(require("quality.spec:create"))):
    return run_tx(db, lambda d: svc.create_spec(d, cu, item_id=body.item_id, stage=body.stage, tests=[t.model_dump() for t in body.tests], sampling_plan_id=body.sampling_plan_id), name="quality.spec.create")


@router.get("/specs")
def specs(item_id: int | None = None, db: Session = DB, _: CurrentUser = Depends(require("quality.spec:read"))):
    q = select(QualitySpec).order_by(QualitySpec.id.desc()).limit(200)
    if item_id:
        q = q.where(QualitySpec.item_id == item_id)
    return [svc.spec_dict(s) for s in db.scalars(q).unique()]


@router.post("/inspections", status_code=201)
def create_inspection(body: InspectionIn, db: Session = DB, cu: CurrentUser = Depends(require("quality.inspection:create"))):
    return run_tx(db, lambda d: svc.create_inspection(d, cu, body.batch_id, body.stage), name="quality.inspection.create")


@router.get("/inspections")
def inspections(status: str | None = None, batch_id: int | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
                db: Session = DB, _: CurrentUser = Depends(require("quality.inspection:read"))):
    q = select(Inspection).order_by(Inspection.id.desc()).limit(page_size).offset((page - 1) * page_size)
    if status:
        q = q.where(Inspection.status == status)
    if batch_id:
        q = q.where(Inspection.batch_id == batch_id)
    return {"items": [svc.inspection_dict(db, i) for i in db.scalars(q)], "page": page}


@router.get("/inspections/{iid}")
def inspection(iid: int, db: Session = DB, _: CurrentUser = Depends(require("quality.inspection:read"))):
    i = db.get(Inspection, iid)
    if not i:
        raise NotFound("Inspection")
    return svc.inspection_dict(db, i)


@router.post("/inspections/{iid}/results", status_code=201)
def result(iid: int, body: ResultIn, db: Session = DB, cu: CurrentUser = Depends(require("quality.inspection:record"))):
    return run_tx(db, lambda d: svc.record_result(d, cu, iid, test_name=body.test_name, value=body.value, passed=body.passed, observation=body.observation),
                  name="quality.inspection.result")


@router.post("/inspections/{iid}/decide")
def decide(iid: int, body: DecisionIn, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("quality.inspection:decide"))):
    return idempotent_post(db, cu, scope="quality.inspection.decide", key=key, payload={"id": iid, **body.model_dump()},
                           effect=lambda d: (200, svc.decide(d, cu, iid, body.decision, body.reason), "inspection", iid))


@router.post("/deviations", status_code=201)
def raise_dev(body: DeviationIn, db: Session = DB, cu: CurrentUser = Depends(require("quality.deviation:create"))):
    return run_tx(db, lambda d: svc.raise_deviation(d, cu, title=body.title, description=body.description, classification=body.classification, batch_id=body.batch_id),
                  name="quality.deviation.raise")


@router.get("/deviations")
def deviations(status: str | None = None, db: Session = DB, _: CurrentUser = Depends(require("quality.deviation:read"))):
    q = select(Deviation).order_by(Deviation.id.desc()).limit(100)
    if status:
        q = q.where(Deviation.status == status)
    return [svc.deviation_dict(d) for d in db.scalars(q).unique()]


@router.post("/deviations/{did}/investigate")
def investigate(did: int, body: RootCause, db: Session = DB, cu: CurrentUser = Depends(require("quality.deviation:update"))):
    return run_tx(db, lambda d: svc.investigate(d, cu, did, body.root_cause), name="quality.deviation.investigate")


@router.post("/deviations/{did}/capa", status_code=201)
def add_capa(did: int, body: CapaIn, db: Session = DB, cu: CurrentUser = Depends(require("quality.capa:create"))):
    return run_tx(db, lambda d: svc.add_capa(d, cu, did, kind=body.kind, description=body.description, responsible_id=body.responsible_id, due_date=body.due_date),
                  name="quality.capa.add")


@router.post("/capa/{cid}/complete")
def capa_complete(cid: int, db: Session = DB, cu: CurrentUser = Depends(require("quality.capa:update"))):
    return run_tx(db, lambda d: svc.capa_step(d, cu, cid, "complete"), name="quality.capa.complete")


@router.post("/capa/{cid}/verify")
def capa_verify(cid: int, db: Session = DB, cu: CurrentUser = Depends(require("quality.capa:verify"))):
    return run_tx(db, lambda d: svc.capa_step(d, cu, cid, "verify"), name="quality.capa.verify")


@router.post("/deviations/{did}/close")
def close(did: int, db: Session = DB, cu: CurrentUser = Depends(require("quality.deviation:close"))):
    return run_tx(db, lambda d: svc.close_deviation(d, cu, did), name="quality.deviation.close")
