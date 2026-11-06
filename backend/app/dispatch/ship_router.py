from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.idempotency import require_idempotency_key
from app.core.posting import idempotent_post
from app.core.uow import run_tx
from app.dispatch import ship_service as svc
from app.dispatch.router import PackageIn, _In

router = APIRouter(prefix="/dispatch", tags=["dispatch"])
DB = Depends(get_db, scope="function")


class AttemptIn(_In):
    outcome: Literal["DELIVERED", "FAILED", "REFUSED", "RESCHEDULED"]
    attempted_at: datetime | None = None
    reason: str | None = Field(None, max_length=500)
    received_by: str | None = Field(None, max_length=120)
    document_id: int | None = None
    remarks: str | None = Field(None, max_length=500)


class ProofIn(_In):
    received_by: str = Field(min_length=2, max_length=120)
    document_id: int | None = None
    remarks: str | None = Field(None, max_length=500)


class PackagesIn(_In):
    packages: list[PackageIn] = Field(min_length=1, max_length=100)


@router.get("/shipments/{sid}/detail")
def detail(sid: int, db: Session = DB, _: CurrentUser = Depends(require("dispatch.shipment:read"))):
    return svc.shipment_detail(db, sid)


@router.post("/shipments/{sid}/delivery-attempts", status_code=201)
def attempt(sid: int, body: AttemptIn, key: str = Depends(require_idempotency_key), db: Session = DB, cu: CurrentUser = Depends(require("dispatch.shipment:record"))):
    return idempotent_post(db, cu, scope="dispatch.shipment.attempt", key=key, payload={"sid": sid, **body.model_dump(mode="json")},
                           effect=lambda d: (201, svc.record_attempt(d, cu, shipment_id=sid, **body.model_dump()), "shipment", sid))


@router.post("/shipments/{sid}/proof-of-delivery", status_code=201)
def proof(sid: int, body: ProofIn, db: Session = DB, cu: CurrentUser = Depends(require("dispatch.shipment:record"))):
    return run_tx(db, lambda d: svc.add_proof(d, cu, shipment_id=sid, **body.model_dump()), name="dispatch.shipment.pod")


@router.put("/shipments/{sid}/packages")
def packages(sid: int, body: PackagesIn, db: Session = DB, cu: CurrentUser = Depends(require("dispatch.shipment:record"))):
    return run_tx(db, lambda d: svc.set_packages(d, cu, shipment_id=sid, packages=[p.model_dump() for p in body.packages]), name="dispatch.shipment.packages")
