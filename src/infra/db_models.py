"""
SQLAlchemy 2.0 declarative models for Neon-persisted application data.

Tables:
  users             — accounts (stack_user_id from Neon Auth populates in Phase B)
  workspaces        — a user-owned container: holds N kb files, 1 graph, 1 chat history
  workspace_files   — individual KB JSON files belonging to a workspace
  build_jobs        — every graph-build invocation (scoped to a workspace)
  chat_sessions     — a conversation thread within a workspace
  chat_messages     — individual turns (user + assistant)
  config_versions   — YAML snapshot on every PUT
  kb_uploads        — every KB JSON upload event (dedup by sha256 within a workspace)
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
# users
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # Email is the human-facing tenant key. Unique + case-normalised (stored
    # lowercase by signup). Nullable only for historical rows predating auth
    # — new signups always fill it.
    email: Mapped[Optional[str]] = mapped_column(String(256), nullable=True, unique=True)
    # bcrypt hash — NULL for any legacy rows; login requires non-null.
    password_hash: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    # Retained for historical rows from the Neon Auth era. New signups leave
    # this NULL. Kept nullable to avoid a destructive migration on upgrade.
    stack_user_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    workspaces: Mapped[list["Workspace"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


# ---------------------------------------------------------------------------
# workspaces
# ---------------------------------------------------------------------------

class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    domain_config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    graph_config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[User] = relationship(back_populates="workspaces")
    files: Mapped[list["WorkspaceFile"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_workspace_user_name"),
        Index("ix_workspaces_user_id", "user_id"),
    )


# ---------------------------------------------------------------------------
# workspace_files
# ---------------------------------------------------------------------------

class WorkspaceFile(Base):
    __tablename__ = "workspace_files"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    kb_source: Mapped[str] = mapped_column(String(16), nullable=False)  # knowledge|tool
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chapters: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    title: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    blob_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    local_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    workspace: Mapped[Workspace] = relationship(back_populates="files")

    __table_args__ = (
        CheckConstraint("kb_source IN ('knowledge','tool')", name="ck_workspace_files_source"),
        UniqueConstraint("workspace_id", "kb_source", "sha256", name="uq_workspace_files_sha"),
        Index("ix_workspace_files_workspace_id", "workspace_id"),
    )


# ---------------------------------------------------------------------------
# build_jobs (now scoped to a workspace)
# ---------------------------------------------------------------------------

class BuildJobRow(Base):
    __tablename__ = "build_jobs"

    job_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
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
    # Written by the worker every ~10s while a build is running. The sweeper
    # treats rows with ``status='running' AND heartbeat_at < now()-90s`` as
    # crashed-worker zombies and flips them to 'error' (or re-queues if
    # attempt_count < 3). Nullable so pre-feature rows stay valid.
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Host+pid+uuid of the worker that last claimed the row — invaluable
    # when debugging stuck jobs across a fleet.
    worker_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Incremented by the sweeper each time a job is re-queued from a zombie
    # state. After 3 attempts, the row is left as permanent 'error'.
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("status IN ('queued','running','done','error')", name="ck_build_jobs_status"),
        Index("ix_build_jobs_workspace_created", "workspace_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# chat_sessions + chat_messages
# ---------------------------------------------------------------------------

class ChatSession(Base):
    __tablename__ = "chat_sessions"

    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
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

    __table_args__ = (Index("ix_chat_sessions_workspace_activity", "workspace_id", "last_activity_at"),)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
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
# config_versions (per-workspace)
# ---------------------------------------------------------------------------

class ConfigVersion(Base):
    __tablename__ = "config_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    yaml_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    parsed_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    changed_sections: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    requires_rebuild: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint("kind IN ('domain','graph')", name="ck_config_versions_kind"),
        Index("ix_config_versions_ws_kind_created", "workspace_id", "kind", "created_at"),
    )


# ---------------------------------------------------------------------------
# kb_uploads (now per-workspace)
# ---------------------------------------------------------------------------

class KbUpload(Base):
    __tablename__ = "kb_uploads"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    kb_source: Mapped[str] = mapped_column(String(16), nullable=False)
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
        UniqueConstraint("workspace_id", "kb_source", "sha256", name="uq_kb_uploads_ws_src_sha256"),
        Index("ix_kb_uploads_ws_created", "workspace_id", "created_at"),
    )


# ---------------------------------------------------------------------------
# user_audit_log — append-only record of meaningful user actions.
# ---------------------------------------------------------------------------

class UserAuditLog(Base):
    """Who did what, when, against which target.

    Every mutating API call records one row via ``src.infra.audit.record_audit``.
    The table is append-only by convention — no updates, no deletes (except
    CASCADE on user deletion).

    Fields are intentionally loose so every consumer can share one table:
    - ``action`` is a dotted verb string (``workspace.create``, ``build.start``,
      ``chat.query``, …).
    - ``target_type`` is the object class (``workspace``, ``template``,
      ``chat_session`` …).
    - ``target_id`` is a free-form string; could be a UUID, a job_id, a slug.
    - ``workspace_id`` is denormalised when known, so the UI can filter the
      feed per-workspace cheaply.
    - ``metadata`` JSONB holds per-action context (e.g. ``{"name": "mine"}``
      on workspace.create).
    """
    __tablename__ = "user_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Free-form context per action. Keep it small — this is an audit trail, not
    # a log aggregator; large payloads belong in their own table.
    audit_metadata: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_user_audit_user_created", "user_id", "created_at"),
        Index("ix_user_audit_ws_created", "workspace_id", "created_at"),
        Index("ix_user_audit_action", "action"),
    )


# ---------------------------------------------------------------------------
# Legacy anonymous user marker
#
# The dev-mode fallback that wrote rows with this sentinel is gone; Neon Auth
# is now required in all environments. The constant is retained only because
# ``scripts/migrate_anon_workspaces.py`` still uses it to clean up historical
# anon rows. Do NOT reintroduce into the request path.
# ---------------------------------------------------------------------------

ANONYMOUS_STACK_ID = "__anonymous__"


# ---------------------------------------------------------------------------
# user_templates — user-owned KB templates (custom, private to creator)
#
# Stock templates ship as YAML files under /templates (global, read-only).
# This table stores custom templates created via the /templates page CRUD UI.
# The two sources are merged at read-time by the API layer; on instantiate,
# the backend shape-detects the identifier (UUID → custom, slug → stock).
# ---------------------------------------------------------------------------

class UserTemplate(Base):
    __tablename__ = "user_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="custom")
    icon: Mapped[str] = mapped_column(String(32), nullable=False, default="Folder")
    # The five DomainProfile fields copied onto Workspace.domain_config on
    # instantiate. Shape matches stock KBTemplate.domain exactly.
    domain: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        # One user can't have two templates with the same name — matches the
        # per-user workspace naming rule so the UI copy stays consistent.
        UniqueConstraint("user_id", "name", name="uq_user_templates_user_name"),
        Index("ix_user_templates_user_id", "user_id"),
    )
