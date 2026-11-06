from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.quality import extras as svc

router = APIRouter(prefix="/quality", tags=["quality"])
DB = Depends(get_db, scope="function")


@router.get("/inspections/{iid}/coa")
def coa(iid: int, db: Session = DB, _: CurrentUser = Depends(require("quality.inspection:read"))):
    number, pdf = svc.coa_pdf(db, iid)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="COA-{number.replace("/", "-")}.pdf"'})


@router.get("/batches/{batch_id}/dispositions")
def dispositions(batch_id: int, db: Session = DB, _: CurrentUser = Depends(require("quality.inspection:read"))):
    return svc.dispositions(db, batch_id)
