"""
Supabase Storage adapter — per-workspace layout, S3-compatible.

Reads go through Supabase's **public object URLs** (plain HTTP GET, no auth
header) — matches the prior Vercel Blob behavior and keeps existing download
helpers and stored ``blob_url`` values working without schema changes.

Writes / lists / deletes go through the S3-compatible endpoint using boto3
SigV4 against an access key id + secret from Supabase → Project Settings →
Storage → S3 Connection.

Path scheme inside the bucket:

    {workspace_id}/{knowledge|tool}/<filename>                   parsed JSON
    {workspace_id}/_raw/{knowledge|tool}/<sha256[:8]>_<file>     raw source doc

Every operation is parameterised on ``workspace_id`` so two workspaces never
touch the same object. The module filename is kept as ``blob_loader.py`` for
import compatibility with existing callers (upload_routes, workspace_routes,
build_jobs, build_worker). A future cleanup can rename the module to
``storage.py`` without behavioral change.
"""

from __future__ import annotations

import hashlib as _hashlib
import logging
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Optional
from urllib.parse import unquote

import httpx

from src.config import (
    SUPABASE_BUCKET,
    SUPABASE_PUBLIC_URL_BASE,
    SUPABASE_S3_ACCESS_KEY_ID,
    SUPABASE_S3_ENDPOINT,
    SUPABASE_S3_REGION,
    SUPABASE_S3_SECRET_ACCESS_KEY,
    USE_SUPABASE_STORAGE,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# boto3 client — lazy singleton. Path-addressing is required; the Supabase S3
# endpoint is bucket-in-URL, not subdomain-routed like AWS.
# ---------------------------------------------------------------------------

_s3_singleton: Any = None


def _s3_client() -> Any:
    global _s3_singleton
    if _s3_singleton is not None:
        return _s3_singleton
    # Import lazily so modules that only need download helpers don't pay the
    # boto3 import cost at collection time.
    import boto3
    from botocore.config import Config

    _s3_singleton = boto3.client(
        "s3",
        endpoint_url=SUPABASE_S3_ENDPOINT,
        region_name=SUPABASE_S3_REGION,
        aws_access_key_id=SUPABASE_S3_ACCESS_KEY_ID,
        aws_secret_access_key=SUPABASE_S3_SECRET_ACCESS_KEY,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )
    return _s3_singleton


def _bucket() -> str:
    return SUPABASE_BUCKET


def _public_url(key: str) -> str:
    return f"{SUPABASE_PUBLIC_URL_BASE}/{SUPABASE_BUCKET}/{key}"


def _key_from_url(blob_url: str) -> str:
    """Extract the S3 object key from a public URL.

    Public URLs look like:
        {SUPABASE_PUBLIC_URL_BASE}/{bucket}/{key}
    where {key} may contain slashes. We strip the base + bucket prefix and
    URL-decode what's left.
    """
    prefix = f"{SUPABASE_PUBLIC_URL_BASE}/{SUPABASE_BUCKET}/"
    if blob_url.startswith(prefix):
        return unquote(blob_url[len(prefix):])
    # Fall back: best-effort split on "/object/public/<bucket>/" so we stay
    # forgiving if an operator rewrites SUPABASE_PUBLIC_URL_BASE.
    marker = f"/object/public/{SUPABASE_BUCKET}/"
    idx = blob_url.find(marker)
    if idx != -1:
        return unquote(blob_url[idx + len(marker):])
    raise ValueError(f"Cannot extract S3 key from url: {blob_url!r}")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _remote_key(workspace_id: str, kb_source: str, filename: str) -> str:
    if kb_source not in ("knowledge", "tool"):
        raise ValueError(f"kb_source must be 'knowledge' or 'tool', got {kb_source!r}")
    safe_name = Path(filename).name or f"{kb_source}.json"
    return f"{workspace_id}/{kb_source}/{safe_name}"


def _raw_remote_key(workspace_id: str, kb_source: str, filename: str, sha256_prefix: str) -> str:
    if kb_source not in ("knowledge", "tool"):
        raise ValueError(f"kb_source must be 'knowledge' or 'tool', got {kb_source!r}")
    safe_name = Path(filename).name or "document.bin"
    short = (sha256_prefix or "")[:8] or "unknown"
    return f"{workspace_id}/_raw/{kb_source}/{short}_{safe_name}"


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_file_bytes(
    workspace_id: str,
    kb_source: str,
    filename: str,
    data: bytes,
) -> str:
    """Upload raw bytes under {workspace_id}/{kb_source}/{filename}.

    Returns the public Supabase object URL. Raises on S3 error.
    """
    if not USE_SUPABASE_STORAGE:
        raise RuntimeError("Supabase Storage is not configured; cannot upload.")
    key = _remote_key(workspace_id, kb_source, filename)
    _s3_client().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=data,
        ContentType="application/json",
    )
    url = _public_url(key)
    logger.info("Uploaded ws=%s src=%s name=%s -> %s", workspace_id, kb_source, filename, url)
    return url


