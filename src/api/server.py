"""
FastAPI application factory for the KB Knowledge Graph Engine.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.routes import router, set_knowledge_graph
from src.config import GRAPH_PICKLE_PATH
from src.kb_config import get_active_kb_config

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Load the KnowledgeGraph on startup."""
    graph_path = Path(GRAPH_PICKLE_PATH)
    if not graph_path.exists():
        logger.warning(
            f"Graph file not found at {graph_path}. "
            "Run `python scripts/build_graph.py` to build it first."
        )
    else:
        from src.graph_builder.builder import KnowledgeGraph
        kg = KnowledgeGraph.load()
        set_knowledge_graph(kg)
        logger.info("KnowledgeGraph loaded and ready.")
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

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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

    # Health check
    @app.get("/health", tags=["health"])
    async def health():
        return {"status": "ok", "service": "kb-knowledge-graph"}

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
