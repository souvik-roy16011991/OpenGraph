"""
KB upload endpoints — per-workspace, multi-file, cloud-only.

POST /api/v1/kb/upload (X-Workspace-Id required)
  - Accepts any number of ``knowledge_files`` and any number of ``tool_files``
    in a single multipart request.
  - Each file is sniffed by content:
      * JSON  → validated, sha256-hashed, uploaded to Supabase Storage at
        {bucket}/{ws_id}/{kb_source}/{filename}, recorded as a
        WorkspaceFile + kb_uploads row in Neon. Returns the full file
        info synchronously.
      * PDF / PPTX / DOCX / XLSX / ODP / ODT / ODS / RTF (etc.) → raw
        bytes are uploaded to _raw/... in Supabase Storage, a ParseJob is
        enqueued, and the response returns {parse_job_id, status:"queued"}.
        The vision-OCR worker picks it up, produces the parsed JSON, and
        inserts the WorkspaceFile row on completion. Frontend polls
        GET /api/v1/kb/parse-jobs/{job_id}.
  - JSON dedup by sha256(raw bytes). Raw-doc dedup by sha256 of the
    source document — same PPTX uploaded twice short-circuits to the
    existing parsed WorkspaceFile without a second OCR pass.

Nothing is written to the local filesystem — Supabase Storage is the single
source of truth for uploaded KB content. If the storage upload fails the
whole request fails; there is no local fallback to drift out of sync.
"""

from __future__ import annotations

import asyncio
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
from src.config import USE_SUPABASE_STORAGE
from src.infra.audit import record_audit
from src.infra.db import get_session
from src.infra.db_models import KbUpload, User, WorkspaceFile
from src.kb.doc_parser.mime import MIME_BY_KIND, SUPPORTED_DOC_KINDS, sniff

logger = logging.getLogger(__name__)

router = APIRouter()


class UploadedFileInfo(BaseModel):
    # For JSON uploads + completed parses, all fields are set. For a
    # freshly-enqueued raw-doc parse the WorkspaceFile row doesn't exist
    # yet, so `id` + `chapters` + `title` remain at their placeholder
    # values and `status` is "queued"; the caller polls `parse_job_id`
    # to learn when the real WorkspaceFile is ready.
    id: Optional[int] = None
    kb_source: str
    filename: str
    size_bytes: int
    chapters: int = 0
    title: Optional[str] = None
    sha256: str
    blob_url: str = ""
    duplicate: bool = False
    # Set only when the upload went through the raw-doc path. Consumers
    # use this as the discriminator: if present, render a progress card
    # instead of a completed file row.
    parse_job_id: Optional[str] = None
    status: str = "done"  # "done" | "queued"


class UploadResponse(BaseModel):
    workspace_id: str
    knowledge: list[UploadedFileInfo] = []
    tool: list[UploadedFileInfo] = []
    warnings: list[str] = []


# Cap per-file upload. A 100 MB PDF is already a stretch; anything larger
# is almost certainly a mistake and burns OpenRouter quota if it parses.
MAX_RAW_DOC_BYTES = 100 * 1024 * 1024


def _validate_json_payload(raw: bytes, label: str) -> dict[str, Any]:
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
    return payload


def _safe_json_filename(filename: Optional[str], kb_source: str) -> str:
    base = Path(filename or f"{kb_source}.json").name
    if not base.lower().endswith(".json"):
        base += ".json"
    return base


def _safe_doc_filename(filename: Optional[str], kb_source: str, kind: str) -> str:
    """Keep the caller's original filename + extension for UI/debuggability.

    Used for both the raw blob name and the WorkspaceFile.filename once
    the parse completes. A missing/empty filename falls back to
    ``{kb_source}.{kind}``.
    """
    base = Path(filename or f"{kb_source}.{kind}").name
    return base or f"{kb_source}.{kind}"


async def _persist_one(
    workspace_id: str,
    kb_source: str,
    upload: UploadFile,
    user: User,
) -> UploadedFileInfo:
    """Per-file dispatch.

    JSON files take the existing (small, validated-in-memory) path. Raw
    documents take the streaming path — we never call ``upload.read()``
    on docs, so a 200-file × 20 MB batch peaks at a few hundred KB of
    in-flight buffer instead of 4 GB.
    """
    # Peek at the magic bytes without draining the stream. FastAPI's
    # UploadFile.file is a SpooledTemporaryFile — reading is sync and
    # cheap even at 200 concurrent calls.
    fp = upload.file
    fp.seek(0)
    head = fp.read(16)
    fp.seek(0)
    if not head:
        raise HTTPException(status_code=400, detail=f"{kb_source} upload is empty")

    kind = sniff(head, upload.filename)

    if kind == "json":
        # Small by definition — OK to buffer + validate.
        raw = fp.read()
        return await _persist_json(workspace_id, kb_source, upload, raw)

    if kind in SUPPORTED_DOC_KINDS:
        # Size is known without materialising the bytes.
        try:
            fp.seek(0, 2)  # 2 = SEEK_END
            size_bytes = fp.tell()
            fp.seek(0)
        except OSError:
            # Fallback: drain once to measure. Should never trigger for
            # SpooledTemporaryFile, which supports seek natively.
            size_bytes = len(fp.read())
            fp.seek(0)
        if size_bytes > MAX_RAW_DOC_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"{kb_source} document exceeds the {MAX_RAW_DOC_BYTES // (1024*1024)} MB "
                    "upload cap. Split it into smaller files."
                ),
            )
        return await _persist_raw_document(
            workspace_id, kb_source, upload, size_bytes, kind, user,
        )

    raise HTTPException(
        status_code=415,
        detail=(
            f"{kb_source} file type not supported. Accepted: JSON, PDF, PPTX, PPT, "
            "DOCX, DOC, XLSX, XLS, ODP, ODT, ODS, RTF."
        ),
    )


