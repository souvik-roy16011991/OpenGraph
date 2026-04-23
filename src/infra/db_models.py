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
    text,
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
    # GitHub OAuth identity. NULL for users who signed up with email/password.
    # Stored as the numeric id from GitHub (stable across username changes).
    github_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    # Cached avatar URL from the OAuth provider (GitHub today).
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
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
    # Delete saga. ``DELETE /workspaces/{id}`` sets ``deleted_at`` and kicks
    # off the cross-store cascade (Memgraph / Qdrant / Blob). On full success
    # the row is hard-deleted. On partial failure the row persists with
    # ``deleted_at`` set and ``deletion_failure_count`` incremented; a
    # background sweeper retries every minute. All read paths filter
    # ``deleted_at IS NULL`` so the user stops seeing the workspace the
    # moment the HTTP delete returns.
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deletion_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletion_last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # API deployment. NULL means "not published to /api/v1/ext/*"; the
    # ext surface rejects calls targeting undeployed workspaces. Set
    # by ``POST /workspaces/{id}/deploy`` (which also mints a scoped
    # API key in the same transaction). Cleared on soft-delete; a
    # backfill in init_db marks pre-existing built workspaces as
    # deployed so legacy integrations keep working.
    deployed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="workspaces")
    files: Mapped[list["WorkspaceFile"]] = relationship(
        back_populates="workspace", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_workspace_user_name"),
        Index("ix_workspaces_user_id", "user_id"),
        # Partial index keeps the sweeper's scan cheap: only rows currently
        # mid-deletion hit the index, so at 1M workspaces the sweeper reads
        # ~N-in-flight rows instead of a full table scan.
        Index(
            "ix_workspaces_deleted_at",
            "deleted_at",
            postgresql_where=text("deleted_at IS NOT NULL"),
        ),
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


# ---------------------------------------------------------------------------
# Billing — trial / PAYG credits / Team subscription / BYOK
# ---------------------------------------------------------------------------
# One row per user. Denormalized trial counters + credit balances so the
# enforcement fast-path reads a single row on every /build and /query.
# Ledger-source-of-truth is ``CreditTransaction``; this table caches the
# running balance for cheap reads.

class BillingAccount(Base):
    __tablename__ = "billing_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    plan_tier: Mapped[str] = mapped_column(String(16), nullable=False, default="trial")
    # Lifetime trial counters — reset only by support action, never by time.
    trial_build_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trial_chat_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Two buckets: subscription_credits reset monthly on Stripe renewal,
    # topup_credits persist indefinitely. Debit subscription first.
    subscription_credits: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    topup_credits: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Negative balance is allowed down to this floor so a single in-flight
    # build doesn't fail mid-stream on post-commit deduction. New builds /
    # chats are rejected once total balance falls below this value.
    overdraft_limit: Mapped[int] = mapped_column(BigInteger, nullable=False, default=-200)
    stripe_customer_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "plan_tier IN ('trial','payg','team')",
            name="ck_billing_accounts_plan_tier",
        ),
    )


# Append-only ledger. Every credit motion — grant from Stripe, spend from a
# build, storage proration tick, admin adjustment — writes exactly one row.
# This table IS the audit log for money movement; don't duplicate into
# ``user_audit_log``.

class CreditTransaction(Base):
    __tablename__ = "credit_transactions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Positive = grant, negative = spend. Matches accounting sign convention.
    delta_credits: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bucket: Mapped[str] = mapped_column(String(16), nullable=False)  # subscription | topup
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    # Free-form reference back to the action that caused the ledger entry.
    source_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    source_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False, default="system")
    actor_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    # Stripe idempotency — a duplicate webhook hits the UNIQUE and rolls back,
    # never double-crediting a user. Nullable for non-Stripe sources.
    stripe_event_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_credit_tx_user_created", "user_id", "created_at"),
        UniqueConstraint("stripe_event_id", name="uq_credit_tx_stripe_event"),
        CheckConstraint(
            "bucket IN ('subscription','topup')",
            name="ck_credit_tx_bucket",
        ),
        CheckConstraint(
            "actor_type IN ('user','system','stripe_webhook','admin')",
            name="ck_credit_tx_actor_type",
        ),
    )


