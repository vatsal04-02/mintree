from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import NotFound
from app.core.uow import run_tx
from app.rnd import service as svc
from app.rnd.models import Formulation, FormulationVersion

router = APIRouter(prefix="/rnd", tags=["rnd"])
DB = Depends(get_db, scope="function")
Q4 = Annotated[Decimal, Field(gt=0, max_digits=15, decimal_places=4)]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Ing(_In):
    item_id: int
    qty: Q4


class FormulationIn(_In):
    code: str = Field(min_length=2, max_length=40)
    name: str = Field(min_length=1, max_length=160)
    product_item_id: int
    batch_size: Q4
    process_instructions: str | None = None
    ingredients: list[Ing] = Field(min_length=1, max_length=100)


class DraftUpdate(_In):
    batch_size: Q4 | None = None
    process_instructions: str | None = None
    ingredients: list[Ing] | None = None


class NewVersion(_In):
    from_version_no: int = Field(ge=1)
    revision_note: str = Field(min_length=3, max_length=500)


class TrialIn(_In):
    quantity: Q4


class TrialConclude(_In):
    status: Literal["PASSED", "FAILED"]
    observations: str = Field(min_length=3)
    stability_note: str | None = None


class Reason(_In):
    reason: str = Field(min_length=3, max_length=500)


@router.post("/formulations", status_code=201)
def create(body: FormulationIn, db: Session = DB, cu: CurrentUser = Depends(require("rnd.formula:create"))):
    return run_tx(db, lambda d: svc.create_formulation(d, cu, code=body.code, name=body.name, product_item_id=body.product_item_id, batch_size=body.batch_size,
                                                       process_instructions=body.process_instructions, ingredients=[i.model_dump() for i in body.ingredients]),
                  name="rnd.formulation.create")


@router.get("/formulations")
def list_(db: Session = DB, _: CurrentUser = Depends(require("rnd.formula:read"))):
    return [svc.formulation_dict(f) for f in db.scalars(select(Formulation).order_by(Formulation.id.desc()).limit(200)).unique()]


@router.get("/formulations/{fid}")
def get(fid: int, db: Session = DB, _: CurrentUser = Depends(require("rnd.formula:read"))):
    f = db.get(Formulation, fid)
    if not f:
        raise NotFound("Formulation")
    return svc.formulation_dict(f)


@router.post("/formulations/{fid}/versions", status_code=201)
def new_version(fid: int, body: NewVersion, db: Session = DB, cu: CurrentUser = Depends(require("rnd.formula:create"))):
    return run_tx(db, lambda d: svc.new_version(d, cu, fid, body.from_version_no, body.revision_note), name="rnd.version.create")


@router.patch("/versions/{vid}")
def update_draft(vid: int, body: DraftUpdate, db: Session = DB, cu: CurrentUser = Depends(require("rnd.formula:update"))):
    return run_tx(db, lambda d: svc.update_draft(d, cu, vid, batch_size=body.batch_size, process_instructions=body.process_instructions,
                                                 ingredients=None if body.ingredients is None else [i.model_dump() for i in body.ingredients]), name="rnd.version.update")


@router.post("/versions/{vid}/submit")
def submit(vid: int, db: Session = DB, cu: CurrentUser = Depends(require("rnd.formula:update"))):
    return run_tx(db, lambda d: svc.submit(d, cu, vid), name="rnd.version.submit")


@router.post("/versions/{vid}/approve")
def approve(vid: int, db: Session = DB, cu: CurrentUser = Depends(require("rnd.formula:approve"))):
    return run_tx(db, lambda d: svc.approve(d, cu, vid), name="rnd.version.approve")


@router.post("/versions/{vid}/reject")
def reject(vid: int, body: Reason, db: Session = DB, cu: CurrentUser = Depends(require("rnd.formula:approve"))):
    return run_tx(db, lambda d: svc.reject(d, cu, vid, body.reason), name="rnd.version.reject")


@router.post("/versions/{vid}/trials", status_code=201)
def trial(vid: int, body: TrialIn, db: Session = DB, cu: CurrentUser = Depends(require("rnd.trial:create"))):
    return run_tx(db, lambda d: svc.create_trial(d, cu, vid, body.quantity), name="rnd.trial.create")


@router.post("/trials/{tid}/conclude")
def conclude(tid: int, body: TrialConclude, db: Session = DB, cu: CurrentUser = Depends(require("rnd.trial:update"))):
    return run_tx(db, lambda d: svc.conclude_trial(d, cu, tid, body.status, body.observations, body.stability_note), name="rnd.trial.conclude")
