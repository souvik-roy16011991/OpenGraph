# Deploying to Render

`render.yaml` provisions **three Docker services** off a single repo
and a single backend image. SDK packages are published separately —
they aren't Render services.

| Service | Purpose | Port |
|---|---|---|
| `opengraph-backend` | FastAPI — all REST routes, including `/api/v1/ext/*` public API | auto |
| `opengraph-build-worker` | Durable build worker — polls Neon's `build_jobs` queue | — |
| `opengraph-frontend` | Next.js 15 production server | auto |

| Package | Surface | Deploy path |
|---|---|---|
| `opengraph-sdk` (PyPI) | [sdks/python/](./sdks/python/) — Python SDK for `/ext/*` | GitHub Actions on tag `sdk-python-v*` |
| `@opengraph/sdk` (npm) | [sdks/typescript/](./sdks/typescript/) — TypeScript/JS SDK | GitHub Actions on tag `sdk-typescript-v*` |

The frontend bakes `NEXT_PUBLIC_API_BASE` with the backend's Render host
at build time. The browser and third-party SDK callers both target the
backend directly — no proxy hop.

---

## 1. Provision managed stores

Render runs your code; everything else comes from these SaaS vendors.

| Store | Used for | Where to create | Secrets you'll need |
|---|---|---|---|
| **Neon** (Postgres) | users, workspaces, builds, chats, configs, audit, build queue, API keys, billing | neon.tech | `DATABASE_URL` |
| **Memgraph Cloud** | knowledge graph nodes + edges | memgraph.com/cloud | `MEMGRAPH_URI`, `_USERNAME`, `_PASSWORD` |
| **Qdrant Cloud** | embedding vectors | qdrant.tech | `QDRANT_URL`, `QDRANT_API_KEY` |
| **Vercel Blob** | uploaded KB JSONs (authoritative file store) | vercel.com/dashboard/stores | `BLOB_READ_WRITE_TOKEN` |
| **OpenRouter** | LLM + embedding API (342+ models) | openrouter.ai | `OPENROUTER_API_KEY` |
| **Upstash Redis** | JWKS cache, cross-link cache, model-catalog cache, rate-limit counters | upstash.com | `UPSTASH_REDIS_REST_URL`, `_TOKEN` |

**Auth is first-party JWT (HS256).** Generate a 48-byte secret and paste
it into `JWT_SECRET` on both backend and worker services.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Qdrant mode decision

- **Per-workspace collection** (default; `QDRANT_SHARED_COLLECTION=""`):
  one Qdrant collection per workspace. Simple mental model, but hits
  Qdrant Cloud's per-account collection cap around 1,000 workspaces.
- **Shared collection** (`QDRANT_SHARED_COLLECTION=kb-shared-v1`): every
  tenant shares one collection, filtered by a `workspace_id` payload.
  Required for scale past ~1,000 tenants.

**If you flip to shared mode, set the same value on backend AND
worker** — `render.yaml` has a placeholder on both services. A mismatch
means writes land in one collection and queries read from another.

---

## 2. Push this repo to GitHub

Render watches a Git branch. `.env*` files are already gitignored.

```bash
git add .
git commit -m "Prepare for Render"
git push origin main
```

---

## 3. Create the Blueprint on Render

1. Render dashboard → **New** → **Blueprint**.
2. Connect the GitHub repo.
3. Render parses `render.yaml` and shows **three** planned services.
4. Click **Apply**.

The first deploy will **fail** on the backend + worker because the
`sync: false` secrets aren't set yet. Fill them in below.

### Required secrets — `opengraph-backend`

```ini
# Core app
OPENROUTER_API_KEY        = sk-or-v1-...
DATABASE_URL              = postgresql://<user>:<pw>@<host>.neon.tech/<db>?sslmode=require
MEMGRAPH_URI              = bolt+ssc://<host>:7687
MEMGRAPH_USERNAME         = <user>
MEMGRAPH_PASSWORD         = <pw>
QDRANT_URL                = https://<cluster>.qdrant.io:6333
QDRANT_API_KEY            = eyJhbGci...
BLOB_READ_WRITE_TOKEN     = vercel_blob_rw_...
UPSTASH_REDIS_REST_URL    = https://<name>.upstash.io
UPSTASH_REDIS_REST_TOKEN  = <token>

# Auth (REQUIRED)
JWT_SECRET                = <48-byte urlsafe string>

# Optional — GitHub OAuth sign-in button
GITHUB_CLIENT_ID          = Iv1...
GITHUB_CLIENT_SECRET      = ...
```

### Required secrets — `opengraph-build-worker`

