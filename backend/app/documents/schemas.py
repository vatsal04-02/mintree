from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

DOC_TYPES = [
    "COA", "SUPPLIER_INVOICE", "SUPPLIER_DOCUMENT", "QUALITY_CERTIFICATE", "FORMULATION", "BATCH_RECORD",
    "INSPECTION_RECORD", "PRODUCT_IMAGE", "MARKETING_ASSET", "COURIER_LABEL", "CUSTOMER_DOCUMENT", "OTHER",
]


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version_no: int
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    note: str | None
    uploaded_by_id: int | None
    uploaded_at: datetime


class LinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    entity_type: str
    entity_id: str


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    doc_type: str
    description: str | None
    current_version_no: int
    created_at: datetime
    updated_at: datetime
    versions: list[VersionOut]
    links: list[LinkOut]


class LinkIn(BaseModel):
    entity_type: str = Field(pattern=r"^[a-z][a-z0-9_]{1,59}$")
    entity_id: str = Field(min_length=1, max_length=64)


class DocumentUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    doc_type: str | None = None