def upload_raw_document(
    workspace_id: str,
    kb_source: str,
    filename: str,
    data: bytes,
    content_type: str,
    sha256_prefix: str,
) -> str:
    """Upload a raw source document (PDF/PPTX/DOCX/…) destined for the
    vision-OCR ingestion pipeline.

    Path scheme:
        {workspace_id}/_raw/{kb_source}/{sha256_prefix}_{filename}

    The ``_raw/`` segment keeps these objects from colliding with the
    parsed JSON written under ``{kb_source}/{filename}.json``. The
    sha256 prefix disambiguates when two files happen to share a name.

    Prefer :func:`upload_raw_document_stream` for large uploads —
    passing ``data`` as bytes buffers the whole file in RAM.
    """
    if not USE_SUPABASE_STORAGE:
        raise RuntimeError("Supabase Storage is not configured; cannot upload.")
    key = _raw_remote_key(workspace_id, kb_source, filename, sha256_prefix)
    _s3_client().put_object(
        Bucket=_bucket(),
        Key=key,
        Body=data,
        ContentType=content_type or "application/octet-stream",
    )
    url = _public_url(key)
    logger.info(
        "Uploaded raw doc ws=%s src=%s name=%s size=%d -> %s",
        workspace_id, kb_source, Path(filename).name, len(data), url,
    )
    return url


# ---------------------------------------------------------------------------
# Streaming upload — avoids buffering the full file in RAM
# ---------------------------------------------------------------------------

# 256 KB chunks for hashing + for the fallback single-shot read. boto3's
# own multipart uploader uses 8 MiB part sizes (see TransferConfig below).
_STREAM_CHUNK = 256 * 1024


def _iter_chunks(fp: BinaryIO, chunk: int = _STREAM_CHUNK) -> Iterator[bytes]:
    while True:
        buf = fp.read(chunk)
        if not buf:
            return
        yield buf


def sha256_and_size_streaming(fp: BinaryIO) -> tuple[str, int]:
    """Compute sha256 + size by streaming through *fp*.

    Rewinds to 0 on entry and exit so the caller can upload the same
    stream right after. Works on any file-like that supports ``seek``;
    FastAPI's ``UploadFile.file`` is a ``SpooledTemporaryFile`` which
    does.
    """
    fp.seek(0)
    hasher = _hashlib.sha256()
    size = 0
    for chunk in _iter_chunks(fp):
        hasher.update(chunk)
        size += len(chunk)
    fp.seek(0)
    return hasher.hexdigest(), size


