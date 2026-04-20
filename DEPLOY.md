# Deploying to Render

The repo ships with a [`render.yaml`](./render.yaml) Blueprint that
provisions **two stateless Docker services** — no disks, no stateful
pods. All runtime state lives in managed cloud stores.

| Service        | Purpose                                   | Port |
| -------------- | ----------------------------------------- | ---- |
| `kb-backend`   | FastAPI — all API routes + build workers  | auto |
| `kb-frontend`  | Next.js 15 production server              | auto |

The frontend bakes `NEXT_PUBLIC_API_BASE` with the backend's Render host
at build time, so the browser calls the backend directly (no proxy hop).

---

## 1. Provision managed stores

Render runs your code; everything else comes from these SaaS vendors.
Grab the listed secrets from each dashboard — you'll paste them into
Render in step 3.

| Store                   | Used for                                          | Where to create            | Secrets you'll need |
| ----------------------- | ------------------------------------------------- | -------------------------- | ------------------- |
| **Neon** (Postgres)     | users, workspaces, builds, chats, configs, audit  | neon.tech                  | `DATABASE_URL` |
| **Neon Auth**           | user sign-up / sign-in / OAuth                    | neon.tech → project → Auth | `NEON_AUTH_BASE_URL`, `NEON_AUTH_PROJECT_ID`, `NEON_AUTH_SECRET_SERVER_KEY`, `NEXT_PUBLIC_NEON_AUTH_PUBLISHABLE_CLIENT_KEY` |
| **Memgraph Cloud**      | knowledge graph nodes + edges                     | memgraph.com/cloud         | `MEMGRAPH_URI`, `_USERNAME`, `_PASSWORD` |
| **Qdrant Cloud**        | embedding vectors (one collection per workspace)  | qdrant.tech                | `QDRANT_URL`, `QDRANT_API_KEY` |
| **Vercel Blob**         | uploaded KB JSONs (authoritative file store)      | vercel.com/dashboard/stores | `BLOB_READ_WRITE_TOKEN` |
| **OpenRouter**          | LLM + embedding API (any of 342 models)           | openrouter.ai              | `OPENROUTER_API_KEY` |
| **Upstash Redis**       | JWKS cache, cross-link cache, model-catalog cache | upstash.com                | `UPSTASH_REDIS_REST_URL`, `_TOKEN` |

**Neon Auth is required.** Protected routes return 503 until the
`NEON_AUTH_*` vars are set. There is no unauthenticated fallback.

---

## 2. Push this repo to GitHub

Render watches a Git branch. Your `.env*` files are already in
`.gitignore`, so no secrets leak.

```bash
git add .
git commit -m "Prepare for Render"
git push origin main
```

---

## 3. Create the Blueprint on Render

1. Render dashboard → **New** → **Blueprint**.
2. Connect the GitHub repo.
3. Render parses `render.yaml` and shows the two planned services.
4. Click **Apply**.

The first deploy will **fail on `kb-backend`** because the `sync: false`
secrets aren't set yet. Fill them in next.

### Required secrets on `kb-backend`

Open `kb-backend` → **Environment** tab → add:

```ini
# Core app — required
OPENROUTER_API_KEY         = sk-or-v1-...
DATABASE_URL               = postgresql://<user>:<pw>@<host>.neon.tech/<db>?sslmode=require
MEMGRAPH_URI               = bolt+ssc://<host>:7687
MEMGRAPH_USERNAME          = <user>
MEMGRAPH_PASSWORD          = <pw>
QDRANT_URL                 = https://<cluster>.qdrant.io:6333
QDRANT_API_KEY             = eyJhbGci...
BLOB_READ_WRITE_TOKEN      = vercel_blob_rw_...
UPSTASH_REDIS_REST_URL     = https://<name>.upstash.io
UPSTASH_REDIS_REST_TOKEN   = <token>

# Neon Auth — REQUIRED. Copy the four values from your Neon Auth tenant.
NEON_AUTH_BASE_URL              = https://<ep-id>.neonauth.<region>.aws.neon.tech/neondb/auth
NEON_AUTH_PROJECT_ID            =
NEON_AUTH_SECRET_SERVER_KEY     =
# Optional overrides — leave blank to derive from NEON_AUTH_BASE_URL:
# NEON_AUTH_JWKS_URL            = $NEON_AUTH_BASE_URL/.well-known/jwks.json
# NEON_AUTH_ISSUER              = $NEON_AUTH_BASE_URL
# NEON_AUTH_AUDIENCE            = $NEON_AUTH_PROJECT_ID
```

