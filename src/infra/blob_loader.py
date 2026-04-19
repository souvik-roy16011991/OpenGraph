"""
Vercel Blob storage adapter.

Provides helpers to upload and download KB JSON files to/from Vercel Blob
using the REST API (no official Python SDK required — plain requests/httpx).

Storage layout
--------------
All files are stored at:
    {BLOB_STORE_PATH}/knowledge/<filename>.json
    {BLOB_STORE_PATH}/tool/<filename>.json

where BLOB_STORE_PATH defaults to "v0-it-support-automation-blob/kb-config".

The store_id (the part before the first "/") is extracted from BLOB_STORE_PATH
and passed as the x-api-blob-store-id header for upload operations.

Usage
-----
    from src.infra.blob_loader import upload_kb_files, download_kb_file

    # One-time upload
    upload_kb_files()

    # During build when USE_BLOB_STORAGE is True
    raw = download_kb_file("knowledge")   # or "tool"
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx

from src.config import (
    BLOB_READ_WRITE_TOKEN,
    BLOB_STORE_PATH,
)
from src.kb_config import get_active_kb_config

logger = logging.getLogger(__name__)

_BLOB_API_BASE = "https://blob.vercel-storage.com"


def _kb_source_path(kb_source: str) -> Path:
    cfg = get_active_kb_config()
    if kb_source == "knowledge":
        return cfg.knowledge_kb_path
    if kb_source == "tool":
        return cfg.tool_kb_path
    raise ValueError(f"kb_source must be 'knowledge' or 'tool', got '{kb_source}'")


def _store_id() -> str:
    """Extract the Vercel Blob store ID from BLOB_STORE_PATH."""
    return BLOB_STORE_PATH.split("/")[0]


def _remote_path(kb_source: str) -> str:
    """
    Build the full blob pathname for a kb_source.

    e.g. "knowledge" → "v0-it-support-automation-blob/kb-config/knowledge/Wealth_Management_Knowledge_Base.json"
    """
    local_path = _kb_source_path(kb_source)
    sub_dir = kb_source  # "knowledge" or "tool"
    return f"{BLOB_STORE_PATH}/{sub_dir}/{local_path.name}"


def _auth_headers(content_type: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {BLOB_READ_WRITE_TOKEN}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_kb_file(kb_source: str) -> str:
    """
    Upload a single KB JSON file to Vercel Blob.

    Parameters
    ----------
    kb_source : "knowledge" or "tool"

    Returns
    -------
    The public URL of the uploaded blob.
    """
    local_path = _kb_source_path(kb_source)
    if not local_path.exists():
        raise FileNotFoundError(f"KB file not found: {local_path}")

    remote = _remote_path(kb_source)
    url = f"{_BLOB_API_BASE}/{remote}"

    logger.info(f"Uploading {local_path.name} → {url} …")
    data = local_path.read_bytes()

    headers = _auth_headers("application/json")
    headers["x-api-blob-store-id"] = _store_id()
    headers["x-add-random-suffix"] = "0"  # keep deterministic filename

    resp = httpx.put(url, content=data, headers=headers, timeout=120)
    resp.raise_for_status()

    blob_url: str = resp.json().get("url", url)
    logger.info(f"Uploaded {kb_source} KB → {blob_url}")
    return blob_url


def upload_kb_files() -> dict[str, str]:
    """
    Upload both KB JSON files.

    Returns
    -------
    Dict mapping kb_source → blob URL.
    """
    results: dict[str, str] = {}
    for source in ("knowledge", "tool"):
        results[source] = upload_kb_file(source)
    return results


# ---------------------------------------------------------------------------
# List / discovery
# ---------------------------------------------------------------------------

def list_kb_blobs() -> list[dict[str, Any]]:
    """List all blobs under the KB prefix in the store."""
    prefix = BLOB_STORE_PATH
    url = f"{_BLOB_API_BASE}?prefix={prefix}/"
    resp = httpx.get(url, headers=_auth_headers(), timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get("blobs", [])


def _get_blob_url(kb_source: str) -> str | None:
    """Resolve the public URL of the blob for a given kb_source by listing."""
    remote = _remote_path(kb_source)
    blobs = list_kb_blobs()
    for blob in blobs:
        pathname = blob.get("pathname", "")
        if pathname == remote or pathname.endswith(f"/{Path(remote).name}"):
            return blob.get("url") or blob.get("downloadUrl")
    return None


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_kb_file(kb_source: str) -> dict[str, Any]:
    """
    Download a KB JSON file from Vercel Blob and return the parsed dict.

    Parameters
    ----------
    kb_source : "knowledge" or "tool"

    Returns
    -------
    Parsed JSON dict (same structure as reading the local file).
    """
    # Raises ValueError if kb_source is invalid.
    _kb_source_path(kb_source)

    # Try to discover the public URL from the blob listing
    blob_url = _get_blob_url(kb_source)

    if not blob_url:
        # Fallback: construct the URL directly (works if the blob is public)
        remote = _remote_path(kb_source)
        blob_url = f"{_BLOB_API_BASE}/{remote}"
        logger.warning(
            f"Blob URL for '{kb_source}' not found in listing; "
            f"trying direct URL: {blob_url}"
        )

    logger.info(f"Downloading {kb_source} KB from {blob_url} …")
    resp = httpx.get(blob_url, headers=_auth_headers(), timeout=120, follow_redirects=True)
    resp.raise_for_status()

    raw: dict[str, Any] = resp.json()
    logger.info(
        f"Downloaded {kb_source} KB "
        f"({len(resp.content) / 1024:.1f} KB, "
        f"{len(raw.get('chapters', []))} chapters)"
    )
    return raw
