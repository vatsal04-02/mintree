from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.ai import assistant
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