Non-secrets (model names, `MAX_WORKSPACES_PER_USER`, CORS target, etc.)
are pre-populated by `render.yaml` and need no editing.

### Required secrets on `kb-frontend`

Open `kb-frontend` → **Environment** tab → add (required — the app gates
every route on these):

```ini
NEON_AUTH_BASE_URL                             = https://<ep-id>.neonauth.<region>.aws.neon.tech/neondb/auth
NEXT_PUBLIC_NEON_AUTH_BASE_URL                 = same value as NEON_AUTH_BASE_URL
NEXT_PUBLIC_NEON_AUTH_PROJECT_ID               =
NEXT_PUBLIC_NEON_AUTH_PUBLISHABLE_CLIENT_KEY   =
NEON_AUTH_SECRET_SERVER_KEY                    =
```

The other frontend envs (`NEXT_PUBLIC_API_BASE`, `BACKEND_URL`) are
auto-wired via `fromService` and do not need manual entry.

### Trigger redeploy

`kb-backend` → **Manual Deploy** → Deploy latest commit. Once
`/health` returns 200, the frontend build automatically picks up the
backend host and deploys.

---

## 4. Post-deploy smoke test

Replace `<fe>` and `<be>` with your actual Render hosts.

```bash
# 1. Backend health — should list all 5 cloud backends as true
curl https://<be>.onrender.com/health

# 2. Template catalog (public; no auth)
curl https://<be>.onrender.com/api/v1/templates | jq '.templates | length'
# => 29

# 3. Neon Auth is always on — this call returns 401 unauthenticated.
#    Sign in through the frontend to get a session, then use the browser
#    devtools to grab the Authorization: Bearer ... header and replay here.
curl -X POST -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $NEON_AUTH_TOKEN" \
  -d '{"name":"production-test"}' \
  https://<be>.onrender.com/api/v1/workspaces
WID=<uuid-returned>

# 4. Upload, build, query
curl -H "X-Workspace-Id: $WID" \
  -F "knowledge_files=@knowledge.json" -F "tool_files=@tools.json" \
  https://<be>.onrender.com/api/v1/kb/upload
curl -X POST -H "X-Workspace-Id: $WID" -H 'Content-Type: application/json' \
  -d '{"skip_embeddings":false}' \
  https://<be>.onrender.com/api/v1/build

# 5. Frontend landing — will redirect to /templates
open https://<fe>.onrender.com/
```

---

## 5. Enabling Neon Auth

### 5.1 Create the Neon Auth tenant

1. In your Neon project dashboard → **Auth** tab → enable Neon Auth. Neon
   provisions a Stack-Auth-compatible tenant and shows you:
   - `NEON_AUTH_BASE_URL` — the tenant endpoint, e.g.
     `https://ep-<id>.neonauth.<region>.aws.neon.tech/neondb/auth`
   - `NEON_AUTH_PROJECT_ID` — the tenant project id
   - `NEON_AUTH_SECRET_SERVER_KEY` — server-only, keep secret
   - `NEON_AUTH_PUBLISHABLE_CLIENT_KEY` — baked into the client bundle

### 5.2 Pre-flight: sanity-check the `workspaces.domain_config` column

On first Neon snapshot, confirm the column exists (it should, since
`create_all()` ran it at startup):

```bash
psql "$DATABASE_URL" -c "\d workspaces" | grep domain_config
# should show: domain_config | jsonb | nullable
```

### 5.3 Take a Neon snapshot

Neon dashboard → **Branches** → create a point-in-time branch. The
anon-workspace migration below is irreversible.

### 5.4 Wipe anonymous-owned data

Every workspace currently belongs to the synthetic `__anonymous__` user.
Once Stack Auth is enforced, real users can't access that data anyway,
so either delete it or reassign.

```bash
# Render shell, from the kb-backend service:
python3 scripts/migrate_anon_workspaces.py --dry-run     # preview counts
python3 scripts/migrate_anon_workspaces.py --delete-all  # nuclear
# OR:
python3 scripts/migrate_anon_workspaces.py --assign-to <your-new-stack-sub>
```

### 5.5 Set the three env vars on BOTH services and redeploy

On `kb-backend`:

```ini
STACK_PROJECT_ID          = <from 5.1>
STACK_SECRET_SERVER_KEY   = <from 5.1>
```

On `kb-frontend`:

```ini
NEXT_PUBLIC_STACK_PROJECT_ID              = <from 5.1>
NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY  = <from 5.1>
STACK_SECRET_SERVER_KEY                   = <from 5.1>
```

