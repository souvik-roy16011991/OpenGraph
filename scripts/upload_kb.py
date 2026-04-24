#!/usr/bin/env python3
"""
Upload local KB JSON files to Supabase Storage (the object store).

Run this once after initial setup, or whenever KB files change.

Usage
-----
    python scripts/upload_kb.py              # upload both
    python scripts/upload_kb.py --source knowledge
    python scripts/upload_kb.py --source tool
    python scripts/upload_kb.py --list       # list existing objects

Requirements
------------
    The six SUPABASE_* env vars must be set — see src/config.py.
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Ensure project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload KB files to Supabase Storage")
    parser.add_argument(
        "--source",
        choices=["knowledge", "tool"],
        default=None,
        help="Which KB to upload (default: both)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List existing objects and exit",
    )
    args = parser.parse_args()

    from src.config import SUPABASE_BUCKET, USE_SUPABASE_STORAGE

    if not USE_SUPABASE_STORAGE:
        logger.error(
            "Supabase Storage is not configured. "
            "Set the SUPABASE_* env vars in .env before running this script."
        )
        sys.exit(1)

    # NOTE: This is a legacy single-KB helper predating the multi-tenant
    # upload flow. For new uploads prefer POST /api/v1/kb/upload (see the
    # frontend upload page or the public SDK). The functions referenced
    # below (list_kb_blobs / upload_kb_file / upload_kb_files) were
    # retired alongside the multi-workspace refactor; this CLI stays for
    # archival use against the (rare) deployments that still import them.
    from src.infra.blob_loader import list_kb_blobs, upload_kb_file, upload_kb_files

    if args.list:
        blobs = list_kb_blobs()
        if blobs:
            logger.info(f"Found {len(blobs)} object(s) in bucket '{SUPABASE_BUCKET}':")
            for b in blobs:
                size_kb = b.get("size", 0) / 1024
                logger.info(f"  {b.get('pathname', b.get('key', '?'))}  ({size_kb:.1f} KB)  {b.get('url', '')}")
        else:
            logger.info("No objects found.")
        return

    if args.source:
        url = upload_kb_file(args.source)
        print(f"✓ {args.source}  →  {url}")
    else:
        results = upload_kb_files()
        for source, url in results.items():
            print(f"✓ {source}  →  {url}")

    logger.info("Upload complete.")


if __name__ == "__main__":
    main()
