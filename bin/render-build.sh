#!/usr/bin/env bash
# render-build.sh — Build script for Render's native Python runtime.
#
# Used when deploying WITHOUT Docker (runtime: python in render.yaml).
# Render runs this script during the Build Phase. The data/ artifacts
# produced here are preserved and available when the Start Command runs.
#
# Usage (set as Build Command in Render dashboard or render.yaml):
#   ./bin/render-build.sh
#
# Environment variables honoured at build time:
#   OPENROUTER_API_KEY  — if set, uses real Qwen embeddings; otherwise TF-IDF
#   KB_FORCE_TFIDF      — set to "1" to force TF-IDF regardless of API key

set -euo pipefail

echo "==> Installing Python dependencies"
pip install --no-cache-dir -r requirements.txt

echo "==> Creating data/ directory"
mkdir -p data

echo "==> Building knowledge graph"
if [ -z "${OPENROUTER_API_KEY:-}" ]; then
    echo "    OPENROUTER_API_KEY not set — using fast TF-IDF embeddings"
    KB_FORCE_TFIDF=1 python scripts/build_graph.py --no-llm
else
    echo "    OPENROUTER_API_KEY found — using remote Qwen embeddings"
    KMP_DUPLICATE_LIB_OK=TRUE python scripts/build_graph.py --no-llm
fi

echo "==> Build complete. Artifacts in data/:"
ls -lh data/
