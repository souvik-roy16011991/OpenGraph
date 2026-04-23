"""
Unified process entry point.

Dispatched by ``CMD ["python", "-m", "src.entrypoint"]`` in the Dockerfile.
A single image runs either the FastAPI web server or the build-worker
process depending on the ``APP_MODE`` environment variable:

    APP_MODE=api     → uvicorn src.api.server:app  (default)
    APP_MODE=worker  → asyncio.run(src.worker.build_worker.worker_main())

``WEB_CONCURRENCY`` sets the number of uvicorn worker processes per
instance (defaults to 2). The build-worker intentionally ignores
WEB_CONCURRENCY — scale by running more instances, not more concurrency
inside a single process (see worker docstring for why).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)


def _configure_root_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet noisy 3rd-party loggers that emit brand names we don't want
    # in operator / build logs. Specifically httpx logs every request at
    # INFO with the full URL (which contains "qdrant.io", "upstash.io",
    # etc.). Setting to WARNING hides the per-request traces; errors
    # still come through.
    for name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _run_api() -> None:
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
    logger.info("Starting API: host=0.0.0.0 port=%s workers=%s", port, workers)
    uvicorn.run(
        "src.api.server:app",
        host="0.0.0.0",
        port=port,
        workers=workers,
    )


def _run_worker() -> None:
    from src.worker.build_worker import worker_main
    logger.info("Starting build worker")
    asyncio.run(worker_main())


def main() -> None:
    _configure_root_logging()
    mode = os.environ.get("APP_MODE", "api").strip().lower()
    if mode == "worker":
        _run_worker()
    elif mode in ("api", "web", ""):
        _run_api()
    else:
        logger.error("Unknown APP_MODE=%r — expected 'api' or 'worker'", mode)
        sys.exit(2)


if __name__ == "__main__":
    main()
