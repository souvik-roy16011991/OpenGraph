"""
KB template catalog endpoints.

- ``GET /templates`` and ``GET /templates/{slug}`` are **public** (no auth) —
  a signed-out visitor can browse the catalog before creating an account.
- ``POST /templates/{slug}/instantiate`` is auth'd. It creates a new
  ``Workspace`` for the caller, pre-seeding ``domain_config`` with the
  template's five DomainProfile fields, and returns the workspace summary.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from src.api.auth import require_user
from src.config import USE_NEON
from src.infra.audit import record_audit
from src.infra.db import get_session
from src.infra.db_models import User, Workspace
from src.templates import get_template, search_templates

logger = logging.getLogger(__name__)

router = APIRouter()

# Workspace cap — prevents template-instantiation abuse (see plan risk table).
# Operator can raise via env var; default matches what the planning doc proposed.
import os
MAX_WORKSPACES_PER_USER = int(os.environ.get("MAX_WORKSPACES_PER_USER", "25"))


class TemplateOut(BaseModel):
    slug: str
    name: str
    description: str
    category: str
    icon: str
    domain: dict


class TemplateListResponse(BaseModel):
    templates: list[TemplateOut]


class InstantiateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128,
                                description="Workspace name; defaults to the template's display name.")
    description: Optional[str] = None


def _to_out(t) -> TemplateOut:
    return TemplateOut(
        slug=t.slug, name=t.name, description=t.description,
        category=t.category, icon=t.icon, domain=t.domain,
    )


@router.get("/templates", response_model=TemplateListResponse, summary="Browse the KB template catalog")
def list_templates_route(
    q: Optional[str] = Query(default=None, description="Substring search on name/slug/description"),
    category: Optional[str] = Query(default=None, description="Filter by category (e.g. 'people', 'legal')"),
):
    """Open endpoint — browseable without sign-in."""
    return TemplateListResponse(templates=[_to_out(t) for t in search_templates(q, category)])


@router.get("/templates/{slug}", response_model=TemplateOut, summary="Template detail")
def get_template_route(slug: str):
    t = get_template(slug)
    if t is None:
        raise HTTPException(status_code=404, detail=f"Template {slug!r} not found.")
    return _to_out(t)


class InstantiateResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    template_slug: str


@router.post(
    "/templates/{slug}/instantiate",
    response_model=InstantiateResponse,
    status_code=201,
    summary="Create a workspace pre-seeded with this template's domain",
)
async def instantiate_template(
    slug: str,
    body: InstantiateRequest,
    user: User = Depends(require_user),
):
    if not USE_NEON:
        raise HTTPException(status_code=503, detail="Workspaces require DATABASE_URL (Neon).")

    tmpl = get_template(slug)
    if tmpl is None:
        raise HTTPException(status_code=404, detail=f"Template {slug!r} not found.")

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

        wanted_name = (body.name or tmpl.name).strip() or tmpl.name
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
            description=body.description if body.description is not None else tmpl.description,
            domain_config=dict(tmpl.domain),
        )
        s.add(ws)
        await s.commit()
        await s.refresh(ws)

    record_audit(
        user.id, "template.instantiate",
        target_type="template", target_id=tmpl.slug,
        workspace_id=ws.id,
        metadata={"workspace_name": ws.name, "template_name": tmpl.name},
    )
    return InstantiateResponse(
        id=str(ws.id),
        name=ws.name,
        description=ws.description,
        template_slug=tmpl.slug,
    )
