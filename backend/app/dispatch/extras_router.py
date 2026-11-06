from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.uow import run_tx
from app.dispatch import extras as svc

router = APIRouter(prefix="/dispatch", tags=["dispatch"])
DB = Depends(get_db, scope="function")


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChargeIn(_In):
    amount: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=2)]


class ManifestIn(_In):
    courier: str = Field(min_length=2, max_length=30)


@router.get("/dashboard")
def dashboard(db: Session = DB, _: CurrentUser = Depends(require("dispatch.order:read"))):
    return svc.dashboard(db)


@router.get("/orders/{oid}/pick-list")
def pick_list(oid: int, db: Session = DB, _: CurrentUser = Depends(require("dispatch.order:read"))):
    return svc.pick_list(db, oid)


@router.get("/orders/{oid}/packing-list")
def packing_list(oid: int, db: Session = DB, _: CurrentUser = Depends(require("dispatch.order:read"))):
    return svc.packing_list(db, oid)


@router.post("/shipments/{sid}/shipping-charge")
def charge(sid: int, body: ChargeIn, db: Session = DB, cu: CurrentUser = Depends(require("dispatch.shipment:book"))):
    return run_tx(db, lambda d: svc.set_shipping_charge(d, cu, sid, body.amount), name="dispatch.shipping_charge")


@router.post("/manifests", status_code=201)
def create_manifest(body: ManifestIn, db: Session = DB, cu: CurrentUser = Depends(require("dispatch.manifest:create"))):
    return run_tx(db, lambda d: svc.create_manifest(d, cu, body.courier), name="dispatch.manifest.create")


@router.get("/manifests/{manifest_no:path}")
def manifest(manifest_no: str, db: Session = DB, _: CurrentUser = Depends(require("dispatch.manifest:read"))):
    return svc.get_manifest(db, manifest_no)


@router.post("/returns/{rid}/reverse-pickup")
def reverse_pickup(rid: int, db: Session = DB, _: CurrentUser = Depends(require("dispatch.return:create"))):
    from contextlib import nullcontext
    return {"result": svc.request_reverse_pickup(rid, lambda: nullcontext(db))}      # courier call happens with no transaction open


@router.get("/invoices/{iid}/pdf")
def invoice_pdf(iid: int, db: Session = DB, _: CurrentUser = Depends(require("accounts.sales_invoice:read"))):
    number, pdf = svc.invoice_pdf(db, iid)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{number.replace("/", "-")}.pdf"'})
