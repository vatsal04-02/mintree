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


class ScanIn(_In):
    code: str = Field(min_length=1, max_length=120)
    qty: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=3)] = Decimal(1)


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


@router.get("/orders/{oid}/scan-status")
def scan_status(oid: int, db: Session = DB, _: CurrentUser = Depends(require("dispatch.order:read"))):
    return svc.scan_progress(db, oid)


@router.post("/orders/{oid}/scan")
def scan(oid: int, body: ScanIn, db: Session = DB, cu: CurrentUser = Depends(require("dispatch.order:pack"))):
    return run_tx(db, lambda d: svc.scan_pick(d, cu, oid, body.code, body.qty), name="dispatch.pick_scan")


@router.post("/manifests/{manifest_no:path}/scan")
def scan_handover(manifest_no: str, body: ScanIn, db: Session = DB, cu: CurrentUser = Depends(require("dispatch.manifest:create"))):
    return run_tx(db, lambda d: svc.scan_manifest(d, cu, manifest_no, body.code), name="dispatch.handover_scan")


@router.get("/orders/{oid}/shipment-lines")
def shipment_lines(oid: int, db: Session = DB, _: CurrentUser = Depends(require("dispatch.return:read"))):
    return svc.shipment_lines(db, oid)


@router.get("/shipments/{sid}/challan")
def shipment_challan(sid: int, db: Session = DB, _: CurrentUser = Depends(require("dispatch.shipment:read"))):
    from app.core.print_docs import challan_pdf
    number, pdf = challan_pdf(db, sid)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{number.replace("/", "-")}-challan.pdf"'})
