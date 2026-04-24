# OpenGraph

<p align="left">
  <picture>
    <img src="./frontend/public/opengraph-mark.svg" alt="OpenGraph" width="56" />
  </picture>
</p>

**OpenGraph** is a multi-tenant enterprise platform that turns pairs of
structured JSON knowledge bases (domain **knowledge** + **tool** catalog)
into a typed, workspace-scoped graph, then exposes a LangGraph agent that
queries that graph in natural language. Every runtime artifact — uploads,
vectors, graph, chat history, config — lives in managed cloud stores. No
local disk. No per-tenant state in the container.

## What it does 

1. **Pick a template** (or start blank) from a 29-template catalog
   spanning healthcare, finance, legal, engineering, people, ops,
   sales, marketing, customer success.
2. **Upload** your knowledge + tool JSONs.
3. **Tune** the domain profile and graph-construction knobs.
4. **Build** — the pipeline parses, extracts nodes, computes embeddings
   (OpenRouter), writes the graph to Memgraph, writes vectors to
   Qdrant, caches cross-KB links in Upstash.
5. **Explore** the graph visually (Cytoscape), or **chat** with it
   from the standalone Playground using any OpenRouter model.

---

## Architecture

```
          ┌──────────────── Users ────────────────┐
          │                                       │
          │  Next.js 15 / Tailwind / Radix UI     │
          │  ┌─────────────────────────────────┐  │
          │  │ /templates  (OpenRouter-style)  │  │
          │  │ /upload → /domain → /graph-     │  │
          │  │   config → /build → /explore    │  │
          │  │ /chat    (standalone playground)│  │
          │  │ /history (audit)                │  │
          │  └─────────────────────────────────┘  │
          │   first-party JWT (HS256, bcrypt pw)  │
          └────────────────┬──────────────────────┘
                           │ Authorization: Bearer <jwt>
                           │ X-Workspace-Id: <uuid>
                           ▼
          ┌─────────── FastAPI  :8000 ────────────┐
          │                                       │
          │   src/api/auth.py      HS256 verify   │
          │   src/api/deps.py      ownership      │
          │   src/api/routes.py    + sub-routers  │
          │       workspace │ kb │ config │ build │
          │       history   │ llm│ graph  │ tmpl  │
          │                                       │
          │   src/graph_builder/   pipeline       │
          │   src/agent/           LangGraph      │
          └───┬─────┬──────┬──────┬──────┬────────┘
              │     │      │      │      │
              ▼     ▼      ▼      ▼      ▼
          ┌──────┐┌─────┐┌──────┐┌─────┐┌────────┐
          │ Neon ││Mem- ││Qdrant││Blob ││Upstash │
          │ PG   ││graph││      ││     ││ Redis  │
          └──────┘└─────┘└──────┘└─────┘└────────┘
           users   nodes  vectors files  JWKS,
           ws      edges         (JSON)  cross-
           builds                        links,
           chat                          LLM
           config                        catalog
```

### Cloud backends (single source of truth for each concern)

| Store | Purpose | Per-workspace isolation |
|---|---|---|
| **Neon Postgres** | users, workspaces, build jobs, chat history, config versions, upload metadata | row-level via FK + owner check |
| **Memgraph Cloud** | graph nodes + edges (labels, types, properties) | `workspace_id` property on every node + edge |
| **Qdrant Cloud** | embedding vectors | 1 collection per workspace (`kb-{wid-short}`) |
| **Supabase Storage** (S3-compatible) | uploaded KB JSON files + raw source docs (authoritative) | path prefix `{ws_id}/…` inside a public bucket |
| **Upstash Redis** | JWKS cache, LLM cross-link cache, OpenRouter model catalog cache | keyed by `{wid}` / `{project_id}` |

### Auth & tenancy model

