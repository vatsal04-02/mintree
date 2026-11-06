"""Generic permission-gated, audited CRUD router used by master-data resources.

Rules enforced here for every resource:
  * backend permission checks (`<perm>:read|create|update|delete|export`)
  * every create/update/deactivate writes an audit row in the same transaction
  * records are never hard-deleted; DELETE deactivates (is_active = false)
  * DB constraint violations become 409/422 responses instead of 500s
"""
import csv
import io
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from psycopg import errors as pgerr
from pydantic import BaseModel
from sqlalchemy import String, asc, cast, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import service as audit
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import Conflict, NotFound, Unprocessable

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


@dataclass
class Resource:
    entity: str  # audit entity name, e.g. "item"
    model: type
    perm: str  # permission prefix, e.g. "masters.item"
    create_schema: type[BaseModel]
    update_schema: type[BaseModel]
    read_schema: type[BaseModel]
    search_cols: list[str]
    filter_cols: list[str] = field(default_factory=list)
    default_sort: str = "id"
    has_active_flag: bool = True
    exportable: bool = False
    # Hooks
    read_transform: Callable[[dict, CurrentUser], dict] | None = None
    write_guard: Callable[[dict, CurrentUser, bool], None] | None = None  # (data, user, creating)
    audit_exclude: set[str] = field(default_factory=lambda: {"created_at", "updated_at"})


def translate_integrity_error(exc: IntegrityError) -> Exception:
    orig = exc.orig
    if isinstance(orig, pgerr.UniqueViolation):
        return Conflict("A record with the same unique value (code / number) already exists.")
    if isinstance(orig, pgerr.ForeignKeyViolation):
        return Unprocessable("A referenced record does not exist or is still in use.")
    if isinstance(orig, pgerr.CheckViolation):
        return Unprocessable(f"Value violates a business rule ({getattr(orig.diag, 'constraint_name', 'check')}).")
    if isinstance(orig, pgerr.NotNullViolation):
        return Unprocessable("A required field is missing.")
    return Unprocessable("The data could not be saved.")


