"""
KB JSON upload endpoints — per-workspace, multi-file, cloud-only.

POST /api/v1/kb/upload (X-Workspace-Id required)
  - Accepts any number of ``knowledge_files`` and any number of ``tool_files``
    in a single multipart request.
  - Each file is validated, sha256-hashed, uploaded to Vercel Blob at
    {BLOB_STORE_PATH}/{ws_id}/{kb_source}/{filename}, and recorded as a
    WorkspaceFile + kb_uploads row in Neon.
  - Duplicates (same sha256 for same kb_source in this workspace) are
    silently skipped.

Nothing is written to the local filesystem — Vercel Blob is the single
source of truth for uploaded KB content. If the Blob upload fails the whole
request fails; there is no local fallback to drift out of sync.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.api.auth import require_user
from src.api.deps import require_workspace_id
from src.config import BLOB_READ_WRITE_TOKEN
from src.infra.audit import record_audit
from src.infra.db import get_session
from src.infra.db_models import KbUpload, User, WorkspaceFile

logger = logging.getLogger(__name__)

router = APIRouter()


class UploadedFileInfo(BaseModel):
    id: int
    kb_source: str
    filename: str
    size_bytes: int
    chapters: int
    title: Optional[str] = None
    sha256: str
    blob_url: str
    duplicate: bool = False


class UploadResponse(BaseModel):
    workspace_id: str
    knowledge: list[UploadedFileInfo] = []
    tool: list[UploadedFileInfo] = []
    warnings: list[str] = []


async def _read_and_validate(file: UploadFile, label: str) -> tuple[bytes, dict[str, Any]]:
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail=f"{label} upload is empty")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"{label} file is not valid JSON: {exc}",
        )
    if not isinstance(payload, dict) or "chapters" not in payload or not isinstance(payload["chapters"], list):
        raise HTTPException(
            status_code=400,
            detail=f"{label} JSON must be an object with a top-level `chapters` list",
        )
    return raw, payload


def _safe_filename(filename: Optional[str], kb_source: str, sha: str) -> str:
    base = Path(filename or f"{kb_source}.json").name
    if not base.lower().endswith(".json"):
        base += ".json"
    return base


async def _persist_one(
    workspace_id: str,
    kb_source: str,
    upload: UploadFile,
) -> UploadedFileInfo:
    raw, payload = await _read_and_validate(upload, kb_source)
    sha = hashlib.sha256(raw).hexdigest()
    filename = _safe_filename(upload.filename, kb_source, sha)

    # Dedup by sha inside this workspace
    async with get_session() as s:
        r = await s.execute(
            select(WorkspaceFile).where(
                WorkspaceFile.workspace_id == uuid.UUID(workspace_id),
                WorkspaceFile.kb_source == kb_source,
                WorkspaceFile.sha256 == sha,
            )
        )
        existing = r.scalar_one_or_none()
        if existing is not None:
            return UploadedFileInfo(
                id=existing.id,
                kb_source=existing.kb_source,
                filename=existing.filename,
                size_bytes=existing.size_bytes,
                chapters=existing.chapters,
                title=existing.title,
                sha256=existing.sha256,
                blob_url=existing.blob_url or "",
                duplicate=True,
            )

    # Upload to Vercel Blob — the single source of truth. Fail the request
    # if this fails; we will not silently fall back to local disk.
    if not BLOB_READ_WRITE_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="BLOB_READ_WRITE_TOKEN is not configured; cannot accept uploads.",
        )
    try:
        from src.infra.blob_loader import upload_file_bytes
        blob_url = upload_file_bytes(workspace_id, kb_source, filename, raw)
    except Exception as exc:
        logger.error("Vercel Blob upload failed for ws=%s %s/%s: %s", workspace_id, kb_source, filename, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Vercel Blob upload failed: {exc}",
        )

    async with get_session() as s:
        wf = WorkspaceFile(
            workspace_id=uuid.UUID(workspace_id),
            kb_source=kb_source,
            filename=filename,
            size_bytes=len(raw),
            chapters=len(payload.get("chapters", [])),
            title=payload.get("title"),
            sha256=sha,
            blob_url=blob_url,
            local_path=None,
            active=True,
        )
        s.add(wf)
        s.add(KbUpload(
            workspace_id=uuid.UUID(workspace_id),
            kb_source=kb_source,
            filename=filename,
            size_bytes=len(raw),
            chapters=len(payload.get("chapters", [])),
            title=payload.get("title"),
            sha256=sha,
            blob_url=blob_url,
            blob_error=None,
        ))
        try:
            await s.commit()
        except IntegrityError:
            # Two concurrent uploads of the same sha both passed the dedup
            # SELECT. The unique constraint ``uq_workspace_files_sha`` just
            # rejected the loser. Both produced identical byte content
            # (sha matched), so the blob we just uploaded is redundant:
            # try to delete it to avoid orphaning a named copy, then
            # re-read the winner's row and return it as a duplicate so
            # the client sees consistent metadata.
            await s.rollback()
            _best_effort_blob_delete(blob_url)
            r2 = await s.execute(
                select(WorkspaceFile).where(
                    WorkspaceFile.workspace_id == uuid.UUID(workspace_id),
                    WorkspaceFile.kb_source == kb_source,
                    WorkspaceFile.sha256 == sha,
                )
            )
            winner = r2.scalar_one_or_none()
            if winner is None:
                # Extremely unlikely — the unique constraint says someone
                # has this (ws,kb,sha) tuple, but the winning row isn't
                # visible. Surface rather than pretending success.
                raise HTTPException(
                    status_code=500,
                    detail="Upload raced but winner row not found",
                )
            return UploadedFileInfo(
                id=winner.id,
                kb_source=winner.kb_source,
                filename=winner.filename,
                size_bytes=winner.size_bytes,
                chapters=winner.chapters,
                title=winner.title,
                sha256=winner.sha256,
                blob_url=winner.blob_url or "",
                duplicate=True,
            )
        except Exception:
            # Any other DB failure (connection lost, check constraint,
            # etc.) leaves our blob orphaned if we don't compensate.
            await s.rollback()
            _best_effort_blob_delete(blob_url)
            raise
        await s.refresh(wf)

    return UploadedFileInfo(
        id=wf.id,
        kb_source=kb_source,
        filename=filename,
        size_bytes=len(raw),
        chapters=len(payload.get("chapters", [])),
        title=payload.get("title"),
        sha256=sha,
        blob_url=blob_url,
        duplicate=False,
    )


def _best_effort_blob_delete(blob_url: str) -> None:
    """Delete a just-uploaded blob after a failed DB commit.

    Not durable — if this fails the blob is orphaned and will be cleaned
    up by the reconciler job. We log and move on; the caller's error
    path already returns a user-visible failure.
    """
    try:
        from src.infra.blob_loader import delete_blob
        delete_blob(blob_url)
    except Exception as exc:
        logger.warning("Compensating blob delete failed for %s: %s", blob_url, exc)


@router.post("/kb/upload", response_model=UploadResponse, summary="Upload one or more KB JSON files")
async def upload_kb_files(
    workspace_id: str = Depends(require_workspace_id),
    user: User = Depends(require_user),
    knowledge_files: list[UploadFile] = File(default=[]),
    tool_files: list[UploadFile] = File(default=[]),
):
    if not knowledge_files and not tool_files:
        raise HTTPException(status_code=400, detail="Provide at least one knowledge_files or tool_files entry")

    response = UploadResponse(workspace_id=workspace_id)

    for f in knowledge_files or []:
        info = await _persist_one(workspace_id, "knowledge", f)
        response.knowledge.append(info)

    for f in tool_files or []:
        info = await _persist_one(workspace_id, "tool", f)
        response.tool.append(info)

    record_audit(
        user.id, "file.upload",
        target_type="workspace", target_id=workspace_id,
        workspace_id=workspace_id,
        metadata={
            "knowledge_added": sum(1 for i in response.knowledge if not i.duplicate),
            "tool_added": sum(1 for i in response.tool if not i.duplicate),
            "duplicates": sum(1 for i in response.knowledge + response.tool if i.duplicate),
        },
    )
    return response
