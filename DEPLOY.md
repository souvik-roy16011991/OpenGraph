# Deploying to Render

The repo ships with a [`render.yaml`](./render.yaml) Blueprint that provisions
**two stateless services**:

| Service        | Purpose                                    | Port  |
| -------------- | ------------------------------------------ | ----- |
| `kb-backend`   | FastAPI — all API routes + build workers   | auto  |
| `kb-frontend`  | Next.js 15 production server               | auto  |

All runtime state lives in the managed stores below — neither container
touches a persistent disk.

The frontend builds with `NEXT_PUBLIC_API_BASE` already set to the backend's
Render host, so the browser calls the backend directly (no proxy hop).

---

## 1. Before deploying — provision external stores

Render only runs your code. You still need the managed data stores:

| Store                    | Used for                                         | Where to create |
| ------------------------ | ------------------------------------------------ | --------------- |
| **Neon** (Postgres)      | users, workspaces, build jobs, chat history, configs | neon.tech |
| **Memgraph Cloud**       | knowledge graph nodes + edges                    | memgraph.com/cloud |
| **Qdrant Cloud**         | embedding vectors (one collection per workspace) | qdrant.tech |
| **Vercel Blob**          | uploaded KB JSONs (authoritative file store)     | vercel.com/dashboard/stores |
| **OpenRouter**           | LLM + embedding API                              | openrouter.ai |
| **Upstash Redis**        | cross-link + session cache                       | upstash.com |

Grab the connection string / API key from each one — you'll paste them into
Render's secret fields in step 3.

---

## 2. Push this repo to GitHub

Render watches a Git branch. Push to any branch — the Blueprint picks it up.

```bash
git add .
git commit -m "Prepare for Render"
git push origin main
```

Your `.env` and `.env.local` are already in `.gitignore`, so no secrets leak.

---

## 3. Create the Blueprint on Render

1. Render dashboard → **New** → **Blueprint**.
2. Connect the GitHub repo.
3. Render parses `render.yaml` and shows two planned services + one disk.
4. Click **Apply**.

First deploy will **fail on `kb-backend`** because the secret env vars
(marked `sync: false` in `render.yaml`) haven't been set yet. Fill them in:

### Secrets to set on `kb-backend`

Open `kb-backend` → **Environment** tab → add each:

```
OPENROUTER_API_KEY        = sk-or-v1-...
MEMGRAPH_URI              = bolt+ssc://<host>:7687
MEMGRAPH_USERNAME         = <user>
MEMGRAPH_PASSWORD         = <pw>
QDRANT_URL                = https://<cluster>.qdrant.io:6333
QDRANT_API_KEY            = eyJhbGci...
DATABASE_URL              = postgresql://<user>:<pw>@<host>.neon.tech/<db>?sslmode=require
BLOB_READ_WRITE_TOKEN     = vercel_blob_rw_...
UPSTASH_REDIS_REST_URL    = https://<name>.upstash.io    # optional, Phase C
UPSTASH_REDIS_REST_TOKEN  = <token>                      # optional, Phase C
```

The non-secret values (model names, URLs, feature flags, CORS origins)
come pre-populated from `render.yaml` and don't need editing for a default
deploy.

### Secret **not** needed on `kb-frontend`

The frontend reads its API URL from `NEXT_PUBLIC_API_BASE`, which is
auto-wired via `fromService` in `render.yaml` — no manual action needed.

### Trigger a redeploy

`kb-backend` → **Manual Deploy** → Deploy latest commit.

Render builds the Dockerfile (no graph is baked in — graphs are now built
per-workspace at runtime). Once healthcheck `/health` returns 200, the frontend
build kicks off and wires its `NEXT_PUBLIC_API_BASE` to the backend's public
host.

---

## 4. Post-deploy smoke test

Replace `<fe>` and `<be>` with your actual Render hosts.

```bash
# 1. Backend health
curl https://<be>.onrender.com/health
# expects {"status":"ok","service":"kb-knowledge-graph"}

# 2. Create a workspace
curl -X POST -H 'Content-Type: application/json' \
  -d '{"name":"production-test"}' \
  https://<be>.onrender.com/api/v1/workspaces

# 3. Copy the returned id, export it, upload KB JSON(s)
WID=<uuid-from-above>
curl -H "X-Workspace-Id: $WID" \
  -F "knowledge_files=@my-knowledge.json" \
  -F "tool_files=@my-tools.json" \
  https://<be>.onrender.com/api/v1/kb/upload

# 4. Build
curl -X POST -H "X-Workspace-Id: $WID" -H 'Content-Type: application/json' \
  -d '{"skip_embeddings":true}' \
  https://<be>.onrender.com/api/v1/build

# 5. Open the frontend — the workspace will be listed.
open https://<fe>.onrender.com/workspaces
```

---

## 5. Tighten CORS for production

Once you know the frontend origin, update the backend env var:

```
CORS_ALLOW_ORIGINS = https://<fe>.onrender.com
```

(Multiple origins: comma-separate. A single `"*"` keeps everything permissive
but forbids credentials, which is fine for header-based X-Workspace-Id auth.)

---

## 6. Local Docker parity check

Before pushing, reproduce the prod setup locally:

```bash
# From the repo root
docker build -t kb-backend .
docker run --rm -p 8000:8000 \
  -e OPENROUTER_API_KEY=... \
  -e MEMGRAPH_URI=bolt+ssc://... \
  -e MEMGRAPH_USERNAME=... \
  -e MEMGRAPH_PASSWORD=... \
  -e QDRANT_URL=https://... \
  -e QDRANT_API_KEY=... \
  -e DATABASE_URL=postgresql://... \
  -e BLOB_READ_WRITE_TOKEN=... \
  -e UPSTASH_REDIS_REST_URL=https://... \
  -e UPSTASH_REDIS_REST_TOKEN=... \
  kb-backend

# Frontend in another terminal
cd frontend
docker build -t kb-frontend \
  --build-arg NEXT_PUBLIC_API_BASE=http://host.docker.internal:8000 .
docker run --rm -p 3000:3000 kb-frontend
```

On macOS `host.docker.internal` lets the frontend container reach the
backend running on the host network.

---

## Common pitfalls

- **`Missing X-Workspace-Id header`** on every request: the frontend hasn't
  picked a workspace yet. Visit `/workspaces` and select or create one.
- **Backend starts but `/graph/stats` returns 503**: expected until the first
  workspace is built. Hit `POST /api/v1/build` with a workspace id.
- **`orphaned — process restarted`** on old build jobs: startup-time scan
  flips any `running` row to `error`. This is cosmetic; new builds work fine.
- **Qdrant collection limit**: each workspace gets its own collection
  (`kb-{short_wid}`). Qdrant Cloud free is ~100 collections — if you expect
  more workspaces, delete unused ones via `DELETE /api/v1/workspaces/{id}`
  (it purges Memgraph + Qdrant + Blob + local disk + Neon in one call).