def _csv_safe(v: Any) -> Any:
    # Neutralise spreadsheet formula injection.
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def build_router(r: Resource, *, prefix: str, tags: list[str]) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=tags)
    Model = r.model
    Read = r.read_schema

    def to_out(obj: Any, cu: CurrentUser) -> dict:
        data = Read.model_validate(obj, from_attributes=True).model_dump()
        return r.read_transform(data, cu) if r.read_transform else data

    def snap(obj: Any) -> dict:
        return audit.snapshot(obj, r.audit_exclude)

    def get_or_404(db: Session, id_: int) -> Any:
        obj = db.get(Model, id_)
        if not obj:
            raise NotFound(r.entity.replace("_", " ").capitalize())
        return obj

    def flush(db: Session) -> None:
        try:
            db.flush()
        except IntegrityError as e:
            raise translate_integrity_error(e) from e

    def base_query(q, is_active, include_inactive, filters: dict[str, str | None]):
        stmt = select(Model)
        if r.has_active_flag:
            if is_active is not None:  # explicit filter wins
                stmt = stmt.where(Model.is_active == is_active)
            elif not include_inactive:
                stmt = stmt.where(Model.is_active.is_(True))
        if q:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(*[cast(getattr(Model, c), String).ilike(like) for c in r.search_cols]))
        for col, val in filters.items():
            if val not in (None, ""):
                stmt = stmt.where(getattr(Model, col) == val)
        return stmt

    def sorted_stmt(stmt, sort: str):
        desc_ = sort.startswith("-")
        name = sort.lstrip("-")
        col = getattr(Model, name, None)
        if col is None or not hasattr(col, "property") or not hasattr(col.property, "columns"):
            name, desc_, col = r.default_sort.lstrip("-"), r.default_sort.startswith("-"), getattr(Model, r.default_sort.lstrip("-"))
        return stmt.order_by(desc(col) if desc_ else asc(col), asc(Model.id))

    def filter_deps():
        # Filters are passed as ?<col>=<value>; declared dynamically via request.query_params.
        from fastapi import Request

        def dep(request: Request) -> dict[str, str | None]:
            return {c: request.query_params.get(c) for c in r.filter_cols}

        return dep

    @router.get("", response_model=Page[Read])
    def list_(
        q: str | None = None,
        is_active: bool | None = None,
        include_inactive: bool = False,
        sort: str = r.default_sort,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=500),
        filters: dict = Depends(filter_deps()),
        db: Session = Depends(get_db, scope="function"),
        cu: CurrentUser = Depends(require(f"{r.perm}:read")),
    ):
        stmt = base_query(q, is_active, include_inactive, filters)
        total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = db.scalars(sorted_stmt(stmt, sort).limit(page_size).offset((page - 1) * page_size)).unique().all()
        return {"items": [to_out(o, cu) for o in rows], "total": total, "page": page, "page_size": page_size}

    if r.exportable:
        @router.get("/export.csv")
        def export(
            q: str | None = None,
            is_active: bool | None = None,
            include_inactive: bool = False,
            filters: dict = Depends(filter_deps()),
            db: Session = Depends(get_db, scope="function"),
            cu: CurrentUser = Depends(require(f"{r.perm}:export")),
        ):
            rows = db.scalars(sorted_stmt(base_query(q, is_active, include_inactive, filters), r.default_sort)).unique().all()
            data = [to_out(o, cu) for o in rows]
            buf = io.StringIO()
            if data:
                w = csv.DictWriter(buf, fieldnames=list(data[0].keys()))
                w.writeheader()
                for row in data:
                    w.writerow({k: _csv_safe(v) for k, v in row.items()})
            audit.record(db, user=cu.user, action="export", entity=r.entity, after={"rows": len(data)})
            return StreamingResponse(
                iter([buf.getvalue()]), media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{r.entity}.csv"'},
            )

    @router.get("/{id_}", response_model=Read)
    def get_(id_: int, db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(require(f"{r.perm}:read"))):
        return to_out(get_or_404(db, id_), cu)

    @router.post("", response_model=Read, status_code=201)
    def create(
        body: r.create_schema,  # type: ignore[valid-type]
        db: Session = Depends(get_db, scope="function"),
        cu: CurrentUser = Depends(require(f"{r.perm}:create")),
    ):
        data = body.model_dump()
        if r.write_guard:
            r.write_guard(data, cu, True)
        obj = Model(**data)
        if hasattr(Model, "created_by_id"):
            obj.created_by_id = obj.updated_by_id = cu.id
        db.add(obj)
        flush(db)
        db.refresh(obj)
        audit.record(db, user=cu.user, action="create", entity=r.entity, entity_id=obj.id, after=snap(obj))
        return to_out(obj, cu)

    @router.patch("/{id_}", response_model=Read)
    def update(
        id_: int,
        body: r.update_schema,  # type: ignore[valid-type]
        db: Session = Depends(get_db, scope="function"),
        cu: CurrentUser = Depends(require(f"{r.perm}:update")),
    ):
        obj = get_or_404(db, id_)
        data = body.model_dump(exclude_unset=True)
        if r.write_guard:
            r.write_guard(data, cu, False)
        before = snap(obj)
        for k, v in data.items():
            setattr(obj, k, v)
        if hasattr(obj, "updated_by_id"):
            obj.updated_by_id = cu.id
        flush(db)
        db.refresh(obj)
        b, a = audit.diff(before, snap(obj))
        if a:
            audit.record(db, user=cu.user, action="update", entity=r.entity, entity_id=obj.id, before=b, after=a)
        return to_out(obj, cu)

    if r.has_active_flag:
        @router.delete("/{id_}", status_code=204)
        def deactivate(id_: int, db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(require(f"{r.perm}:delete"))):
            obj = get_or_404(db, id_)
            if obj.is_active:
                obj.is_active = False
                if hasattr(obj, "updated_by_id"):
                    obj.updated_by_id = cu.id
                audit.record(db, user=cu.user, action="deactivate", entity=r.entity, entity_id=obj.id,
                             before={"is_active": True}, after={"is_active": False})

    return router
