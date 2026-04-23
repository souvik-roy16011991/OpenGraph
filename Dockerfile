FROM python:3.11-slim AS base

WORKDIR /app

# System deps:
#   gcc/g++/libgomp1 — faiss-cpu + numpy wheels.
#   curl — /health smoke.
#   libreoffice-core/impress/writer — headless PPTX/DOCX/XLSX/ODP → PDF
#     conversion for the vision-OCR ingestion path. Imports src.kb.doc_parser
#     shell out to `soffice --headless --convert-to pdf`. Adds ~600 MB to
#     the image; the backend and worker share one image so both have it.
#   fonts-liberation — sans/serif fallback fonts so LibreOffice renders
#     text even when a source doc's embedded font is missing.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    libgomp1 \
    libreoffice-core \
    libreoffice-impress \
    libreoffice-writer \
    libreoffice-calc \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Warmup: prime LibreOffice's per-user profile so the first real convert
# doesn't pay a ~5s cold-start penalty. --terminate_after_init exits
# immediately after the profile is created.
RUN soffice --headless --terminate_after_init || true

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
