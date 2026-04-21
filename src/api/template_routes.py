"""
KB template catalog + user-CRUD endpoints.

Two sources feed the catalog:
  1. **Stock** templates — YAML files under /templates at the repo root,
     loaded once per process. Global, read-only, shipped via PRs.
  2. **Custom** templates — rows in ``public.user_templates``, private per
     user, fully CRUDable.

The read API merges both; the write API (POST/PATCH/DELETE) only touches
custom templates. Every response carries ``source`` and ``editable`` so the
UI can gate edit/delete buttons on ownership.

Identifier scheme: stock templates use kebab-case slugs, custom templates
use UUIDs. A single ``{id}`` path param resolves either by shape-detecting
the string (UUID parse succeeds → custom, else stock).
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.api.auth import current_user, require_user
from src.config import USE_NEON
from src.infra.audit import record_audit
from src.infra.db import get_session
from src.infra.db_models import User, UserTemplate, Workspace
from src.templates import get_template, search_templates

logger = logging.getLogger(__name__)

router = APIRouter()

# Workspace cap — prevents template-instantiation abuse (see plan risk table).
# Operator can raise via env var; default matches what the planning doc proposed.
MAX_WORKSPACES_PER_USER = int(os.environ.get("MAX_WORKSPACES_PER_USER", "25"))

# User-created-template cap — stops a single user from flooding their own
# catalog. Stock templates are always visible regardless.
MAX_USER_TEMPLATES = int(os.environ.get("MAX_USER_TEMPLATES_PER_USER", "50"))


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class DomainPayload(BaseModel):
    """The five DomainProfile fields copied onto Workspace.domain_config."""
    domain_name: str = Field(min_length=1, max_length=128)
    domain_display_name: str = Field(min_length=1, max_length=128)
    organization_name: str = Field(min_length=1, max_length=128)
    knowledge_focus_examples: str = Field(min_length=1, max_length=4000)
    tool_focus_examples: str = Field(min_length=1, max_length=4000)


class TemplateOut(BaseModel):
    # Opaque identifier: slug for stock, UUID string for custom. The client
    # uses this verbatim in /templates/{id} paths.
    id: str
    # Legacy alias retained for the stock slug — same as `id` for stock rows,
    # empty string for custom rows. Lets existing FE code keep rendering the
    # small mono-font caption under the template name without a type change.
    slug: str
    source: Literal["stock", "custom"]
    editable: bool
    name: str
    description: str
    category: str
    icon: str
    domain: dict


class TemplateListResponse(BaseModel):
    templates: list[TemplateOut]


class TemplateCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)
    category: str = Field(min_length=1, max_length=64, default="custom")
    icon: str = Field(min_length=1, max_length=32, default="Folder")
    domain: DomainPayload


class TemplateUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    category: Optional[str] = Field(default=None, min_length=1, max_length=64)
    icon: Optional[str] = Field(default=None, min_length=1, max_length=32)
    domain: Optional[DomainPayload] = None


class InstantiateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128,
                                description="Workspace name; defaults to the template's display name.")
    description: Optional[str] = None


class InstantiateResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    template_slug: str


# ---------------------------------------------------------------------------
# Helpers — uniform (stock | custom) -> TemplateOut
# ---------------------------------------------------------------------------

def _stock_out(t) -> TemplateOut:
    return TemplateOut(
        id=t.slug, slug=t.slug, source="stock", editable=False,
        name=t.name, description=t.description,
        category=t.category, icon=t.icon, domain=t.domain,
    )


def _custom_out(row: UserTemplate) -> TemplateOut:
    return TemplateOut(
        id=str(row.id), slug="", source="custom", editable=True,
        name=row.name, description=row.description,
        category=row.category, icon=row.icon, domain=dict(row.domain),
    )


def _as_uuid(ident: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(ident)
    except (ValueError, TypeError):
        return None


async def _resolve_template(
    ident: str,
    user: Optional[User],
) -> tuple[Literal["stock", "custom"], dict]:
    """Return ('stock'|'custom', {slug, name, description, domain, ...}) for
    an identifier — UUID means custom (must be owned by ``user``), else it's
    a stock slug. Raises 404 if nothing matches or 403 when a custom template
    is not owned by the caller.
    """
    row_uuid = _as_uuid(ident)
    if row_uuid is not None:
        if user is None or not USE_NEON:
            raise HTTPException(status_code=404, detail=f"Template {ident!r} not found.")
        async with get_session() as s:
            row = (await s.execute(
                select(UserTemplate).where(UserTemplate.id == row_uuid)
            )).scalar_one_or_none()
            if row is None:
                raise HTTPException(status_code=404, detail=f"Template {ident!r} not found.")
            if row.user_id != user.id:
                # Privacy: don't leak existence of another user's template.
                raise HTTPException(status_code=404, detail=f"Template {ident!r} not found.")
            return ("custom", {
                "slug": str(row.id),  # opaque identifier for audit trail
                "name": row.name,
                "description": row.description,
                "domain": dict(row.domain),
            })
    t = get_template(ident)
    if t is None:
        raise HTTPException(status_code=404, detail=f"Template {ident!r} not found.")
    return ("stock", {
        "slug": t.slug,
        "name": t.name,
        "description": t.description,
        "domain": dict(t.domain),
    })


# ---------------------------------------------------------------------------
# READ — list + detail (merged stock + custom)
# ---------------------------------------------------------------------------

@router.get(
    "/templates",
    response_model=TemplateListResponse,
    summary="Browse the KB template catalog (stock + your custom templates)",
)
async def list_templates_route(
    q: Optional[str] = Query(default=None, description="Substring search on name/slug/description"),
    category: Optional[str] = Query(default=None, description="Filter by category (e.g. 'people', 'legal')"),
    user: Optional[User] = Depends(current_user),
):
    """Anyone can browse stock templates (no auth required). Custom templates
    only appear when a valid bearer token identifies the caller."""
    stock = [_stock_out(t) for t in search_templates(q, category)]

    custom: list[TemplateOut] = []
    if user is not None and USE_NEON:
        async with get_session() as s:
            stmt = select(UserTemplate).where(UserTemplate.user_id == user.id)
            if category:
                stmt = stmt.where(UserTemplate.category == category)
            rows = (await s.execute(stmt.order_by(UserTemplate.created_at.desc()))).scalars().all()
        for r in rows:
            if q:
                needle = q.lower().strip()
                haystack = f"{r.name} {r.description} {r.category}".lower()
                if needle not in haystack:
                    continue
            custom.append(_custom_out(r))

    # Custom first so the user's own templates surface at the top of the grid.
    return TemplateListResponse(templates=custom + stock)


@router.get("/templates/{ident}", response_model=TemplateOut, summary="Template detail (stock slug or custom UUID)")
async def get_template_route(
    ident: str,
    user: Optional[User] = Depends(current_user),
):
    row_uuid = _as_uuid(ident)
    if row_uuid is not None:
        if user is None or not USE_NEON:
            raise HTTPException(status_code=404, detail=f"Template {ident!r} not found.")
        async with get_session() as s:
            row = (await s.execute(
                select(UserTemplate).where(UserTemplate.id == row_uuid)
            )).scalar_one_or_none()
        if row is None or row.user_id != user.id:
            raise HTTPException(status_code=404, detail=f"Template {ident!r} not found.")
        return _custom_out(row)
    t = get_template(ident)
    if t is None:
        raise HTTPException(status_code=404, detail=f"Template {ident!r} not found.")
    return _stock_out(t)


# ---------------------------------------------------------------------------
# WRITE — custom templates only (POST / PATCH / DELETE)
# ---------------------------------------------------------------------------

@router.post(
    "/templates",
    response_model=TemplateOut,
    status_code=201,
    summary="Create a custom template (user-owned)",
)
async def create_template_route(
    body: TemplateCreateRequest,
    user: User = Depends(require_user),
):
    if not USE_NEON:
        raise HTTPException(status_code=503, detail="Custom templates require DATABASE_URL.")

    async with get_session() as s:
        # Cap per user
        count = (await s.execute(
            select(func.count()).select_from(UserTemplate)
            .where(UserTemplate.user_id == user.id)
        )).scalar_one()
        if int(count) >= MAX_USER_TEMPLATES:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Custom-template cap ({MAX_USER_TEMPLATES}) reached. "
                    "Delete an unused template before creating another."
                ),
            )

        row = UserTemplate(
            user_id=user.id,
            name=body.name.strip(),
            description=body.description.strip(),
            category=body.category.strip() or "custom",
            icon=body.icon.strip() or "Folder",
            domain=body.domain.model_dump(),
        )
        s.add(row)
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"You already have a template named {body.name!r}.",
            )
        await s.refresh(row)

    record_audit(
        user.id, "template.create",
        target_type="template", target_id=str(row.id),
        metadata={"template_name": row.name, "category": row.category},
    )
    return _custom_out(row)


@router.patch(
    "/templates/{ident}",
    response_model=TemplateOut,
    summary="Update a custom template (stock templates are read-only)",
)
async def update_template_route(
    ident: str,
    body: TemplateUpdateRequest,
    user: User = Depends(require_user),
):
    row_uuid = _as_uuid(ident)
    if row_uuid is None:
        # Stock slug — explicitly reject
        if get_template(ident) is not None:
            raise HTTPException(
                status_code=400,
                detail="Stock templates are read-only. Duplicate it first by creating a new custom template.",
            )
        raise HTTPException(status_code=404, detail=f"Template {ident!r} not found.")

    if not USE_NEON:
        raise HTTPException(status_code=503, detail="Custom templates require DATABASE_URL.")

    async with get_session() as s:
        row = (await s.execute(
            select(UserTemplate).where(UserTemplate.id == row_uuid)
        )).scalar_one_or_none()
        if row is None or row.user_id != user.id:
            raise HTTPException(status_code=404, detail=f"Template {ident!r} not found.")

        updates: dict = {}
        if body.name is not None: updates["name"] = body.name.strip()
        if body.description is not None: updates["description"] = body.description.strip()
        if body.category is not None: updates["category"] = body.category.strip()
        if body.icon is not None: updates["icon"] = body.icon.strip()
        if body.domain is not None: updates["domain"] = body.domain.model_dump()
        if not updates:
            return _custom_out(row)

        for k, v in updates.items():
            setattr(row, k, v)
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise HTTPException(
                status_code=409,
                detail=f"You already have another template with that name.",
            )
        await s.refresh(row)

    record_audit(
        user.id, "template.update",
        target_type="template", target_id=str(row.id),
        metadata={"template_name": row.name, "fields": sorted(updates.keys())},
    )
    return _custom_out(row)


@router.delete(
    "/templates/{ident}",
    summary="Delete a custom template (stock templates are read-only)",
)
async def delete_template_route(
    ident: str,
    user: User = Depends(require_user),
):
    row_uuid = _as_uuid(ident)
    if row_uuid is None:
        if get_template(ident) is not None:
            raise HTTPException(
                status_code=400,
                detail="Stock templates are read-only and cannot be deleted.",
            )
        raise HTTPException(status_code=404, detail=f"Template {ident!r} not found.")

    if not USE_NEON:
        raise HTTPException(status_code=503, detail="Custom templates require DATABASE_URL.")

    async with get_session() as s:
        row = (await s.execute(
            select(UserTemplate).where(UserTemplate.id == row_uuid)
        )).scalar_one_or_none()
        if row is None or row.user_id != user.id:
            raise HTTPException(status_code=404, detail=f"Template {ident!r} not found.")
        name_snap = row.name
        await s.delete(row)
        await s.commit()

    record_audit(
        user.id, "template.delete",
        target_type="template", target_id=str(row_uuid),
        metadata={"template_name": name_snap},
    )
    return {"ok": True, "deleted_template_id": str(row_uuid)}


# ---------------------------------------------------------------------------
# INSTANTIATE — works for both stock and custom
# ---------------------------------------------------------------------------

@router.post(
    "/templates/{ident}/instantiate",
    response_model=InstantiateResponse,
    status_code=201,
    summary="Create a workspace pre-seeded with this template's domain",
)
async def instantiate_template(
    ident: str,
    body: InstantiateRequest,
    user: User = Depends(require_user),
):
    if not USE_NEON:
        raise HTTPException(status_code=503, detail="Workspaces require DATABASE_URL (Neon).")

    _, tmpl = await _resolve_template(ident, user)

    async with get_session() as s:
        # Workspace cap per user — prevents template-instantiation abuse.
        count = (await s.execute(
            select(func.count()).select_from(Workspace).where(Workspace.user_id == user.id)
        )).scalar_one()
        if int(count) >= MAX_WORKSPACES_PER_USER:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Workspace cap ({MAX_WORKSPACES_PER_USER}) reached for this account. "
                    "Delete an unused workspace before creating another."
                ),
            )

        wanted_name = (body.name or tmpl["name"]).strip() or tmpl["name"]
        # Disambiguate on name collision: append a short counter so repeat
        # instantiations don't 409 — users expect "make me a fresh one."
        final_name = wanted_name
        suffix = 2
        while True:
            existing = (await s.execute(
                select(Workspace).where(
                    Workspace.user_id == user.id, Workspace.name == final_name
                )
            )).scalar_one_or_none()
            if existing is None:
                break
            final_name = f"{wanted_name} ({suffix})"
            suffix += 1

        ws = Workspace(
            user_id=user.id,
            name=final_name,
            description=body.description if body.description is not None else tmpl["description"],
            domain_config=dict(tmpl["domain"]),
        )
        s.add(ws)
        await s.commit()
        await s.refresh(ws)

    record_audit(
        user.id, "template.instantiate",
        target_type="template", target_id=tmpl["slug"],
        workspace_id=ws.id,
        metadata={"workspace_name": ws.name, "template_name": tmpl["name"]},
    )
    return InstantiateResponse(
        id=str(ws.id),
        name=ws.name,
        description=ws.description,
        template_slug=tmpl["slug"],
    )
