"""
SQLAlchemy 2.0 declarative models for Neon-persisted application data.

Tables:
  build_jobs        — every graph-build invocation, with config snapshot + log tail
  chat_sessions     — a user's conversation thread
  chat_messages     — individual turns (user query + assistant response) in a session
  config_versions   — snapshot of domain.yaml / graph.yaml on every PUT
  kb_uploads        — every KB JSON file upload event
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# build_jobs
# ---------------------------------------------------------------------------

class BuildJobRow(Base):
    __tablename__ = "build_jobs"

    job_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # queued|running|done|error
    stage: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Queued")
    percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    log_tail: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    skip_embeddings: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    skip_llm_cross_links: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    domain_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    graph_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    stats: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    backends: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("status IN ('queued','running','done','error')", name="ck_build_jobs_status"),
        Index("ix_build_jobs_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# chat_sessions + chat_messages
# ---------------------------------------------------------------------------

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ChatMessage.id"
    )

    __table_args__ = (Index("ix_chat_sessions_last_activity", "last_activity_at"),)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user|assistant
    query: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    intent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    kb_focus: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    extracted_topics: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    tools_referenced: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    knowledge_concepts: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    traversal_path: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    follow_up_suggestions: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    session: Mapped[ChatSession] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="ck_chat_messages_role"),
        Index("ix_chat_messages_session_id_created_at", "session_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# config_versions
# ---------------------------------------------------------------------------

class ConfigVersion(Base):
    __tablename__ = "config_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # domain|graph
    yaml_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    changed_sections: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    requires_rebuild: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("kind IN ('domain','graph')", name="ck_config_versions_kind"),
        Index("ix_config_versions_kind_created_at", "kind", "created_at"),
    )


# ---------------------------------------------------------------------------
# kb_uploads
# ---------------------------------------------------------------------------

class KbUpload(Base):
    __tablename__ = "kb_uploads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kb_source: Mapped[str] = mapped_column(String(16), nullable=False)  # knowledge|tool
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    blob_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    blob_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("kb_source IN ('knowledge','tool')", name="ck_kb_uploads_source"),
        UniqueConstraint("kb_source", "sha256", name="uq_kb_uploads_source_sha256"),
        Index("ix_kb_uploads_created_at", "created_at"),
    )
