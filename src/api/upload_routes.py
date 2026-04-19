"""
KB JSON upload endpoints.

Accepts multipart uploads of knowledge.json and/or tool.json, validates the
structure (must have `chapters: [...]`), writes to kb-config/{knowledge,tool}/,
and mirrors to Vercel Blob when BLOB_READ_WRITE_TOKEN is set. Blob upload
failures are non-fatal.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from src.config import BLOB_READ_WRITE_TOKEN
from src.kb_config import get_active_kb_config, reset_active_kb_config

logger = logging.getLogger(__name__)

router = APIRouter()


class UploadedFileInfo(BaseModel):
    path: str
    filename: str
    size: int
    chapters: int
    title: Optional[str] = None
    blob_url: Optional[str] = None
    blob_error: Optional[str] = None


class UploadResponse(BaseModel):
    knowledge: Optional[UploadedFileInfo] = None
    tool: Optional[UploadedFileInfo] = None
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


def _write_kb_file(kb_source: str, filename: str, raw: bytes) -> Path:
    cfg = get_active_kb_config()
    target_dir = cfg.root / kb_source
    target_dir.mkdir(parents=True, exist_ok=True)

    # Clear any existing .json siblings so _pick_kb_file() picks the new one
    for old in target_dir.glob("*.json"):
        try:
            old.unlink()
        except OSError:
            pass

    safe_name = Path(filename).name or f"{kb_source}.json"
    if not safe_name.lower().endswith(".json"):
        safe_name += ".json"
    target_path = target_dir / safe_name
    target_path.write_bytes(raw)
    return target_path


def _maybe_upload_to_blob(kb_source: str) -> tuple[Optional[str], Optional[str]]:
    """Return (blob_url, error). Non-fatal; logs warning on failure."""
    if not BLOB_READ_WRITE_TOKEN:
        return None, None
    try:
        from src.infra.blob_loader import upload_kb_file
        url = upload_kb_file(kb_source)
        return url, None
    except Exception as exc:
        logger.warning("Vercel Blob upload for %s failed: %s", kb_source, exc)
        return None, str(exc)


@router.post("/kb/upload", response_model=UploadResponse, summary="Upload KB JSON files")
async def upload_kb_files(
    knowledge_file: Optional[UploadFile] = File(default=None),
    tool_file: Optional[UploadFile] = File(default=None),
):
    """Accept knowledge and/or tool KB JSONs; persist locally and mirror to Vercel Blob."""
    if knowledge_file is None and tool_file is None:
        raise HTTPException(status_code=400, detail="Provide at least one of knowledge_file / tool_file")

    response = UploadResponse()

    if knowledge_file is not None:
        raw, payload = await _read_and_validate(knowledge_file, "knowledge")
        path = _write_kb_file("knowledge", knowledge_file.filename or "knowledge.json", raw)
        # Clear cached active config so the new file is picked up.
        reset_active_kb_config()
        blob_url, blob_err = _maybe_upload_to_blob("knowledge")
        response.knowledge = UploadedFileInfo(
            path=str(path),
            filename=path.name,
            size=len(raw),
            chapters=len(payload.get("chapters", [])),
            title=payload.get("title"),
            blob_url=blob_url,
            blob_error=blob_err,
        )
        if blob_err:
            response.warnings.append(f"knowledge: blob mirror failed ({blob_err})")

    if tool_file is not None:
        raw, payload = await _read_and_validate(tool_file, "tool")
        path = _write_kb_file("tool", tool_file.filename or "tool.json", raw)
        reset_active_kb_config()
        blob_url, blob_err = _maybe_upload_to_blob("tool")
        response.tool = UploadedFileInfo(
            path=str(path),
            filename=path.name,
            size=len(raw),
            chapters=len(payload.get("chapters", [])),
            title=payload.get("title"),
            blob_url=blob_url,
            blob_error=blob_err,
        )
        if blob_err:
            response.warnings.append(f"tool: blob mirror failed ({blob_err})")

    return response