def upload_raw_document_stream(
    *,
    workspace_id: str,
    kb_source: str,
    filename: str,
    fp: BinaryIO,
    size_bytes: int,
    content_type: str,
    sha256_prefix: str,
) -> str:
    """Streaming variant of :func:`upload_raw_document`.

    Delegates to ``S3.upload_fileobj`` with a TransferConfig that keeps
    per-upload RAM to ~8 MiB (one multipart part) regardless of file
    size. ``use_threads=False`` matches the single-request posture of the
    prior httpx implementation so concurrent uploads don't multiply by
    the thread count.
    """
    if not USE_SUPABASE_STORAGE:
        raise RuntimeError("Supabase Storage is not configured; cannot upload.")
    from boto3.s3.transfer import TransferConfig

    key = _raw_remote_key(workspace_id, kb_source, filename, sha256_prefix)
    fp.seek(0)
    _s3_client().upload_fileobj(
        fp,
        _bucket(),
        key,
        ExtraArgs={"ContentType": content_type or "application/octet-stream"},
        Config=TransferConfig(
            multipart_threshold=8 * 1024 * 1024,
            multipart_chunksize=8 * 1024 * 1024,
            use_threads=False,
        ),
    )
    url = _public_url(key)
    logger.info(
        "Streamed raw doc ws=%s src=%s name=%s size=%d -> %s",
        workspace_id, kb_source, Path(filename).name, size_bytes, url,
    )
    return url


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def list_workspace_blobs(workspace_id: str) -> list[dict[str, Any]]:
    """List every object under ``{workspace_id}/``.

    Returns a list of dicts with at least ``url`` and ``key`` keys; some
    callers read ``size`` too. Pagination is handled transparently via
    ``ContinuationToken``.
    """
    if not USE_SUPABASE_STORAGE:
        return []
    prefix = f"{workspace_id}/"
    out: list[dict[str, Any]] = []
    s3 = _s3_client()
    token: Optional[str] = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": _bucket(), "Prefix": prefix, "MaxKeys": 1000}
        if token is not None:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []) or []:
            key = obj["Key"]
            out.append({"url": _public_url(key), "key": key, "size": obj.get("Size", 0)})
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
        if not token:
            break
    return out


# ---------------------------------------------------------------------------
# Download — public URLs, plain HTTP GET, no auth header required.
# ---------------------------------------------------------------------------

def download_by_url(blob_url: str) -> dict[str, Any]:
    """Download + parse a blob as JSON."""
    resp = httpx.get(blob_url, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def download_bytes_by_url(blob_url: str) -> bytes:
    """Download raw bytes from a public URL. Raises on HTTP error."""
    resp = httpx.get(blob_url, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def _delete_keys_batched(keys: list[str]) -> int:
    """Delete N objects.

    Supabase's S3-compatible endpoint does NOT implement the
    Multi-Object Delete operation (DeleteObjects returns
    ``InvalidRequest: must have required property 'Body'``). Fall back
    to sequential ``DeleteObject`` calls — slower at high volume but
    works on every S3-compatible backend we might target. A single
    workspace wipe is typically dozens to low-hundreds of objects, so
    the latency hit is acceptable.
    """
    if not keys:
        return 0
    s3 = _s3_client()
    bucket = _bucket()
    deleted = 0
    for key in keys:
        try:
            s3.delete_object(Bucket=bucket, Key=key)
            deleted += 1
        except Exception as exc:
            logger.warning("Delete failed: key=%s err=%s", key, exc)
    return deleted


def delete_workspace_blobs(workspace_id: str) -> int:
    """Delete every object under the workspace prefix. Returns count deleted."""
    if not USE_SUPABASE_STORAGE:
        return 0
    objs = list_workspace_blobs(workspace_id)
    if not objs:
        return 0
    keys = [o["key"] for o in objs]
    n = _delete_keys_batched(keys)
    logger.info("Deleted %d objects for ws=%s", n, workspace_id)
    return n


def delete_blob(blob_url: str) -> None:
    """Delete a single object by URL."""
    if not USE_SUPABASE_STORAGE:
        return
    key = _key_from_url(blob_url)
    _s3_client().delete_object(Bucket=_bucket(), Key=key)
