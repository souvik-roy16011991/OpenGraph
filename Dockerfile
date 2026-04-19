FROM python:3.11-slim

WORKDIR /app

# System deps: gcc for compiling C extensions (faiss-cpu, numpy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies before copying source so this layer is cached
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source and KB data
COPY . .

# Build the knowledge graph at image build time.
# Uses fast TF-IDF hash embeddings (no network / no API key required).
# Override at build time with --build-arg TFIDF=0 to use remote embeddings,
# but OPENROUTER_API_KEY must be available as a build secret in that case.
ARG TFIDF=1
RUN mkdir -p data && KB_FORCE_TFIDF=${TFIDF} python scripts/build_graph.py --no-llm

# Render injects $PORT at runtime; default to 10000 to match Render's default
ENV PORT=10000

# Expose for local docker run
EXPOSE ${PORT}

CMD uvicorn src.api.server:app --host 0.0.0.0 --port ${PORT} --workers 1