The worker needs **every** secret the backend has — same Neon, same
Memgraph, same Qdrant, same Blob, same Upstash, same `JWT_SECRET`
(the worker imports auth helpers that reference it, even though it
never mints tokens). Use Render's **Link Environment Groups** feature
or just paste them again.

### Required secrets — `opengraph-frontend`

None. No third-party auth env vars. `NEXT_PUBLIC_API_BASE` and
`BACKEND_URL` are hard-coded in `render.yaml` to the backend's
`.onrender.com` host; change them there if you rename the backend
service or move to a custom domain.

### Trigger redeploy

Backend → **Manual Deploy** → **Deploy latest commit**. Once
`/health` returns 200, the worker and frontend deploys follow.

---

## 4. Post-deploy smoke test

Replace `<fe>` and `<be>` with your Render hosts.

```bash
# 1. Backend health — every managed store shows true
curl https://<be>.onrender.com/health

# 2. Template catalog (public; no auth)
curl https://<be>.onrender.com/api/v1/templates | jq '.templates | length'

# 3. JWT auth — signup + workspace create
TOKEN=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"email":"smoke@example.com","password":"correct-horse"}' \
  https://<be>.onrender.com/api/v1/auth/signup | jq -r .token)

WID=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"production-test"}' \
  https://<be>.onrender.com/api/v1/workspaces | jq -r .id)

# 4. Upload → build → query (assumes you have two KB JSONs handy)
curl -H "Authorization: Bearer $TOKEN" -H "X-Workspace-Id: $WID" \
  -F "knowledge_files=@knowledge.json" -F "tool_files=@tools.json" \
  https://<be>.onrender.com/api/v1/kb/upload

curl -X POST -H "Authorization: Bearer $TOKEN" -H "X-Workspace-Id: $WID" \
  -H 'Content-Type: application/json' -d '{}' \
  https://<be>.onrender.com/api/v1/build

# 5. Deploy + query via the public API
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{}' \
  https://<be>.onrender.com/api/v1/workspaces/$WID/deploy
#   (no secret in the response — mint a key at /api-keys in the UI,
#    or via curl:)
KEY=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"name":"smoke"}' \
  https://<be>.onrender.com/api/v1/keys | jq -r .plaintext)

curl -X POST -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d "{\"workspace_id\":\"$WID\",\"query\":\"what's in my graph?\"}" \
  https://<be>.onrender.com/api/v1/ext/query

# 6. Frontend landing
open https://<fe>.onrender.com/
```

---

## 5. Publishing the SDKs

SDKs don't live on Render — they go to PyPI + npm via tagged releases.

```bash
# Python — bump version in sdks/python/pyproject.toml, then:
git tag sdk-python-v0.2.0
git push origin sdk-python-v0.2.0

# TypeScript — bump version in sdks/typescript/package.json, then:
git tag sdk-typescript-v0.2.0
git push origin sdk-typescript-v0.2.0
```

GitHub Actions workflows at
[.github/workflows/sdk-python.yml](./.github/workflows/sdk-python.yml) and
[.github/workflows/sdk-typescript.yml](./.github/workflows/sdk-typescript.yml)
run the matrix tests, verify the tag version matches the package
manifest, and publish. PyPI uses trusted publishing (OIDC — no long-
lived token); npm uses provenance via the `NPM_TOKEN` repo secret.

First-time operator setup:

- **PyPI trusted publisher.** pypi.org → *Your projects* → *Publishing*
  → add `souvikroy/OpenGraph`, workflow `sdk-python.yml`, environment
  `pypi-release`. Matching name is load-bearing.
- **`NPM_TOKEN` secret.** Create a classic automation token on npmjs.com
  with publish permission on `@opengraph/sdk`. Paste into the repo's
  **Settings → Secrets → Actions → `NPM_TOKEN`**.

---

## 6. Auth housekeeping

### Rotate `JWT_SECRET`

Set a fresh 48-byte secret on **both** backend and worker, then redeploy
both. All existing JWTs become invalid; every browser tab bounces to
`/sign-in` on its next request. API keys are unaffected — they're
hashed with bcrypt, not signed with the JWT secret.

### Rotate an API key

Users do this from `/api-keys` in the UI: Revoke the old one, then
click "New key". The revoke takes effect within ~5 minutes across all
workers (in-proc cache TTL).

### Un-deploy a workspace

There's no dedicated "un-deploy" endpoint in v1. To kill API access to
a graph without deleting it, either revoke the key(s) scoped to it, or
manually `UPDATE workspaces SET deployed_at = NULL WHERE id = $1`
against Neon.

---

## 7. Tightening CORS

