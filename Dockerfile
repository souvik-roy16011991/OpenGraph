FROM python:3.11-slim AS base

WORKDIR /app

# System deps: gcc/g++ for faiss-cpu + numpy wheels, libpq for psycopg fallback
# (asyncpg is used at runtime — libpq stays optional but cheap).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps first so the layer caches across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY . .

# Multi-tenant runtime: graphs are built per-workspace via POST /api/v1/build.
# Per-workspace artifacts (FAISS + NetworkX pickle + node_registry) land
# under $DATA_DIR, which we default to a path intended to be backed by a
# Render Persistent Disk mounted at /var/data. Subfolders are auto-created
# by workspace_data_dir().
ENV DATA_DIR=/var/data
RUN mkdir -p /var/data

# Render assigns PORT; fall back to 8000 for local docker run.
ENV PORT=8000
EXPOSE ${PORT}

# Lightweight inline healthcheck (Render also polls healthCheckPath).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# Single uvicorn worker — the build-job runner keeps per-workspace state in
# memory (build_jobs._jobs). Multiple workers would each have their own
# dict and the /build/{job_id} route could land on the wrong worker.
# Horizontal scaling requires moving _jobs into Neon (future work).
CMD uvicorn src.api.server:app --host 0.0.0.0 --port ${PORT} --workers 1