async def _persist_json(
    workspace_id: str,
    kb_source: str,
    upload: UploadFile,
    raw: bytes,
) -> UploadedFileInfo:
    payload = _validate_json_payload(raw, kb_source)
    sha = hashlib.sha256(raw).hexdigest()
    filename = _safe_json_filename(upload.filename, kb_source)

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

    # Upload to Supabase Storage — the single source of truth. Fail the
    # request if this fails; we will not silently fall back to local disk.
    if not USE_SUPABASE_STORAGE:
        raise HTTPException(
            status_code=503,
            detail="Supabase Storage is not configured; cannot accept uploads.",
        )
    try:
        from src.infra.blob_loader import upload_file_bytes
        blob_url = upload_file_bytes(workspace_id, kb_source, filename, raw)
    except Exception as exc:
        logger.error("Supabase Storage upload failed for ws=%s %s/%s: %s", workspace_id, kb_source, filename, exc)
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


async def _persist_raw_document(
    workspace_id: str,
    kb_source: str,
    upload: UploadFile,
    size_bytes: int,
    kind: str,
    user: User,
) -> UploadedFileInfo:
    """Enqueue a vision-OCR parse for a non-JSON upload.

    Streaming version: sha256 is computed by reading the underlying
    SpooledTemporaryFile in 256 KB chunks (not by buffering the whole
    file), and the PUT to Vercel Blob streams the same file. Peak RAM
    per call is ~256 KB regardless of the document size. At 200
    concurrent uploads in one request, the in-flight buffer budget is
    ~200 × 256 KB = 50 MB — well within Render starter's 512 MB.
    """
    from src.infra.blob_loader import (
        sha256_and_size_streaming,
        upload_raw_document_stream,
    )

    # Billing / fairness guardrail. Runs BEFORE the blob upload so a
    # blocked request never creates an orphaned raw blob. Raises
    # PaymentRequired (HTTP 402) on trial cap, overdraft, per-user
    # queue cap (USER_PARSE_QUEUE_CAP), or daily cost ceiling
    # (USER_DAILY_PARSE_USD) — see src.billing.enforcement.
    from src.billing import check_document_parse_allowed
    await check_document_parse_allowed(user.id)

    fp = upload.file  # SpooledTemporaryFile, disk-backed past 1 MB
    # Two-pass: stream-hash, then stream-upload. Both seek back to 0.
    sha, streamed_size = await asyncio.to_thread(sha256_and_size_streaming, fp)
    if streamed_size != size_bytes:
        # Should never fire — both use the same seek(0)+read loop. If
        # it ever does, the size_bytes from SEEK_END is authoritative.
        size_bytes = streamed_size

    filename = _safe_doc_filename(upload.filename, kb_source, kind)

    # Dedup by source document sha256. Two scopes:
    #   (a) a previously-parsed WorkspaceFile (same raw doc, already
    #       completed) → short-circuit to the existing row. No OCR.
    #   (b) an in-flight parse_jobs row (same raw doc, still queued or
    #       running from this or an earlier batch) → point the caller
    #       at the existing job_id so they track it instead of paying
    #       for a second OCR pass.
    from src.infra.db_models import ParseJobRow
    async with get_session() as s:
        existing = (await s.execute(
            select(WorkspaceFile).where(
                WorkspaceFile.workspace_id == uuid.UUID(workspace_id),
                WorkspaceFile.kb_source == kb_source,
                WorkspaceFile.source_document_sha256 == sha,
            )
        )).scalar_one_or_none()
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
                status="done",
            )

        in_flight = (await s.execute(
            select(ParseJobRow).where(
                ParseJobRow.workspace_id == uuid.UUID(workspace_id),
                ParseJobRow.kb_source == kb_source,
                ParseJobRow.source_document_sha256 == sha,
                ParseJobRow.status.in_(("queued", "running")),
            )
        )).scalar_one_or_none()
        if in_flight is not None:
            return UploadedFileInfo(
                id=None,
                kb_source=in_flight.kb_source,
                filename=in_flight.filename,
                size_bytes=int(in_flight.source_document_size),
                chapters=0,
                title=None,
                sha256=sha,
                blob_url=in_flight.source_document_url,
                duplicate=True,
                parse_job_id=in_flight.job_id,
                status="queued",
            )

    if not USE_SUPABASE_STORAGE:
        raise HTTPException(
            status_code=503,
            detail="Supabase Storage is not configured; cannot accept uploads.",
        )

    content_type = MIME_BY_KIND.get(kind, "application/octet-stream")

    try:
        raw_url = await asyncio.to_thread(
            upload_raw_document_stream,
            workspace_id=workspace_id,
            kb_source=kb_source,
            filename=filename,
            fp=fp,
            size_bytes=size_bytes,
            content_type=content_type,
            sha256_prefix=sha,
        )
    except Exception as exc:
        logger.error(
            "Supabase Storage raw upload failed for ws=%s %s/%s: %s",
            workspace_id, kb_source, filename, exc,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Vercel Blob upload failed: {exc}",
        )

    # Enqueue. Worker claims via parse_queue.claim_next.
    try:
        from src.api import parse_queue
        enq = await parse_queue.enqueue(
            workspace_id=workspace_id,
            user_id=str(user.id),
            kb_source=kb_source,
            filename=filename,
            source_document_url=raw_url,
            source_document_sha256=sha,
            source_document_size=size_bytes,
            source_mime=content_type,
        )
    except Exception as exc:
        logger.error(
            "ParseJob enqueue failed for ws=%s %s/%s: %s",
            workspace_id, kb_source, filename, exc,
        )
        _best_effort_blob_delete(raw_url)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to enqueue parse job: {exc}",
        )

    return UploadedFileInfo(
        id=None,
        kb_source=kb_source,
        filename=filename,
        size_bytes=size_bytes,
        chapters=0,
        title=None,
        sha256=sha,
        blob_url=raw_url,   # raw doc URL until parse completes
        duplicate=False,
        parse_job_id=enq["job_id"],
        status="queued",
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


# Per-request concurrency for blob PUTs + dedup SELECTs + enqueue INSERTs.
# Tuning: 8 in flight saturates Vercel Blob's single-connection bandwidth
# without overwhelming Neon's connection pool (DB_POOL_SIZE=2,
# MAX_OVERFLOW=10 → 12 max). Overridable via env for operators tuning
# on a larger DB pool.
import os as _os
UPLOAD_CONCURRENCY = max(1, int(_os.environ.get("UPLOAD_CONCURRENCY", "8")))


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
    sem = asyncio.Semaphore(UPLOAD_CONCURRENCY)

    # Per-file worker. Isolates failure: if file #N fails, files 1..N-1
    # and N+1..M still land in the response. Failures are recorded in
    # `warnings[]` so the caller sees exactly which filenames didn't
    # make it, without blowing up the whole batch.
    async def _one(
        kb_source: str, upload: UploadFile,
    ) -> tuple[str, str, UploadedFileInfo | None, str | None]:
        fname = upload.filename or f"{kb_source}.bin"
        async with sem:
            try:
                info = await _persist_one(workspace_id, kb_source, upload, user)
                return kb_source, fname, info, None
            except HTTPException as exc:
                return kb_source, fname, None, f"{exc.status_code}: {exc.detail}"
            except Exception as exc:
                logger.exception("per-file persist crashed for %s/%s", kb_source, fname)
                return kb_source, fname, None, f"internal error: {type(exc).__name__}: {exc}"

    tasks: list = []
    for f in knowledge_files or []:
        tasks.append(_one("knowledge", f))
    for f in tool_files or []:
        tasks.append(_one("tool", f))

    # return_exceptions=False because _one already swallows and returns a
    # tagged failure — if gather itself raises, it's a framework bug we
    # want to see.
    results = await asyncio.gather(*tasks)

    for kb_source, fname, info, err in results:
        if info is not None:
            (response.knowledge if kb_source == "knowledge" else response.tool).append(info)
        else:
            response.warnings.append(f"{kb_source}/{fname}: {err}")

    all_infos = response.knowledge + response.tool
    record_audit(
        user.id, "file.upload",
        target_type="workspace", target_id=workspace_id,
        workspace_id=workspace_id,
        metadata={
            "knowledge_added": sum(1 for i in response.knowledge if not i.duplicate and i.status == "done"),
            "tool_added": sum(1 for i in response.tool if not i.duplicate and i.status == "done"),
            "duplicates": sum(1 for i in all_infos if i.duplicate),
            "parse_jobs_queued": sum(1 for i in all_infos if i.parse_job_id is not None),
            "failures": len(response.warnings),
            "batch_size": len(tasks),
        },
    )
    return response
