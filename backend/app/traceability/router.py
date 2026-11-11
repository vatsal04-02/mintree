"""Traceability API (Product 360 and Batch 360). Read-only, except that running an impact analysis writes one audit event. No endpoint here changes stock, production or quality records."""
from fastapi import APIRouter, Depends, Query

from app.audit import service as audit
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.uow import run_tx
from app.traceability import service as svc

router = APIRouter(prefix="/traceability", tags=["traceability"])
DB = Depends(get_db, scope="function")
READ = require("inventory.batch:read")
Page = Query(1, ge=1)
Size = Query(25, ge=1, le=100)


def _mask(cu: CurrentUser) -> svc.Mask:
    return svc.Mask(cu.can("masters.supplier:read"), cu.can("masters.customer:read"))


# ---- batches
@router.get("/batches")
def batches(q: str | None = None, item_id: int | None = None, kind: str | None = Query(None, pattern="^(RM|PM|BULK|FG)$"), qc_status: str | None = None,
            page: int = Page, page_size: int = Size, db=DB, _: CurrentUser = Depends(READ)):
    return svc.batch_list(db, q, item_id, kind, qc_status, page, page_size)


@router.get("/batches/{batch_id}")
def batch(batch_id: int, db=DB, cu: CurrentUser = Depends(READ)):
    return svc.detail(db, batch_id, _mask(cu))


@router.get("/batches/{batch_id}/graph")
def batch_graph(batch_id: int, direction: str = Query("both", pattern="^(backward|forward|both)$"), max_depth: int = Query(svc.MAX_DEPTH, ge=1, le=svc.MAX_DEPTH),
                db=DB, cu: CurrentUser = Depends(READ)):
    return svc.graph(db, batch_id, direction, _mask(cu), max_depth)


@router.get("/batches/{batch_id}/movements")
def batch_movements(batch_id: int, page: int = Page, page_size: int = Size, db=DB, _: CurrentUser = Depends(READ)):
    return svc.movements(db, batch_id, page, page_size)


@router.get("/batches/{batch_id}/history")
def batch_history(batch_id: int, db=DB, _: CurrentUser = Depends(READ)):
    svc.batch_row(db, batch_id)
    return {"items": svc.history(db, batch_id)}


@router.post("/batches/{batch_id}/impact-analysis")
def impact_analysis(batch_id: int, max_depth: int = Query(svc.MAX_DEPTH, ge=1, le=svc.MAX_DEPTH), db=DB, cu: CurrentUser = Depends(require("inventory.batch:read", "inventory.batch:qc_release"))):
    """Who and what is downstream of a batch. Authorised users only, and the run itself is audited; it changes nothing. Holds and recalls are separate, explicit actions through the existing QC decision workflow."""
    def run(d):
        out = svc.impact(d, batch_id, _mask(cu), max_depth)
        audit.record(d, user=cu, action="impact_analysis", entity="batch_impact", entity_id=batch_id,
                     after={"batch_no": out["suspect"]["batch_no"], **{k: v for k, v in out["summary"].items() if isinstance(v, int)}}, reason="Batch impact analysis run")
        return out
    return run_tx(db, run, name="traceability.impact")


# ---- products
@router.get("/products")
def products(q: str | None = None, kind: str | None = Query(None, pattern="^(RM|PM|BULK|FG)$"), page: int = Page, page_size: int = Size, db=DB, _: CurrentUser = Depends(READ)):
    return svc.product_list(db, q, kind, page, page_size)


@router.get("/products/{item_id}")
def product(item_id: int, db=DB, _: CurrentUser = Depends(READ)):
    return svc.product_summary(db, item_id)


@router.get("/products/{item_id}/formulations")
def product_formulations(item_id: int, db=DB, _: CurrentUser = Depends(READ)):
    return svc.product_formulations(db, item_id)


@router.get("/products/{item_id}/materials")
def product_materials(item_id: int, db=DB, cu: CurrentUser = Depends(READ)):
    return svc.product_materials(db, item_id, _mask(cu))


@router.get("/products/{item_id}/production")
def product_production(item_id: int, page: int = Page, page_size: int = Size, db=DB, _: CurrentUser = Depends(READ)):
    return svc.product_production(db, item_id, page, page_size)


@router.get("/products/{item_id}/batches")
def product_batches(item_id: int, page: int = Page, page_size: int = Size, db=DB, _: CurrentUser = Depends(READ)):
    return svc.product_batches(db, item_id, page, page_size)


@router.get("/products/{item_id}/quality")
def product_quality(item_id: int, page: int = Page, page_size: int = Size, db=DB, _: CurrentUser = Depends(READ)):
    return svc.product_quality(db, item_id, page, page_size)


@router.get("/products/{item_id}/sales")
def product_sales(item_id: int, page: int = Page, page_size: int = Size, db=DB, cu: CurrentUser = Depends(READ)):
    return svc.product_sales(db, item_id, _mask(cu), page, page_size)


@router.get("/products/{item_id}/history")
def product_history(item_id: int, db=DB, _: CurrentUser = Depends(READ)):
    return {"items": svc.product_history(db, item_id)}