# Stripe-backed subscriptions (Team tier). One active row per user; we keep
# canceled rows for audit.

class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    tier: Mapped[str] = mapped_column(String(16), nullable=False)  # team (future: enterprise)
    stripe_subscription_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    canceled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # During Stripe's smart-retry dunning window (3-21 days by default), the
    # subscription stays ``team`` tier even though the last invoice failed.
    grace_period_ends_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (Index("ix_subscriptions_user", "user_id"),)


# Team-tier member roster. Owner's workspaces become accessible to accepted
# members via a JOIN in ``require_workspace_id`` (Phase 3).

class TeamMember(Base):
    __tablename__ = "team_members"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Null until the invited email completes signup + accept.
    member_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    invited_email: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="member")
    accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_team_members_owner", "owner_user_id"),
        Index("ix_team_members_member", "member_user_id"),
        CheckConstraint("role IN ('owner','admin','member')", name="ck_team_members_role"),
    )


# Per-workspace OpenRouter API key (BYOK). Stored as Fernet ciphertext;
# fingerprint is last 4 chars of plaintext for UI display without decrypt.

class WorkspaceApiKey(Base):
    __tablename__ = "workspace_api_keys"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    encrypted_openrouter_key: Mapped[str] = mapped_column(Text, nullable=False)
    key_fingerprint: Mapped[str] = mapped_column(String(8), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


# Developer API keys (the /api/v1/ext/* surface).
#
# Distinct from ``WorkspaceApiKey`` above — that stores the user's BYOK
# OpenRouter secret for billing waivers. This table holds the credentials
# a third-party developer uses to CALL us. The plaintext key is shown to
# the user ONCE at creation; we persist only a bcrypt hash plus a plaintext
# ``prefix`` (first 16 chars) used for O(1) lookup.
#
# Key wire format: ``og_live_<32-char-base64-urlsafe>``. The ``og_live_``
# marker is reserved so we can later introduce ``og_test_`` without a
# schema change.

class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Optional workspace scope. When set, the key can only call endpoints
    # that resolve to this workspace. NULL means "any workspace I own" —
    # the ext auth dep verifies ownership on every call.
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=True,
    )
    # User-visible label. Shown on the dashboard, not used for lookup.
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # First 16 chars of the plaintext key — safe to show and to index.
    # ``og_live_`` + the first 8 secret chars. Unique across the table.
    prefix: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    # bcrypt hash of the full plaintext key. Cannot be recovered.
    key_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    # JSONB list of scope strings. v1 always ``["ext:read"]``. Kept JSONB
    # so future writes (``ext:workspace:write``) don't need a migration.
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Fixed-window rate limit applied to this key, requests per minute.
    # Default 60; Team-plan users can request higher.
    rate_limit_rpm: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_used_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Soft-delete. All auth paths filter ``revoked_at IS NULL``.
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_api_keys_user_id", "user_id"),
        # Partial index keeps the auth lookup cheap at 1M keys — only live
        # rows hit the index; revoked rows stay out of the hot path.
        Index(
            "ix_api_keys_prefix_live",
            "prefix",
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )


# Fractional daily storage accumulator. A workspace that costs 0.05 credits
# per day (small graph + vectors) would otherwise round to 0 every single
# day and never be billed. This table carries the sub-credit remainder as
# micro-credits (1 credit = 1_000_000 micros) and flushes whole credits to
# the user's balance only when remainder >= 1_000_000.

class WorkspaceStorageMeter(Base):
    __tablename__ = "workspace_storage_meters"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Fractional credit remainder, 0 <= value < 1_000_000. Whole-credit
    # portions are flushed to ``billing_accounts`` as ledger entries.
    remainder_micro: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Last day the sweeper incremented this row (UTC date, serialised as
    # TIMESTAMPTZ at 00:00 of that day for the dedupe check). Replaces
    # the per-(workspace,day) existence check the old sweeper used.
    last_charged_on: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
