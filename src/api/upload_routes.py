"""
KB JSON upload endpoints — per-workspace, multi-file.

POST /api/v1/kb/upload (X-Workspace-Id required)
  - Accepts any number of ``knowledge_files`` and any number of ``tool_files``
    in a single multipart request.
  - Each file is validated, sha256-hashed, persisted locally under
    /data/workspaces/{ws_id}/uploads/{kb_source}/{filename}, mirrored to
    Vercel Blob at {BLOB_STORE_PATH}/{ws_id}/{kb_source}/{filename}, and
    recorded as a WorkspaceFile + kb_uploads row.
  - Duplicates (same sha256 for same kb_source in this workspace) are
    silently skipped.

The graph build pipeline reads the list of active WorkspaceFile rows and
merges all files of each kb_source into one ParsedKB.
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
from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.api.deps import require_workspace_id
from src.config import BLOB_READ_WRITE_TOKEN, USE_NEON, workspace_data_dir
from src.infra.db import get_session
from src.infra.db_models import KbUpload, WorkspaceFile

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
    local_path: str
    blob_url: Optional[str] = None
    blob_error: Optional[str] = None
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


def _upload_dir(workspace_id: str, kb_source: str) -> Path:
    d = workspace_data_dir(workspace_id) / "uploads" / kb_source
    d.mkdir(parents=True, exist_ok=True)
    return d


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
                local_path=existing.local_path or "",
                blob_url=existing.blob_url,
                duplicate=True,
            )

    # Persist locally
    local_path = _upload_dir(workspace_id, kb_source) / filename
    local_path.write_bytes(raw)

    # Mirror to Vercel Blob (best effort)
    blob_url: Optional[str] = None
    blob_error: Optional[str] = None
    if BLOB_READ_WRITE_TOKEN:
        try:
            from src.infra.blob_loader import upload_file_bytes
            blob_url = upload_file_bytes(workspace_id, kb_source, filename, raw)
        except Exception as exc:
            blob_error = str(exc)
            logger.warning("Blob mirror for ws=%s %s/%s failed: %s", workspace_id, kb_source, filename, exc)

    # Record in Neon
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
            local_path=str(local_path),
            active=True,
        )
        s.add(wf)
        # Also audit into kb_uploads
        s.add(KbUpload(
            workspace_id=uuid.UUID(workspace_id),
            kb_source=kb_source,
            filename=filename,
            size_bytes=len(raw),
            chapters=len(payload.get("chapters", [])),
            title=payload.get("title"),
            sha256=sha,
            blob_url=blob_url,
            blob_error=blob_error,
        ))
        await s.commit()
        await s.refresh(wf)

    return UploadedFileInfo(
        id=wf.id,
        kb_source=kb_source,
        filename=filename,
        size_bytes=len(raw),
        chapters=len(payload.get("chapters", [])),
        title=payload.get("title"),
        sha256=sha,
        local_path=str(local_path),
        blob_url=blob_url,
        blob_error=blob_error,
        duplicate=False,
    )


@router.post("/kb/upload", response_model=UploadResponse, summary="Upload one or more KB JSON files")
async def upload_kb_files(
    workspace_id: str = Depends(require_workspace_id),
    knowledge_files: list[UploadFile] = File(default=[]),
    tool_files: list[UploadFile] = File(default=[]),
):
    if not knowledge_files and not tool_files:
        raise HTTPException(status_code=400, detail="Provide at least one knowledge_files or tool_files entry")

    response = UploadResponse(workspace_id=workspace_id)

    for f in knowledge_files or []:
        info = await _persist_one(workspace_id, "knowledge", f)
        response.knowledge.append(info)
        if info.blob_error:
            response.warnings.append(f"knowledge/{info.filename}: blob mirror failed ({info.blob_error})")

    for f in tool_files or []:
        info = await _persist_one(workspace_id, "tool", f)
        response.tool.append(info)
        if info.blob_error:
            response.warnings.append(f"tool/{info.filename}: blob mirror failed ({info.blob_error})")

    return response