Redeploy **both services in the same window**. A backend-only deploy
would 401 every live frontend tab.

### 5.6 Verify the flip

```bash
# Unauthed request on any workspace route → 401
curl -i https://<be>.onrender.com/api/v1/workspaces
# HTTP/1.1 401 Unauthorized

# Template catalog is still public — no 401
curl -i https://<be>.onrender.com/api/v1/templates | head -n 1
# HTTP/1.1 200 OK

# Visit the frontend — middleware bounces unauth'd to /handler/sign-in
open https://<fe>.onrender.com/
```

### 5.7 First user sign-up

1. Open `https://<fe>.onrender.com/` → redirects to `/handler/sign-in`.
2. Click **Sign up**, create an account (email/password or an OAuth
   provider that Stack Auth supports).
3. On first JWT the backend auto-creates a `users` row and emits an
   `auth.signup` audit event — confirm via `/profile`.

---

## 6. Tighten CORS

`render.yaml` pre-wires `CORS_ALLOW_ORIGINS` to the frontend's Render
host via `fromService`, so the backend only accepts requests from that
exact origin once both services are up. If you later add a custom
domain, update the value manually to a comma-separated list.

---

## 7. Local Docker parity check

Reproduce the prod setup before pushing:

```bash
# Backend
docker build -t kb-backend .
docker run --rm -p 8000:8000 \
  -e OPENROUTER_API_KEY=... \
  -e DATABASE_URL=postgresql://... \
  -e MEMGRAPH_URI=bolt+ssc://... \
  -e MEMGRAPH_USERNAME=... -e MEMGRAPH_PASSWORD=... \
  -e QDRANT_URL=https://... -e QDRANT_API_KEY=... \
  -e BLOB_READ_WRITE_TOKEN=... \
  -e UPSTASH_REDIS_REST_URL=... -e UPSTASH_REDIS_REST_TOKEN=... \
  -e STACK_PROJECT_ID=... -e STACK_SECRET_SERVER_KEY=... \
  kb-backend

# Frontend (Stack Auth only activates when all 3 NEXT_PUBLIC_* build args are set)
cd frontend
docker build -t kb-frontend \
  --build-arg NEXT_PUBLIC_API_BASE=http://host.docker.internal:8000 \
  --build-arg NEXT_PUBLIC_STACK_PROJECT_ID=... \
  --build-arg NEXT_PUBLIC_STACK_PUBLISHABLE_CLIENT_KEY=... \
  --build-arg STACK_SECRET_SERVER_KEY=... \
  .
docker run --rm -p 3000:3000 \
  -e STACK_SECRET_SERVER_KEY=... \
  kb-frontend
```

On macOS `host.docker.internal` lets the frontend container reach the
backend running on the host.

---

## 8. Observability & admin

- **Render logs** — every uvicorn + Next.js log line is stream-searchable
  in the Render dashboard. No external aggregator required for Phase 1.
- **Audit trail** — `/profile` in the UI lists every user action. Raw
  access: `GET /api/v1/me/audit` (paginated, filterable by action /
  workspace).
- **Wipe everything** — `python3 scripts/wipe_all.py` clears Memgraph +
  Qdrant + Blob + Neon in sequence. Idempotent.
- **Per-workspace history** — `/history` in the UI reads from Neon
  `build_jobs`, `chat_messages`, `config_versions`, `kb_uploads`.

---

## Common pitfalls

- **`401 Unauthorized` right after flipping Stack Auth on** — expected
  on any open tab that was authenticated with dev mode. Refresh; the
  middleware bounces to sign-in.
- **`401 Unauthorized` during local dev** — Stack Auth vars are
  partially set. Either set all three or unset all three. A partial
  config throws off the client SDK.
- **Backend starts but `/graph/*` returns 503** — expected until the
  first workspace is built. Hit `POST /api/v1/build`.
- **`orphaned — process restarted` on old build jobs** — startup-time
  scan flips any `running` row to `error`. Cosmetic; new builds work.
- **Qdrant collection limit** — each workspace = one Qdrant collection
  (`kb-{short_wid}`). Free tier is ~100. Delete unused workspaces via
  `DELETE /api/v1/workspaces/{id}` (cascades Memgraph + Qdrant + Blob +
  Neon).
- **CORS errors after a custom-domain switch** — remember to update
  `CORS_ALLOW_ORIGINS` on the backend; `fromService` only tracks the
  Render `.onrender.com` host.