`render.yaml` wires `CORS_ALLOW_ORIGINS` to `https://opengraph.tech`
plus the frontend's `.onrender.com` host. If you deploy to a different
domain, update the value to your exact origin (scheme + host) — the
backend compares the `Origin:` header string-for-string.

Browser SDK callers on a domain other than the frontend's origin will
be blocked by CORS. For third-party browser users of your API,
either (a) widen `CORS_ALLOW_ORIGINS` to `*` (drops credential
support) or (b) keep browser usage to the same-origin frontend and
recommend the Python/TS SDKs for everyone else.

---

## 8. Local Docker parity check

Reproduce the prod setup before pushing:

```bash
# Backend (same image as worker — APP_MODE selects which loop runs)
docker build -t opengraph-backend .
docker run --rm -p 8000:8000 \
  -e OPENROUTER_API_KEY=... \
  -e DATABASE_URL=postgresql://... \
  -e MEMGRAPH_URI=bolt+ssc://... \
  -e MEMGRAPH_USERNAME=... -e MEMGRAPH_PASSWORD=... \
  -e QDRANT_URL=https://... -e QDRANT_API_KEY=... \
  -e BLOB_READ_WRITE_TOKEN=... \
  -e UPSTASH_REDIS_REST_URL=... -e UPSTASH_REDIS_REST_TOKEN=... \
  -e JWT_SECRET=... \
  opengraph-backend

# Same image, worker mode
docker run --rm -e APP_MODE=worker \
  -e OPENROUTER_API_KEY=... \
  # ... all the other secrets — same set as backend
  -e JWT_SECRET=... \
  opengraph-backend

# Frontend — NEXT_PUBLIC_API_BASE is frozen at build time
cd frontend
docker build -t opengraph-frontend \
  --build-arg NEXT_PUBLIC_API_BASE=http://host.docker.internal:8000 .
docker run --rm -p 3000:3000 opengraph-frontend
```

On macOS `host.docker.internal` reaches the backend on the host.

---

## 9. Observability & admin

- **Render logs.** Every uvicorn + worker line streams in the Render
  dashboard. Noisy 3rd-party INFO from `httpx`/`httpcore`/`urllib3` is
  suppressed at entrypoint time — errors still come through.
- **Audit trail.** `/profile` lists every user action. Raw:
  `GET /api/v1/me/audit` (paginated, filterable).
- **Orphan reconciler.** `python -m scripts.reconcile_orphans` finds
  Blob files / Qdrant collections / Memgraph workspaces that don't
  match a live Neon row. Dry-run by default; `--delete` purges with a
  100-item safety cap.
- **Wipe everything.** `python scripts/wipe_all.py` clears Memgraph +
  Qdrant + Blob + Neon in sequence. Idempotent.
- **Worker concurrency.** One build per worker process (module-level
  cache invariant). Scale by adding instances, not raising concurrency.

---

## 10. Common pitfalls

- **`401 Unauthorized` right after rotating `JWT_SECRET`** — expected.
  Every open tab loses its session; refresh bounces to `/sign-in`.
- **`503 Authentication is not configured`** — `JWT_SECRET` is empty on
  the backend. Set it and redeploy.
- **Backend starts but `/graph/*` returns 503** — no workspace has been
  built yet. Hit `POST /api/v1/build`.
- **`409 Workspace is not deployed to the API`** from `/ext/*` — the
  user forgot to click Deploy. Open `/workspaces` and deploy the graph.
- **Qdrant collection limit** — each workspace = one collection in
  per-workspace mode. Cloud free tier caps around ~100. Flip
  `QDRANT_SHARED_COLLECTION` (**on both services**) once you approach
  the cap; existing graphs need a rebuild to migrate into the shared
  collection.
- **`kb-config path is not a directory`** — set `KB_CONFIG_PATH=/app/kb-config`
  on the worker (already wired in `render.yaml`). The loader also
  searches ancestor directories of `__file__` and `cwd`; if it still
  can't find one it synthesises a minimal DomainProfile and continues.
- **Builds fail with vendor-named log lines (`OpenRouter` / `Qdrant` /
  `Memgraph`)** — those were renamed to `model` / `vector store` /
  `graph storage` in v1. If you see the old wording, something is
  running an old build of the worker image; trigger a redeploy.
- **Zombie workers.** A worker process whose source was deleted or
  moved holds stale `__file__` paths and keeps failing builds. Check
  `ps -axeo pid,etime,command | grep src.entrypoint`. Kill with
  `kill <pid>` — Render auto-respawns from the current image.
- **CORS errors after a custom-domain switch** — update
  `CORS_ALLOW_ORIGINS` on the backend. The value is a literal origin
  list, not a wildcard pattern.
