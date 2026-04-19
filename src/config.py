"""
Central configuration for the KB Knowledge Graph Engine.

Secrets and environment-specific values are loaded from a .env file at the
project root. Copy .env.example to .env and fill in your values.
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Load .env from the project root (the directory that contains src/)
load_dotenv(Path(__file__).parent.parent / ".env")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Domain name, KB file paths, and column-keyword sets now live in the active
# KBConfig. Import src.kb_config.get_active_kb_config() to access them.
ROOT_DIR = Path(__file__).parent.parent

DATA_DIR = Path(os.environ.get("DATA_DIR") or (ROOT_DIR / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
GRAPH_PICKLE_PATH = DATA_DIR / "knowledge_graph.pkl"
FAISS_INDEX_PATH = DATA_DIR / "faiss_index.bin"
NODE_REGISTRY_PATH = DATA_DIR / "node_registry.json"
CROSS_LINKS_PATH = DATA_DIR / "cross_links.json"

# ---------------------------------------------------------------------------
# LLM – Qwen via OpenRouter
# Values sourced from .env; fall back to empty string so callers get a clear
# error rather than a silent wrong-credential failure.
# ---------------------------------------------------------------------------
OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL: str = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL: str = os.environ.get("LLM_MODEL", "qwen/qwen3-235b-a22b")
LLM_TEMPERATURE: float = float(os.environ.get("LLM_TEMPERATURE", "0.1"))
LLM_MAX_TOKENS: int = int(os.environ.get("LLM_MAX_TOKENS", "4096"))

if not OPENROUTER_API_KEY:
    import warnings
    warnings.warn(
        "OPENROUTER_API_KEY is not set. "
        "Copy .env.example to .env and add your key before running LLM-dependent steps.",
        stacklevel=1,
    )

# ---------------------------------------------------------------------------
# Embedding model — Qwen3-Embedding-8B via OpenRouter
#
# EMBEDDING_MODEL    Remote model ID (contains "/") → routes through OpenRouter
#                    embeddings API (POST /api/v1/embeddings). Uses the same
#                    OPENROUTER_API_KEY / OPENROUTER_BASE_URL credentials above.
#                    Set to a local name (e.g. "all-MiniLM-L6-v2") to fall back
#                    to sentence-transformers.
# EMBEDDING_DIM      Dimension used by the TF-IDF offline fallback (KB_FORCE_TFIDF=1).
#                    Also used as the zero-matrix shape in cross_kb_mapper on failure.
#                    For the OpenRouter path the real FAISS dim comes from the model's
#                    actual output (vecs.shape[1]).
# EMBEDDING_DIMENSIONS  Matryoshka override: pass as `dimensions=` to the API.
#                    None = use model-native 4096. Set e.g. 1024 for lower cost.
# ---------------------------------------------------------------------------
# All embedding / similarity / traversal / cross-KB knobs below are now sourced
# from kb-config/graph.yaml via src.graph_config.get_graph_config(). The
# constants re-exported here stay as the canonical names for backwards
# compatibility; env-var overrides (e.g. SIMILARITY_THRESHOLD=0.9) still apply
# because graph_config respects them internally.
from src.graph_config import get_graph_config as _get_graph_config
_gc = _get_graph_config()

EMBEDDING_MODEL: str = _gc.embeddings.model
EMBEDDING_DIM: int = _gc.embeddings.tfidf_fallback_dim
EMBEDDING_DIMENSIONS: Optional[int] = _gc.embeddings.dimensions
SIMILARITY_THRESHOLD: float = _gc.embeddings.similarity_threshold
MAX_RELATED_EDGES_PER_NODE: int = _gc.embeddings.max_related_edges_per_node

# ---------------------------------------------------------------------------
# Graph traversal defaults (see kb-config/graph.yaml → traversal:)
# ---------------------------------------------------------------------------
MAX_TRAVERSAL_DEPTH: int = _gc.traversal.max_depth
TOP_K_ENTRY_NODES: int = _gc.traversal.top_k_entry_nodes

# ---------------------------------------------------------------------------
# Cross-KB auto-mapping thresholds (see kb-config/graph.yaml → cross_kb:)
# Controls how generate_cross_kb_mappings() scores and filters Tool KB →
# Knowledge KB chapter relationships.
# ---------------------------------------------------------------------------
CROSS_KB_AUTO_THRESHOLD: float = _gc.cross_kb.auto_threshold
CROSS_KB_EMBED_WEIGHT: float = _gc.cross_kb.embed_weight
CROSS_KB_COOCCUR_WEIGHT: float = _gc.cross_kb.cooccur_weight
CROSS_KB_MAX_LINKS_PER_CHAPTER: int = _gc.cross_kb.max_links_per_chapter

# ---------------------------------------------------------------------------
# Cloud infrastructure — Pinecone, Neo4j, Vercel Blob
# Each block auto-detects whether the cloud backend is configured.
# Leave any credential blank to fall back to the local alternative.
# ---------------------------------------------------------------------------

# Pinecone vector database
PINECONE_API_KEY: str = os.environ.get("PINECONE_API_KEY", "")
PINECONE_INDEX_NAME: str = os.environ.get("PINECONE_INDEX_NAME", "kb-index")
PINECONE_NAMESPACE: str = os.environ.get("PINECONE_NAMESPACE", "kb-knowledge-graph")

# Qdrant Cloud vector database (hosted — Neo4j-wire-compatible via REST + gRPC)
QDRANT_URL: str = os.environ.get("QDRANT_URL", "")
QDRANT_API_KEY: str = os.environ.get("QDRANT_API_KEY", "")
QDRANT_COLLECTION_NAME: str = os.environ.get("QDRANT_COLLECTION_NAME", "kb-knowledge-graph")

# Neo4j Aura graph database
NEO4J_URI: str = os.environ.get("NEO4J_URI", "")
NEO4J_USERNAME: str = os.environ.get("NEO4J_USERNAME", "")
NEO4J_PASSWORD: str = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_DATABASE: str = os.environ.get("NEO4J_DATABASE", "neo4j")

# Memgraph graph database (Neo4j-wire-compatible; runs locally via Docker by default)
MEMGRAPH_URI: str = os.environ.get("MEMGRAPH_URI", "")
MEMGRAPH_USERNAME: str = os.environ.get("MEMGRAPH_USERNAME", "")
MEMGRAPH_PASSWORD: str = os.environ.get("MEMGRAPH_PASSWORD", "")
MEMGRAPH_DATABASE: str = os.environ.get("MEMGRAPH_DATABASE", "memgraph")

# Vercel Blob storage
BLOB_READ_WRITE_TOKEN: str = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
BLOB_STORE_PATH: str = os.environ.get("BLOB_STORE_PATH", "v0-it-support-automation-blob/kb-config")

# Feature flags — auto-detected from credential presence
USE_PINECONE: bool = bool(PINECONE_API_KEY)
USE_QDRANT: bool = bool(QDRANT_URL and QDRANT_API_KEY)
USE_NEO4J: bool = bool(NEO4J_URI)
USE_MEMGRAPH: bool = bool(MEMGRAPH_URI)
USE_BLOB_STORAGE: bool = bool(BLOB_READ_WRITE_TOKEN)
