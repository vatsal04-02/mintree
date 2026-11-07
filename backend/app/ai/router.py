from typing import Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from sqlalchemy import select

from app.ai import assistant, extraction
from app.ai.models import AiExtraction
from app.core.db import get_db
from app.core.deps import CurrentUser, current_user, require

router = APIRouter(prefix="/ai", tags=["ai"])
DB = Depends(get_db, scope="function")


class ChatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=20000)    # the real limit (configurable) is enforced in the service with a friendly message
    conversation_id: int | None = None


@router.get("/status")
def status(db: Session = DB, cu: CurrentUser = Depends(current_user)):
    s = assistant.status()
    return {**s, "can_assist": cu.can("ai.assistant:use"), "can_extract": cu.can("ai.extraction:use"), "can_review_proposals": cu.can("ai.proposal:review"),
            "tokens_used_today": assistant.tokens_used_today(db, cu.id)}


@router.post("/chat")
def chat(body: ChatIn, db: Session = DB, cu: CurrentUser = Depends(require("ai.assistant:use"))):
    return assistant.ask(db, cu, body.message, body.conversation_id)


@router.get("/conversations")
def conversations(db: Session = DB, cu: CurrentUser = Depends(require("ai.assistant:use"))):
    return {"items": assistant.conversations(db, cu)}


@router.get("/conversations/{conv_id}")
def conversation(conv_id: int, db: Session = DB, cu: CurrentUser = Depends(require("ai.assistant:use"))):
    return assistant.conversation(db, cu, conv_id)


# ------------------------------------------------------------------ document extraction
@router.post("/extractions", status_code=201)
async def extract(kind: Literal["INVOICE", "DELIVERY_NOTE"] = Form(...), file: UploadFile = File(...), db: Session = DB, cu: CurrentUser = Depends(require("ai.extraction:use"))):
    limit = assistant.get_settings().ai_max_document_mb * 1024 * 1024
    data = await file.read(limit + 1)             # bounded read: an oversized upload is refused without being held in memory
    return extraction.extract(db, cu, filename=file.filename or "document", content_type=(file.content_type or "").split(";")[0], data=data, kind=kind)


@router.get("/extractions")
def list_extractions(db: Session = DB, cu: CurrentUser = Depends(require("ai.extraction:use"))):
    rows = db.scalars(select(AiExtraction).where(AiExtraction.created_by == cu.id).order_by(AiExtraction.id.desc()).limit(30))
    return {"items": [extraction.view(r) for r in rows]}


@router.get("/extractions/{ext_id}")
def get_extraction(ext_id: int, db: Session = DB, cu: CurrentUser = Depends(require("ai.extraction:use"))):
    return extraction.view(extraction.get_mine(db, cu, ext_id))


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["ACCEPTED", "DISCARDED"]
    edits: dict | None = None
    posted: dict | None = None        # {"entity": "supplier_invoice", "id": 12} - what the person created from the draft


@router.post("/extractions/{ext_id}/review")
def review_extraction(ext_id: int, body: ReviewIn, db: Session = DB, cu: CurrentUser = Depends(require("ai.extraction:use"))):
    return extraction.review(db, cu, ext_id, outcome=body.outcome, edits=body.edits, posted=body.posted)
