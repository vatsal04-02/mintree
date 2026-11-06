from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.uow import run_tx
from app.rnd import extras as svc
from app.rnd.models import DevelopmentProject

router = APIRouter(prefix="/rnd", tags=["rnd"])
DB = Depends(get_db, scope="function")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectIn(_In):
    code: str = Field(min_length=2, max_length=30)
    name: str = Field(min_length=2, max_length=160)
    concept: str | None = None
    product_category: str | None = None
    target_specification: str | None = None
    research_notes: str | None = None
    lead_researcher_id: int | None = None
    target_launch_date: date | None = None


class MoveIn(_In):
    status: Literal["DEVELOPMENT", "TRIALS", "APPROVED", "LAUNCHED", "CANCELLED"]


class LinkIn(_In):
    formulation_id: int


class ObsIn(_In):
    kind: Literal["STABILITY", "COMPATIBILITY", "TEXTURE", "APPEARANCE", "FRAGRANCE", "MEASUREMENT", "OTHER"]
    parameter: str | None = Field(default=None, max_length=80)
    value: str | None = Field(default=None, max_length=120)
    result: Literal["INFO", "PASS", "FAIL"] = "INFO"
    notes: str | None = None


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, db: Session = DB, cu: CurrentUser = Depends(require("rnd.project:create"))):
    from sqlalchemy.exc import IntegrityError
    from app.core.errors import BusinessConflict
    def work(d: Session):
        try:
            return svc.create_project(d, cu, body.model_dump())
        except IntegrityError as ex:
            raise BusinessConflict("duplicate_code", "A project with this code exists") from ex
    return run_tx(db, work, name="rnd.project.create")


@router.get("/projects")
def projects(status: str | None = None, db: Session = DB, _: CurrentUser = Depends(require("rnd.project:read"))):
    q = select(DevelopmentProject).order_by(DevelopmentProject.id.desc()).limit(200)
    if status:
        q = q.where(DevelopmentProject.status == status)
    return [svc.project_dict(p) for p in db.scalars(q)]


@router.post("/projects/{pid}/status")
def move(pid: int, body: MoveIn, db: Session = DB, cu: CurrentUser = Depends(require("rnd.project:update"))):
    return run_tx(db, lambda d: svc.move_project(d, cu, pid, body.status), name="rnd.project.move")


@router.post("/projects/{pid}/formulations")
def link(pid: int, body: LinkIn, db: Session = DB, cu: CurrentUser = Depends(require("rnd.project:update"))):
    return run_tx(db, lambda d: svc.link_formulation(d, cu, pid, body.formulation_id), name="rnd.project.link")


@router.post("/trials/{tid}/observations", status_code=201)
def add_obs(tid: int, body: ObsIn, db: Session = DB, cu: CurrentUser = Depends(require("rnd.trial:update"))):
    return run_tx(db, lambda d: svc.add_observation(d, cu, tid, body.model_dump()), name="rnd.trial.observe")


@router.get("/trials/{tid}/observations")
def obs(tid: int, db: Session = DB, _: CurrentUser = Depends(require("rnd.trial:read"))):
    return svc.observations(db, tid)


@router.get("/version-comparison")
def compare(a: int, b: int, db: Session = DB, _: CurrentUser = Depends(require("rnd.formula:read"))):
    return svc.compare_versions(db, a, b)
