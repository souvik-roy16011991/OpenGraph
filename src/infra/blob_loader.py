"""
Vercel Blob storage adapter — per-workspace layout.

Path scheme:
    {BLOB_STORE_PATH}/{workspace_id}/{knowledge|tool}/<filename>.json

Every operation is parameterised on ``workspace_id`` so two workspaces never
touch the same blob. Callers that are already inside a request (where the
workspace is known from the X-Workspace-Id header) can also use the
context-var variants, which read
:func:`src.workspace_context.require_current_workspace`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import httpx

from src.config import BLOB_READ_WRITE_TOKEN, BLOB_STORE_PATH

logger = logging.getLogger(__name__)

_BLOB_API_BASE = "https://blob.vercel-storage.com"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _store_id() -> str:
    return BLOB_STORE_PATH.split("/")[0]


def _remote_path(workspace_id: str, kb_source: str, filename: str) -> str:
    if kb_source not in ("knowledge", "tool"):
        raise ValueError(f"kb_source must be 'knowledge' or 'tool', got {kb_source!r}")
    safe_name = Path(filename).name or f"{kb_source}.json"
    return f"{BLOB_STORE_PATH}/{workspace_id}/{kb_source}/{safe_name}"


def _auth_headers(content_type: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {BLOB_READ_WRITE_TOKEN}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


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

    Returns the public Blob URL. Raises on HTTP error.
    """
    if not BLOB_READ_WRITE_TOKEN:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is not set; cannot upload to Vercel Blob.")
    remote = _remote_path(workspace_id, kb_source, filename)
    url = f"{_BLOB_API_BASE}/{remote}"
    headers = _auth_headers("application/json")
    headers["x-api-blob-store-id"] = _store_id()
    headers["x-add-random-suffix"] = "0"  # deterministic filenames for idempotent uploads

    resp = httpx.put(url, content=data, headers=headers, timeout=120)
    resp.raise_for_status()
    blob_url: str = resp.json().get("url", url)
    logger.info("Uploaded ws=%s src=%s name=%s -> %s", workspace_id, kb_source, filename, blob_url)
    return blob_url


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
        {BLOB_STORE_PATH}/{workspace_id}/_raw/{kb_source}/{sha256_prefix}_{filename}

    The ``_raw/`` segment keeps these blobs from colliding with the
    parsed JSON written under ``{kb_source}/{filename}.json``. The
    sha256 prefix disambiguates when two files happen to share a name.
    Returns the public Blob URL.
    """
    if not BLOB_READ_WRITE_TOKEN:
        raise RuntimeError("BLOB_READ_WRITE_TOKEN is not set; cannot upload to Vercel Blob.")
    if kb_source not in ("knowledge", "tool"):
        raise ValueError(f"kb_source must be 'knowledge' or 'tool', got {kb_source!r}")
    safe_name = Path(filename).name or "document.bin"
    short = (sha256_prefix or "")[:8] or "unknown"
    remote = f"{BLOB_STORE_PATH}/{workspace_id}/_raw/{kb_source}/{short}_{safe_name}"
    url = f"{_BLOB_API_BASE}/{remote}"
    headers = _auth_headers(content_type or "application/octet-stream")
    headers["x-api-blob-store-id"] = _store_id()
    headers["x-add-random-suffix"] = "0"

    resp = httpx.put(url, content=data, headers=headers, timeout=300)
    resp.raise_for_status()
    blob_url: str = resp.json().get("url", url)
    logger.info(
        "Uploaded raw doc ws=%s src=%s name=%s size=%d -> %s",
        workspace_id, kb_source, safe_name, len(data), blob_url,
    )
    return blob_url


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def list_workspace_blobs(workspace_id: str) -> list[dict[str, Any]]:
    """List blobs under {BLOB_STORE_PATH}/{workspace_id}/."""
    prefix = f"{BLOB_STORE_PATH}/{workspace_id}/"
    resp = httpx.get(f"{_BLOB_API_BASE}?prefix={prefix}", headers=_auth_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json().get("blobs", [])


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_by_url(blob_url: str) -> dict[str, Any]:
    """Download + parse a blob as JSON."""
    resp = httpx.get(blob_url, headers=_auth_headers(), timeout=120, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def download_bytes_by_url(blob_url: str) -> bytes:
    """Download raw bytes from a Blob URL. Raises on HTTP error."""
    resp = httpx.get(blob_url, headers=_auth_headers(), timeout=120, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_workspace_blobs(workspace_id: str) -> int:
    """Delete every blob under the workspace prefix. Returns count deleted."""
    if not BLOB_READ_WRITE_TOKEN:
        return 0
    blobs = list_workspace_blobs(workspace_id)
    urls = [b.get("url") for b in blobs if b.get("url")]
    if not urls:
        return 0
    resp = httpx.post(
        f"{_BLOB_API_BASE}/delete",
        json={"urls": urls},
        headers={**_auth_headers(), "Content-Type": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()
    logger.info("Deleted %d blobs for ws=%s", len(urls), workspace_id)
    return len(urls)


def delete_blob(blob_url: str) -> None:
    """Delete a single blob by URL."""
    if not BLOB_READ_WRITE_TOKEN:
        return
    resp = httpx.post(
        f"{_BLOB_API_BASE}/delete",
        json={"urls": [blob_url]},
        headers={**_auth_headers(), "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
