FROM python:3.11-slim AS base

WORKDIR /app

# System deps: gcc/g++ for faiss-cpu + numpy wheels, libpq for psycopg fallback
# (asyncpg is used at runtime — libpq stays optional but cheap).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual environment to avoid the "root user" warning and isolate deps
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python deps first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY . .

# Multi-tenant runtime: graphs are built per-workspace via POST /api/v1/build.
# All per-workspace artifacts live in cloud stores (Memgraph, Qdrant, Vercel
# Blob, Neon, Upstash) — the container filesystem is stateless.

# Render assigns PORT; fall back to 8000 for local docker run.
ENV PORT=8000
EXPOSE ${PORT}

# Lightweight inline healthcheck (Render also polls healthCheckPath).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# Single image, two run modes. The entrypoint inspects APP_MODE:
#   APP_MODE=api     → uvicorn (WEB_CONCURRENCY workers; default 2)
#   APP_MODE=worker  → src.worker.build_worker (one build per process)
# Build state lives in Neon (see src/api/build_queue.py); there is no
# longer any in-memory job registry, so multiple API workers/instances
# and a pool of build workers all coexist safely.
CMD ["python", "-m", "src.entrypoint"]
