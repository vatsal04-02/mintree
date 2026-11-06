import hashlib
import re
import tempfile
from pathlib import PurePath

from pydantic import ValidationError
from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.config import get_settings
from app.core.crud import Page, translate_integrity_error
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import NotFound, Unprocessable
from app.documents import schemas as s
from app.documents.models import Document, DocumentLink, DocumentVersion
from app.documents.storage import Storage, get_storage
from sqlalchemy.exc import IntegrityError

router = APIRouter(prefix="/documents", tags=["documents"])

# Upload allow-list by extension: business documents and images only; no executables / scripts / archives.
ALLOWED_EXT = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".csv": "text/csv", ".txt": "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _get(db: Session, doc_id: int) -> Document:
    d = db.get(Document, doc_id)
    if not d or d.is_deleted:
        raise NotFound("Document")
    return d


def _clean_filename(name: str | None) -> str:
    base = PurePath((name or "file").replace("\\", "/")).name
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip(" .") or "file"
    return base[:200]


def _store(upload: UploadFile, storage: Storage) -> tuple[str, str, int, str, str]:
    """Validate and persist an upload. Returns (key, filename, size, sha256, content_type)."""
    filename = _clean_filename(upload.filename)
    ext = PurePath(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise Unprocessable(f"File type '{ext or 'unknown'}' is not allowed")
    limit = get_settings().max_upload_mb * 1024 * 1024
    sha, size = hashlib.sha256(), 0
    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as tmp:
        while chunk := upload.file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise Unprocessable(f"File exceeds the {get_settings().max_upload_mb} MB limit")
            sha.update(chunk)
            tmp.write(chunk)
        if size == 0:
            raise Unprocessable("File is empty")
        tmp.seek(0)
        key = storage.put(tmp)
    return key, filename, size, sha.hexdigest(), ALLOWED_EXT[ext]


@router.get("", response_model=Page[s.DocumentOut])
def list_documents(
    q: str | None = None, doc_type: str | None = None, entity_type: str | None = None, entity_id: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db), _: CurrentUser = Depends(require("documents.document:read")),
):
    stmt = select(Document).where(Document.is_deleted.is_(False))
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    if entity_type:
        sub = select(DocumentLink.document_id).where(DocumentLink.entity_type == entity_type)
        if entity_id:
            sub = sub.where(DocumentLink.entity_id == entity_id)
        stmt = stmt.where(Document.id.in_(sub))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Document.title.ilike(like), cast(Document.doc_type, String).ilike(like)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(Document.id.desc()).limit(page_size).offset((page - 1) * page_size)).unique().all()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.post("", response_model=s.DocumentOut, status_code=201)
def upload_document(
    file: UploadFile = File(...), title: str = Form(..., min_length=1, max_length=200),
    doc_type: str = Form("OTHER"), description: str | None = Form(None),
    entity_type: str | None = Form(None), entity_id: str | None = Form(None),
    db: Session = Depends(get_db), cu: CurrentUser = Depends(require("documents.document:create")),
    storage: Storage = Depends(get_storage),
):
    if doc_type not in s.DOC_TYPES:
        raise Unprocessable(f"Unknown document type '{doc_type}'")
    link = None
    if entity_type or entity_id:
        try:
            link = s.LinkIn(entity_type=entity_type or "", entity_id=entity_id or "")
        except ValidationError:
            raise Unprocessable("Both entity_type and entity_id are required to link a document") from None
    key, filename, size, digest, ctype = _store(file, storage)
    try:
        doc = Document(title=title.strip(), doc_type=doc_type, description=description, current_version_no=1,
                       created_by_id=cu.id, updated_by_id=cu.id)
        doc.versions.append(DocumentVersion(version_no=1, filename=filename, content_type=ctype, size_bytes=size,
                                            sha256=digest, storage_key=key, uploaded_by_id=cu.id))
        if link:
            doc.links.append(DocumentLink(entity_type=link.entity_type, entity_id=link.entity_id))
        db.add(doc)
        db.flush()
    except Exception:
        storage.delete(key)  # don't orphan the blob if the DB write fails
        raise
    audit.record(db, user=cu.user, action="create", entity="document", entity_id=doc.id,
                 after={"title": doc.title, "doc_type": doc.doc_type, "filename": filename, "sha256": digest})
    return doc


@router.get("/{doc_id}", response_model=s.DocumentOut)
def get_document(doc_id: int, db: Session = Depends(get_db), _: CurrentUser = Depends(require("documents.document:read"))):
    return _get(db, doc_id)


