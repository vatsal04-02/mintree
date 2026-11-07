from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import NotFound
from app.core.idempotency import require_idempotency_key
from app.core.posting import idempotent_post
from app.core.uow import run_tx
from app.procurement import schemas as s
from app.procurement import service as svc
from app.procurement.models import GoodsReceipt, PurchaseOrder, PurchaseRequest

router = APIRouter(prefix="/procurement", tags=["procurement"])
DB = Depends(get_db, scope="function")


def _page(db, model, status, page, size, order):
    stmt = select(model)
    if status:
        stmt = stmt.where(model.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(order).limit(size).offset((page - 1) * size)).unique().all()
    return total, rows


# ---- purchase requests
@router.post("/purchase-requests", status_code=201)
def create_pr(body: s.PRCreate, db: Session = DB, cu: CurrentUser = Depends(require("procurement.pr:create"))):
    return run_tx(db, lambda d: svc.create_pr(d, cu, items=[i.model_dump() for i in body.items], required_by=body.required_by,
                                              notes=body.notes, department_id=body.department_id), name="procurement.pr.create")


@router.get("/purchase-requests")
def list_pr(status: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100), db: Session = DB,
            _: CurrentUser = Depends(require("procurement.pr:read"))):
    total, rows = _page(db, PurchaseRequest, status, page, page_size, PurchaseRequest.id.desc())
    return {"items": [svc.pr_dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/purchase-requests/{pr_id}")
def get_pr(pr_id: int, db: Session = DB, _: CurrentUser = Depends(require("procurement.pr:read"))):
    pr = db.get(PurchaseRequest, pr_id)
    if not pr:
        raise NotFound("Purchase request")
    return svc.pr_dict(pr)


@router.post("/purchase-requests/{pr_id}/{action}")
def pr_action(pr_id: int, action: str, body: s.Transition, db: Session = DB, cu: CurrentUser = Depends(require("procurement.pr:update"))):
    if action not in ("submit", "approve", "reject"):
        raise NotFound("Action")
    if action != "submit" and not cu.can("procurement.pr:approve"):
        from app.core.errors import Forbidden
        raise Forbidden("Missing permission: procurement.pr:approve")
    return run_tx(db, lambda d: svc.pr_transition(d, cu, pr_id, action, body.expected_version), name=f"procurement.pr.{action}")


# ---- purchase orders
@router.post("/purchase-orders", status_code=201)
def create_po(body: s.POCreate, db: Session = DB, cu: CurrentUser = Depends(require("procurement.po:create"))):
    return run_tx(db, lambda d: svc.create_po(d, cu, supplier_id=body.supplier_id, items=[i.model_dump() for i in body.items],
                                              expected_date=body.expected_date, notes=body.notes, pr_id=body.pr_id), name="procurement.po.create")


@router.get("/purchase-orders")
def list_po(status: str | None = None, supplier_id: int | None = None, q: str | None = Query(None, max_length=80), page: int = Query(1, ge=1),
            page_size: int = Query(25, ge=1, le=100), db: Session = DB, _: CurrentUser = Depends(require("procurement.po:read"))):
    stmt = select(PurchaseOrder)
    if status:
        stmt = stmt.where(PurchaseOrder.status == status)
    if supplier_id:
        stmt = stmt.where(PurchaseOrder.supplier_id == supplier_id)
    if q and q.strip():
        from sqlalchemy import or_
        from app.masters.models import Supplier
        like = "%" + q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        stmt = stmt.where(or_(PurchaseOrder.number.ilike(like), PurchaseOrder.supplier_id.in_(select(Supplier.id).where(Supplier.name.ilike(like)))))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(PurchaseOrder.id.desc()).limit(page_size).offset((page - 1) * page_size)).unique().all()
    return {"items": [svc.po_dict(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/purchase-orders/{po_id}")
def get_po(po_id: int, db: Session = DB, _: CurrentUser = Depends(require("procurement.po:read"))):
    po = db.get(PurchaseOrder, po_id)
    if not po:
        raise NotFound("Purchase order")
    return svc.po_dict(po)


@router.post("/purchase-orders/{po_id}/{action}")
def po_action(po_id: int, action: str, body: s.Transition, db: Session = DB, cu: CurrentUser = Depends(require("procurement.po:update"))):
    from app.core.errors import Forbidden
    need = {"submit": "procurement.po:update", "approve": "procurement.po:approve", "reject": "procurement.po:approve",
            "cancel": "procurement.po:cancel"}.get(action)
    if not need:
        raise NotFound("Action")
    if not cu.can(need):
        raise Forbidden(f"Missing permission: {need}")
    return run_tx(db, lambda d: svc.po_transition(d, cu, po_id, action, body.expected_version, body.reason), name=f"procurement.po.{action}")


# ---- goods receipts (idempotent posting)
@router.post("/goods-receipts", status_code=201)
def post_receipt(body: s.GRCreate, key: str = Depends(require_idempotency_key), db: Session = DB,
                 cu: CurrentUser = Depends(require("procurement.receipt:post"))):
    return idempotent_post(
        db, cu, scope="inventory.goods_receipt.post", key=key, payload=body.model_dump(mode="json"),
        effect=lambda d: _gr_effect(svc.post_goods_receipt(d, cu, po_id=body.po_id, warehouse_id=body.warehouse_id,
                                                           delivery_note_no=body.delivery_note_no, lines=[l.model_dump() for l in body.lines])))


def _gr_effect(result: dict):
    return 201, result, "goods_receipt", result["id"]


@router.get("/goods-receipts/{gr_id}")
def get_receipt(gr_id: int, db: Session = DB, _: CurrentUser = Depends(require("procurement.receipt:read"))):
    gr = db.get(GoodsReceipt, gr_id)
    if not gr:
        raise NotFound("Goods receipt")
    return {"id": gr.id, "number": gr.number, "po_id": gr.po_id, "supplier_id": gr.supplier_id, "warehouse_id": gr.warehouse_id,
            "delivery_note_no": gr.delivery_note_no, "status": gr.status, "received_at": gr.received_at,
            "lines": [{"line_no": l.line_no, "po_item_id": l.po_item_id, "item_id": l.item_id, "qty": str(l.qty), "batch_id": l.batch_id}
                      for l in gr.items]}


@router.get("/purchase-orders/{po_id}/pdf")
def po_pdf_view(po_id: int, db: Session = DB, _: CurrentUser = Depends(require("procurement.po:read"))):
    """Printable document with the Mintree logo; amounts are recomputed from the stored lines and the PDF is refused if they disagree with the stored total."""
    from fastapi.responses import Response
    from app.core.print_docs import purchase_order_pdf
    number, pdf = purchase_order_pdf(db, po_id)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{number.replace("/", "-")}.pdf"'})