- **Identity**: first-party JWT. `POST /api/v1/auth/signup` and
  `/auth/login` exchange email + password for a 30-day HS256 JWT
  ([PyJWT](https://pyjwt.readthedocs.io/)); passwords are bcrypt-hashed
  at rest. Each email maps to exactly one `users` row — the tenant
  boundary.
- **`Authorization: Bearer <jwt>`** carries the caller's identity; `sub`
  is the user's UUID. A per-process LRU caches `sub → User` for 5 min
  so the steady-state request path skips the SELECT.
- **`X-Workspace-Id: <uuid>`** must match the workspace's `user_id`.
  Ownership is verified in [src/api/deps.py](src/api/deps.py) before any
  work runs; mismatch returns **404** (not 403) to avoid leaking
  workspace existence across tenants.
- **No dev fallback.** Protected routes return 401 without a valid JWT
  and 503 if `JWT_SECRET` is unset. Rotating `JWT_SECRET` invalidates
  every live session at once.

### In-memory caches (per process)

- `src/kb_config._WORKSPACE_CACHE` — per-workspace `KBConfig` built by
  overlaying `Workspace.domain_config` (JSONB) on the disk seed.
  Primed in [src/api/deps.py](src/api/deps.py) so sync consumers
  (build thread, extractor, prompt loader) never deadlock re-fetching
  from an already-running event loop. Invalidated per workspace by
  `PUT /config/domain`.
- `src/agent/nodes._llm_cache` — one `ChatOpenAI` client per resolved
  model id; queries with different `llm_model` overrides don't rebuild
  the client each time.

---

## Quick start (local)

### Prerequisites

- Python 3.12
- Node 20
- Credentials for: Neon (required), OpenRouter (required), Memgraph +
  Qdrant + Supabase Storage + Upstash (required for production behaviour;
  the app degrades to partial functionality if any are missing).
- A 48-byte `JWT_SECRET` (required):
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

### One-liner — `bin/dev`

Once dependencies are installed and `.env` + `frontend/.env.local` are
filled in, just run:

```bash
bin/dev
```

It starts the three processes you need — the FastAPI API, the
parse/build worker (`APP_MODE=worker`), and the Next.js frontend — with
interleaved tagged logs and a single Ctrl-C to stop them all. Skipping
the worker is the #1 source of "my DOCX upload is stuck at queued
forever" locally, and `bin/dev` removes that footgun.

Pass a component name to run just one: `bin/dev backend`, `bin/dev
worker`, or `bin/dev frontend`.

If you prefer separate terminals, the manual instructions below still
work.

### Backend

```bash
# in repo root
pip install -r requirements.txt

# minimum .env
cat > .env <<EOF
DATABASE_URL=postgresql://...@ep-xxx.neon.tech/neondb
OPENROUTER_API_KEY=sk-or-v1-...
MEMGRAPH_URI=bolt+ssc://...
MEMGRAPH_USERNAME=...
MEMGRAPH_PASSWORD=...
QDRANT_URL=https://...qdrant.cloud
QDRANT_API_KEY=...
SUPABASE_S3_ENDPOINT=https://<project>.storage.supabase.co/storage/v1/s3
SUPABASE_S3_REGION=ap-northeast-1
SUPABASE_S3_ACCESS_KEY_ID=...
SUPABASE_S3_SECRET_ACCESS_KEY=...
SUPABASE_BUCKET=opengraph-kb
SUPABASE_PUBLIC_URL_BASE=https://<project>.supabase.co/storage/v1/object/public
UPSTASH_REDIS_REST_URL=https://...upstash.io
UPSTASH_REDIS_REST_TOKEN=...
EOF

python3 -m uvicorn src.api.server:app --port 8000 --reload
# health: http://localhost:8000/health
# Swagger: http://localhost:8000/docs
```

### Frontend

```bash
cd frontend
npm install

# frontend/.env.local — no third-party auth env vars needed
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8000
BACKEND_URL=http://127.0.0.1:8000

npm run dev
# open http://localhost:3000
```

---

## Environment variables

### Required

| Var | Purpose |
|---|---|
| `DATABASE_URL` | Neon Postgres pooled connection string |
| `OPENROUTER_API_KEY` | OpenRouter chat + embedding calls |

### Cloud backends (all required for production)

| Var | Purpose |
|---|---|
| `MEMGRAPH_URI` / `MEMGRAPH_USERNAME` / `MEMGRAPH_PASSWORD` / `MEMGRAPH_DATABASE` | Graph store |
| `QDRANT_URL` / `QDRANT_API_KEY` / `QDRANT_COLLECTION_NAME` | Vector store |
| `SUPABASE_S3_ENDPOINT` / `SUPABASE_S3_REGION` / `SUPABASE_S3_ACCESS_KEY_ID` / `SUPABASE_S3_SECRET_ACCESS_KEY` / `SUPABASE_BUCKET` / `SUPABASE_PUBLIC_URL_BASE` | Supabase Storage (file store; S3-compatible, public bucket) |
| `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` | Redis REST (caches) |

### Auth (first-party JWT)

| Var | Purpose |
|---|---|
| `JWT_SECRET` | HS256 signing secret. Rotate to log everyone out. |
| `JWT_ALGORITHM` | Optional — defaults to `HS256`. |
| `JWT_EXPIRES_MINUTES` | Access-token lifetime in minutes (default 43200 / 30d). |
| `JWT_ISSUER` | Stamped into `iss`; defaults to `kb-graph-engine`. |
| `PASSWORD_MIN_LENGTH` | Server-enforced minimum password length (default 8). |

### LLM tuning

| Var | Default | Purpose |
|---|---|---|
| `LLM_MODEL` | `qwen/qwen3-235b-a22b` | Env fallback model when neither request-override nor workspace-pref is set |
| `LLM_TEMPERATURE` | `0.1` | |
| `LLM_MAX_TOKENS` | `4096` | |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | |
| `OPENROUTER_ALLOWED_MODELS` | (empty) | Comma-separated allowlist; empty = expose full catalog |

### Alternate backends (optional — used as fallbacks if cloud primary is unset)

| Var | Purpose |
|---|---|
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` / `NEO4J_DATABASE` | Neo4j instead of Memgraph |
| `PINECONE_API_KEY` / `PINECONE_INDEX_NAME` / `PINECONE_NAMESPACE` | Pinecone instead of Qdrant |

### Workspace caps

| Var | Default | Purpose |
|---|---|---|
| `MAX_WORKSPACES_PER_USER` | `25` | Guardrail against template-instantiation abuse |

---

## Data model (Neon)

| Table | Key columns |
|---|---|
| `users` | `id`, `stack_user_id` (unique), `email`, `display_name` |
| `workspaces` | `id`, `user_id` (FK), `name`, `description`, `domain_config` (JSONB), `graph_config` (JSONB — also holds `llm_model`) |
| `workspace_files` | `id`, `workspace_id`, `kb_source`, `filename`, `sha256` (unique within ws + source), `blob_url`, `active` |
| `build_jobs` | `job_id`, `workspace_id`, `status`, `stage`, `percent`, `log_tail`, `domain_snapshot`, `graph_snapshot`, `stats`, `backends` |
| `chat_sessions` | `session_id`, `workspace_id`, `title`, timestamps |
| `chat_messages` | `id`, `session_id`, `role`, `query`, `response` (JSONB), intent/focus/topics, `duration_ms` |
| `config_versions` | `id`, `workspace_id`, `kind` (`domain`/`graph`), `yaml_snapshot`, `parsed_snapshot`, `changed_sections`, `requires_rebuild` |
| `kb_uploads` | audit row on every upload event |

All foreign keys cascade on workspace delete; `DELETE /workspaces/{id}`
additionally purges Memgraph, Qdrant and Blob artifacts for that
workspace.

---

## API reference

**Base URL**: `{host}/api/v1`
**Common headers**:
- `Authorization: Bearer <jwt>` — always required in production. Soft-
  fallback in dev (no env) resolves to the shared anonymous user.
- `X-Workspace-Id: <uuid>` — required on every workspace-scoped route.

OpenAPI spec: `GET /openapi.json`; Swagger UI at `GET /docs`.

### Health

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/health` | public | `{status, service, backends: {neon, memgraph, qdrant, file_storage, upstash}}` |

### Templates (public catalog)

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/api/v1/templates` | public | Query params: `q` (substring), `category`. Returns 29 code-shipped templates. |
| `GET` | `/api/v1/templates/{slug}` | public | Template detail incl. `domain` pre-seed dict |
| `POST` | `/api/v1/templates/{slug}/instantiate` | user | Body: `{name?, description?}`. Creates a workspace with `domain_config` = template's domain. Enforces `MAX_WORKSPACES_PER_USER`. 201 on success. |

### Workspaces

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/workspaces` | List workspaces owned by caller |
| `POST` | `/api/v1/workspaces` | Body: `{name, description?}` → 201 |
| `GET` | `/api/v1/workspaces/{id}` | Ownership-checked detail |
| `PATCH` | `/api/v1/workspaces/{id}` | Body: `{name?, description?}` |
| `DELETE` | `/api/v1/workspaces/{id}` | Cascades Memgraph + Qdrant + Blob + Neon |
| `GET` | `/api/v1/workspaces/{id}/files` | Query: `kb_source` filter |
| `DELETE` | `/api/v1/workspaces/{id}/files/{file_id}` | Also deletes the blob |
| `GET` | `/api/v1/workspaces/{id}/llm` | Returns `{llm_model, effective_model}` |
| `PUT` | `/api/v1/workspaces/{id}/llm` | Body: `{model}` (null/empty clears). Validates against the OpenRouter catalog. |

### KB upload

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/kb/upload` | Multipart: any number of `knowledge_files` + `tool_files`. Uploads go to Vercel Blob first; no local disk. Dedup by sha256 within (workspace, kb_source). |

### Config

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/config/domain` | Returns 5 DomainProfile fields for the workspace |
| `PUT` | `/api/v1/config/domain` | Writes `Workspace.domain_config` + audits to `config_versions`. Invalidates **only this workspace's** cache. |
| `GET` | `/api/v1/config/graph` | Full GraphConfig JSON (6 sections, ~25 knobs) |
| `PUT` | `/api/v1/config/graph` | Returns `{status: 'applied'/'requires_rebuild', changed_sections, requires_rebuild}` |

### Build

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/build` | Body: `{skip_embeddings?, skip_llm_cross_links?}`. 409 if a build is already running for this workspace. |
| `GET` | `/api/v1/build` | Current running job id for this workspace |
| `GET` | `/api/v1/build/{job_id}` | Live status + `log_tail` (last 50 lines). Ownership-checked. |

### Agent query

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/query` | Body: `{query, session_id?, llm_model?}`. Returns the full agent state: `response` markdown, `steps`, `tools_referenced`, `knowledge_concepts`, `follow_up_suggestions`, `traversal_path`, plus the resolved `llm_model`. Persists the turn to `chat_messages` when Neon is on. |

### LLM catalog

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/llm/models` | Lists OpenRouter models (342 at last count). Cached 1h in Upstash. `?refresh=true` busts. Filtered by `OPENROUTER_ALLOWED_MODELS` if set. Returns `{default, models[], allowlist_active}`. |

### Graph read routes

All ownership-checked via `X-Workspace-Id`.

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/graph/stats` | Node + edge counts by type, backends in use |
| `GET` | `/api/v1/graph/tree` | `?max_depth=N` — hierarchical tree from roots |
| `GET` | `/api/v1/graph/chapters` | Flat list of chapter nodes per KB |
| `GET` | `/api/v1/graph/tools` | All extracted `ToolNode`s |
| `GET` | `/api/v1/graph/node/{node_id}` | Node + incident edges + parent/children |
| `POST` | `/api/v1/graph/traverse` | BFS from a node with depth + edge-type filters |
| `GET` | `/api/v1/graph/search` | Hybrid keyword + semantic, `?q=…&top_k=&node_type=` |
| `GET` | `/api/v1/graph/visualization` | Flat nodes + edges payload for Cytoscape. Filters: `max_nodes`, `kb_source`, `node_types`, `edge_types` |

### History (Neon-backed audit)

| Method | Path | Notes |
|---|---|---|
| `GET` | `/api/v1/history/builds` | Workspace-scoped |
| `GET` | `/api/v1/history/builds/{job_id}` | Detail (log tail, snapshots, stats). Ownership-checked. |
| `GET` | `/api/v1/history/chats` | Sessions for this workspace |
| `GET` | `/api/v1/history/chats/{session_id}` | Full message thread. Ownership-checked. |
| `GET` | `/api/v1/history/configs` | Config snapshots, `?kind=domain|graph` |
| `GET` | `/api/v1/history/configs/{config_id}` | Full YAML + parsed snapshot. Ownership-checked. |
| `GET` | `/api/v1/history/uploads` | Upload audit trail |

---

## Template catalog (29 entries across 10 categories)

| Category | Slugs |
|---|---|
| **healthcare** (6) | `healthcare-protocols`, `medicine-product-kb`, `quality-control-lab`, `hospital-management`, `clinical-trials`, `medical-device-compliance` |
| **finance** (8) | `finance-compliance`, `procurement-vendor`, `wealth-management`, `tax-planning`, `lending-operations`, `credit-analysis`, `insurance-operations`, `risk-management` |
| **engineering** (4) | `product-docs`, `devops-sre`, `security-infosec`, `data-engineering` |
| **legal** (3) | `legal-contracts`, `ip-patents`, `privacy-gdpr` |
| **operations** (3) | `it-support`, `customer-support`, `manufacturing-sops` |
| customer (1) | `customer-onboarding` |
| people (1) | `hr-policies` |
| sales (1) | `sales-enablement` |
| marketing (1) | `marketing-brand` |
| general (1) | `blank` |

Add a template: drop a new `templates/<slug>.yaml` with the standard
fields (`slug, name, description, category, icon, domain{...}`), then
touch `src/templates.py` to bust the `lru_cache`.

---

## Graph build pipeline

Full flow of `POST /api/v1/build`:

1. **Collect sources** — `src/api/build_jobs._collect_workspace_sources`
   fetches every `WorkspaceFile`'s bytes from Vercel Blob. No local
   disk is touched.
2. **Parse** — `src/graph_builder/parser.parse_kb_files` accepts
   `(filename, bytes)` tuples and produces a `ParsedKB` per kb_source.
3. **Extract nodes** — `src/graph_builder/extractor.extract_all_nodes`
   walks the ParsedKB into typed nodes (Chapter / Section / Concept /
   Tool / Process / Table / Glossary).
4. **Edges** — `src/graph_builder/edges.EdgeBuilder` builds CONTAINS,
   HAS_CONTENT, NEXT_STEP, INTEGRATES_WITH, IMPLEMENTS, USES_TOOL.
   Cross-KB IMPLEMENTS edges are refined by an LLM call that honors
   the workspace's chosen model; the result is cached in Upstash
   keyed by `{wid, content-hash}`.
5. **Embeddings** — `src/graph_builder/embeddings` generates per-node
   vectors via OpenRouter, upserts to Qdrant (one collection per
   workspace). RELATED_TO edges are built from cosine neighbours.
6. **Persist graph** — `src/graph_builder/builder` writes every node +
   edge into Memgraph tagged with `workspace_id`. A NetworkX view is
   kept in memory for fast traversal; on cold start,
   `KnowledgeGraph.load` rehydrates it directly from Memgraph (no
   pickle on disk).

Build progress is persisted to `build_jobs` and streamed via
`log_tail` on `GET /build/{job_id}`.

---

## LLM model selection

The chat pipeline resolves the model per request:

```
request.llm_model  >  workspace.graph_config["llm_model"]  >  env LLM_MODEL
```

- `GET /llm/models` — browse the 342-model OpenRouter catalog (cached
  1h in Upstash, optional allowlist env).
- `PUT /workspaces/{id}/llm` — set the workspace default (validated
  against the catalog before write).
- `POST /query` — pass `llm_model` for a one-shot override.

The assistant reply carries `llm_model` — the truthful resolved model
that answered this turn, so the UI can label past turns correctly even
after the user switches models.

---

## Frontend routes (Next.js 15 app router)

| Route | Purpose |
|---|---|
| `/` | Landing; redirects unauth'd users to `/sign-in` |
| `/sign-in` | Email + password login — calls `POST /api/v1/auth/login` |
| `/sign-up` | Email + password signup — calls `POST /api/v1/auth/signup` |
| `/templates` | KB template catalog (OpenRouter-style search + category filter). Click → instantiate → `/upload` |
| `/workspaces` | Existing workspace picker |
| `/upload` → `/domain` → `/graph-config` → `/build` → `/explore` | Build wizard, step-locked |
| `/chat` | Standalone Playground. Independent workspace selector (`chat-store`). Model dropdown. |
| `/query` | Legacy redirect → `/chat` |
| `/history` | Audit trail (builds, chats, configs, uploads) |

Auth gate: [frontend/middleware.ts](frontend/middleware.ts) bounces every
unauthenticated request to `/sign-in`. The `auth_token` cookie (mirrored
in `localStorage` so `lib/api.ts` can attach `Authorization: Bearer`) is
the single session marker.

---

## Operations

### Render.com deploy

`render.yaml` provisions two stateless Docker services:

- `opengraph-backend` — FastAPI on uvicorn, 1 worker (build-job state is
  in-memory; horizontal scale requires moving `_jobs` to Neon — future
  work).
- `opengraph-frontend` — Next.js 15 prod build with `NEXT_PUBLIC_API_BASE`
  baked in.

See [DEPLOY.md](DEPLOY.md).

### Admin scripts

| Script | Purpose |
|---|---|
| `scripts/upload_kb.py` | CLI upload of KB JSONs to Vercel Blob (outside the HTTP flow) |
| `scripts/wipe_all.py` | Clear every backing store (Memgraph, Qdrant, Blob, Neon). Idempotent. |
| `scripts/migrate_anon_workspaces.py` | `--dry-run` / `--delete-all` / `--assign-to <user_id>`. Historical one-off to clean up rows from the pre-auth dev-mode era. |

### Rotating `JWT_SECRET`

1. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
2. Update `JWT_SECRET` on `kb-backend` and redeploy — every live token
   becomes invalid; browsers bounce to `/sign-in` on next request.

---

## Tech stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 15.2, React 19, Tailwind 3, Radix UI, Zustand, TanStack Query, Cytoscape, react-markdown |
| Backend | FastAPI, LangGraph, langchain-openai (→ OpenRouter), SQLAlchemy 2.0 async + asyncpg, PyJWT (HS256), bcrypt |
| Graph | Memgraph Cloud (Bolt) via `neo4j` driver; NetworkX as in-memory runtime view |
| Vectors | Qdrant Cloud (REST + gRPC) via `qdrant-client` |
| Files | Vercel Blob REST |
| DB | Neon Postgres (pooled async) |
| Cache | Upstash Redis REST |
| LLM | OpenRouter (any of 342 models — user-selectable per workspace or per request) |
| Auth | First-party JWT (HS256 + bcrypt), email as tenant key |

---

## Roadmap

Planned follow-ups (not shipped):

- **Per-workspace API keys** (`api_keys` table, `/api/v1/ext/*` public
  API namespace, Upstash-backed rate limiting, `/api-docs` frontend
  page with pre-filled curl / JS / Python examples).
- **Team sharing / invites** — move beyond the "one user owns a
  workspace" model.
- **Horizontal build-job scaling** — move the in-memory `_jobs` dict to
  Neon so multiple backend workers can serve the same workspace.
- **Per-message LLM tracking** — add `llm_model` column to
  `chat_messages` so the history page can show which model answered
  each historical turn.