@router.patch("/{doc_id}", response_model=s.DocumentOut)
def update_document(doc_id: int, body: s.DocumentUpdate, db: Session = Depends(get_db),
                    cu: CurrentUser = Depends(require("documents.document:update"))):
    d = _get(db, doc_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("doc_type") and data["doc_type"] not in s.DOC_TYPES:
        raise Unprocessable("Unknown document type")
    before = {k: getattr(d, k) for k in data}
    for k, v in data.items():
        if v is not None:
            setattr(d, k, v)
    d.updated_by_id = cu.id
    audit.record(db, user=cu.user, action="update", entity="document", entity_id=d.id, before=before, after=data)
    return d


@router.post("/{doc_id}/versions", response_model=s.DocumentOut, status_code=201)
def upload_version(
    doc_id: int, file: UploadFile = File(...), note: str | None = Form(None, max_length=300),
    db: Session = Depends(get_db), cu: CurrentUser = Depends(require("documents.document:update")),
    storage: Storage = Depends(get_storage),
):
    d = db.scalar(select(Document).where(Document.id == doc_id, Document.is_deleted.is_(False)).with_for_update())
    if not d:
        raise NotFound("Document")
    key, filename, size, digest, ctype = _store(file, storage)
    try:
        next_no = d.current_version_no + 1
        d.versions.append(DocumentVersion(version_no=next_no, filename=filename, content_type=ctype, size_bytes=size,
                                          sha256=digest, storage_key=key, note=note, uploaded_by_id=cu.id))
        d.current_version_no = next_no
        d.updated_by_id = cu.id
        db.flush()
    except Exception:
        storage.delete(key)
        raise
    audit.record(db, user=cu.user, action="new_version", entity="document", entity_id=d.id,
                 after={"version_no": next_no, "filename": filename, "sha256": digest}, reason=note)
    return d


@router.get("/{doc_id}/download")
def download(doc_id: int, version: int | None = None, db: Session = Depends(get_db),
             cu: CurrentUser = Depends(require("documents.document:download")),
             storage: Storage = Depends(get_storage)):
    d = _get(db, doc_id)
    v = next((x for x in d.versions if x.version_no == (version or d.current_version_no)), None)
    if not v:
        raise NotFound("Document version")
    audit.record(db, user=cu.user, action="download", entity="document", entity_id=d.id,
                 after={"version_no": v.version_no})
    fh = storage.open(v.storage_key)

    def stream():
        try:
            while chunk := fh.read(1024 * 256):
                yield chunk
        finally:
            fh.close()

    return StreamingResponse(stream(), media_type=v.content_type, headers={
        "Content-Disposition": f'attachment; filename="{v.filename}"', "Content-Length": str(v.size_bytes),
        "X-Content-Type-Options": "nosniff"})


@router.post("/{doc_id}/links", response_model=s.DocumentOut, status_code=201)
def add_link(doc_id: int, body: s.LinkIn, db: Session = Depends(get_db),
             cu: CurrentUser = Depends(require("documents.document:update"))):
    d = _get(db, doc_id)
    if any(l.entity_type == body.entity_type and l.entity_id == body.entity_id for l in d.links):
        return d
    d.links.append(DocumentLink(entity_type=body.entity_type, entity_id=body.entity_id))
    try:
        db.flush()
    except IntegrityError as e:
        raise translate_integrity_error(e) from e
    audit.record(db, user=cu.user, action="link", entity="document", entity_id=d.id, after=body.model_dump())
    return d


@router.delete("/{doc_id}/links/{link_id}", status_code=204)
def remove_link(doc_id: int, link_id: int, db: Session = Depends(get_db),
                cu: CurrentUser = Depends(require("documents.document:update"))):
    d = _get(db, doc_id)
    link = next((l for l in d.links if l.id == link_id), None)
    if not link:
        raise NotFound("Link")
    audit.record(db, user=cu.user, action="unlink", entity="document", entity_id=d.id,
                 before={"entity_type": link.entity_type, "entity_id": link.entity_id})
    d.links.remove(link)


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: int, db: Session = Depends(get_db),
                    cu: CurrentUser = Depends(require("documents.document:delete"))):
    """Soft delete only: files and version history are retained for traceability."""
    d = _get(db, doc_id)
    d.is_deleted = True
    d.updated_by_id = cu.id
    audit.record(db, user=cu.user, action="delete", entity="document", entity_id=d.id, before={"title": d.title})
