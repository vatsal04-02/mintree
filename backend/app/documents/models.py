from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.mixins import AuditFieldsMixin


class Document(AuditFieldsMixin, Base):
    __tablename__ = "documents"
    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    doc_type: Mapped[str] = mapped_column(String(40))  # e.g. COA, SUPPLIER_INVOICE, BATCH_RECORD
    description: Mapped[str | None] = mapped_column(Text)
    current_version_no: Mapped[int] = mapped_column(Integer, default=1)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    versions: Mapped[list["DocumentVersion"]] = relationship(
        order_by="DocumentVersion.version_no", lazy="selectin", back_populates="document"
    )
    links: Mapped[list["DocumentLink"]] = relationship(lazy="selectin", cascade="all, delete-orphan")


class DocumentVersion(Base):
    """Immutable file revisions. A new upload creates a new version; old files are kept."""

    __tablename__ = "document_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version_no: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(300), unique=True)
    note: Mapped[str | None] = mapped_column(String(300))
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    document: Mapped[Document] = relationship(back_populates="versions")
    __table_args__ = (UniqueConstraint("document_id", "version_no"),)


class DocumentLink(Base):
    """Links a document to any business record, e.g. ('supplier', '12')."""

    __tablename__ = "document_links"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(64))
    __table_args__ = (
        UniqueConstraint("document_id", "entity_type", "entity_id"),
        Index("ix_document_links_entity", "entity_type", "entity_id"),
    )
