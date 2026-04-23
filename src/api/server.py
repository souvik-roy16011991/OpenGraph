"""
FastAPI application factory for the KB Knowledge Graph Engine.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.routes import router, set_knowledge_graph
from src.kb_config import get_active_kb_config

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Load the KnowledgeGraph + initialise Neon on startup."""
    # Capture the running event loop so background build threads can schedule
    # async DB writes onto it via run_coroutine_threadsafe.
    import asyncio
    from src.infra import db as _db
    _db.set_main_loop(asyncio.get_running_loop())

    # Initialise Neon tables (no-op when DATABASE_URL is blank).
    # NOTE: We deliberately do NOT call mark_orphaned_running_jobs() anymore.
    # With horizontal scaling + a dedicated worker pool, the API is no
    # longer the source of truth for build state — the worker's heartbeat
    # sweeper owns liveness. An API restart must not touch rows owned by
    # a still-healthy worker elsewhere.
    try:
        await _db.init_db()
    except Exception as exc:
        logger.warning("Neon init failed (continuing without persistence): %s", exc)

    # Multi-workspace mode: graphs are loaded lazily per workspace on first
    # scoped request, not eagerly at startup. The legacy singleton path is
    # still reachable via scripts/build_graph.py for local CLI use.
    logger.info("KB Knowledge Graph Engine ready. Workspaces load on demand.")
    yield
    logger.info("Shutting down KB Knowledge Graph Engine.")


def create_app() -> FastAPI:
    # Resolve the active kb-config (honors KB_CONFIG_PATH env var) so the
    # description reflects the active domain, and so the graph load at startup
    # uses the correct paths/profile.
    cfg = get_active_kb_config()
    profile = cfg.profile

    app = FastAPI(
        title=f"{profile.domain_display_name} Knowledge Graph Engine",
        description=(
            f"Tree-based knowledge graph engine for {profile.organization_name}. "
            "Navigate domain knowledge and technology systems step-by-step."
        ),
        version="1.0.0",
        lifespan=lifespan,
    )

    # CORS — driven by CORS_ALLOW_ORIGINS (comma-separated) with a "*" default
    # suitable for dev. In Render production, set this to the frontend origin
    # (e.g. https://kb-frontend-xxxx.onrender.com). Credentials can only be
    # allowed with an exact origin list; with "*" we fall back to no-credentials.
    #
    # Render's `fromService.property: host` returns a bare hostname. Browsers
    # send the full `Origin: https://host` header, so normalise each entry to
    # include a scheme — otherwise every preflight would 400 in production.
    import os as _os
    raw_origins = _os.environ.get("CORS_ALLOW_ORIGINS", "*").strip()

    def _normalise_origin(o: str) -> str:
        o = o.strip()
        if not o or o == "*":
            return o
        if "://" in o:
            return o
        return f"https://{o}"

    origins = [_normalise_origin(o) for o in raw_origins.split(",") if o.strip()] or ["*"]
    allow_credentials = origins != ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Process-Time"],
    )

    # Request timing middleware
    @app.middleware("http")
    async def add_timing_header(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        response.headers["X-Process-Time"] = f"{elapsed:.3f}s"
        return response

    # Mount API routes
    app.include_router(router, prefix="/api/v1")

    # Developer API: key management (JWT-auth) + public /ext/* surface (API-key auth).
    # Kept in separate routers from the internal surface so their security
    # schemes and route prefixes stay isolated in the OpenAPI spec.
    from src.api.ext_routes import router as ext_router
    from src.api.keys_routes import router as keys_router
    app.include_router(keys_router, prefix="/api/v1")
    app.include_router(ext_router, prefix="/api/v1")

    # ---- Custom OpenAPI override ----
    # FastAPI's auto-generated spec has no security scheme; we inject one
    # here so SDK codegen (datamodel-code-generator, openapi-typescript)
    # understands /ext/* paths require an API-key bearer. Path-level security
    # is applied only to /ext/* — the rest of the internal surface stays
    # unauthenticated in the spec (the dashboard passes JWTs via cookie).
    from fastapi.openapi.utils import get_openapi

    def _custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["ApiKeyAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "og_live",
            "description": (
                "Developer API key in the format ``og_live_<secret>``. "
                "Mint one at /api-keys in the dashboard."
            ),
        }
        for path, methods in schema.get("paths", {}).items():
            if "/ext/" not in path:
                continue
            for op in methods.values():
                if not isinstance(op, dict):
                    continue
                op.setdefault("security", [{"ApiKeyAuth": []}])
        app.openapi_schema = schema
        return schema

    app.openapi = _custom_openapi  # type: ignore[method-assign]

    @app.get("/api/v1/ext/openapi.json", include_in_schema=False, tags=["ext"])
    async def ext_only_openapi():
        """Return the OpenAPI spec filtered to the public /ext/* surface.

        Used by the dashboard ``/api-docs`` page and by the SDK codegen
        pipelines so generated types only cover the third-party-safe API,
        not the internal workspace / billing routes.
        """
        full = app.openapi()
        paths = {k: v for k, v in full.get("paths", {}).items() if "/ext/" in k}
        return {
            **{k: v for k, v in full.items() if k != "paths"},
            "paths": paths,
        }

    # Health check
    @app.get("/health", tags=["health"])
    async def health():
        from src.config import (
            USE_BLOB_STORAGE,
            USE_MEMGRAPH,
            USE_NEON,
            USE_QDRANT,
            USE_UPSTASH,
        )
        backends = {
            "neon": USE_NEON,
            "memgraph": USE_MEMGRAPH,
            "qdrant": USE_QDRANT,
            "vercel_blob": USE_BLOB_STORAGE,
            "upstash": USE_UPSTASH,
        }
        return {"status": "ok", "service": "kb-knowledge-graph", "backends": backends}

    # Root redirect
    @app.get("/", tags=["root"])
    async def root():
        return {
            "service": "KB Knowledge Graph Engine",
            "version": "1.0.0",
            "docs": "/docs",
            "api": "/api/v1",
            "endpoints": {
                "query": "POST /api/v1/query",
                "node": "GET /api/v1/graph/node/{node_id}",
                "tree": "GET /api/v1/graph/tree",
                "tools": "GET /api/v1/graph/tools",
                "traverse": "POST /api/v1/graph/traverse",
                "search": "GET /api/v1/graph/search?q=...",
                "stats": "GET /api/v1/graph/stats",
                "chapters": "GET /api/v1/graph/chapters",
                "visualization": "GET /api/v1/graph/visualization",
                "upload_kb": "POST /api/v1/kb/upload",
                "get_domain": "GET /api/v1/config/domain",
                "put_domain": "PUT /api/v1/config/domain",
                "get_graph_cfg": "GET /api/v1/config/graph",
                "put_graph_cfg": "PUT /api/v1/config/graph",
                "build": "POST /api/v1/build",
                "build_status": "GET /api/v1/build/{job_id}",
            },
        }

    return app


app = create_app()


if __name__ == "__main__":
    import os
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)
