# Deploying to Render

The repo ships with a [`render.yaml`](./render.yaml) Blueprint that
provisions **two stateless Docker services** — no disks, no stateful
pods. All runtime state lives in managed cloud stores. 

| Service        | Purpose                                   | Port |
| -------------- | ----------------------------------------- | ---- |
| `opengraph-backend`   | FastAPI — all API routes + build workers  | auto |
| `opengraph-frontend`  | Next.js 15 production server              | auto |

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
| **Memgraph Cloud**      | knowledge graph nodes + edges                     | memgraph.com/cloud         | `MEMGRAPH_URI`, `_USERNAME`, `_PASSWORD` |
| **Qdrant Cloud**        | embedding vectors (one collection per workspace)  | qdrant.tech                | `QDRANT_URL`, `QDRANT_API_KEY` |
| **Vercel Blob**         | uploaded KB JSONs (authoritative file store)      | vercel.com/dashboard/stores | `BLOB_READ_WRITE_TOKEN` |
| **OpenRouter**          | LLM + embedding API (any of 342 models)           | openrouter.ai              | `OPENROUTER_API_KEY` |
| **Upstash Redis**       | JWKS cache, cross-link cache, model-catalog cache | upstash.com                | `UPSTASH_REDIS_REST_URL`, `_TOKEN` |

**Auth is first-party JWT (HS256).** Generate a 48-byte secret and paste it
into `JWT_SECRET` on the backend; users sign up via `POST /api/v1/auth/signup`
and log in via `POST /api/v1/auth/login`. Protected routes return 503 until
`JWT_SECRET` is set.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

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

The first deploy will **fail on `opengraph-backend`** because the `sync: false`
secrets aren't set yet. Fill them in next.

### Required secrets on `opengraph-backend`

Open `opengraph-backend` → **Environment** tab → add:

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

# Auth — REQUIRED. Generate with:
#   python -c "import secrets; print(secrets.token_urlsafe(48))"
JWT_SECRET                = <48-byte urlsafe string>
JWT_EXPIRES_MINUTES       = 43200        # 30 days
JWT_ISSUER                = kb-graph-engine
PASSWORD_MIN_LENGTH       = 8
```

Non-secrets (model names, `MAX_WORKSPACES_PER_USER`, CORS target, etc.)
are pre-populated by `render.yaml` and need no editing.

### Required secrets on `opengraph-frontend`

None. The browser stores the JWT in localStorage + a non-HttpOnly
`auth_token` cookie after signup/login — there are no third-party auth
env vars on the frontend. `NEXT_PUBLIC_API_BASE` and `BACKEND_URL` are
auto-wired via `fromService` and do not need manual entry.

### Trigger redeploy

`opengraph-backend` → **Manual Deploy** → Deploy latest commit. Once
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

# 3. JWT auth — sign up (returns { token, user }), then use that token.
TOKEN=$(curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"email":"smoketest@example.com","password":"correct-horse"}' \
  https://<be>.onrender.com/api/v1/auth/signup | jq -r .token)

curl -X POST -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $TOKEN" \
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

## 5. Auth housekeeping

### 5.1 Rotate `JWT_SECRET`

Set a fresh 48-byte secret and redeploy `opengraph-backend`. All existing JWTs
become invalid; every browser bounces to /sign-in on its next request.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
# Paste into Render → opengraph-backend → Environment → JWT_SECRET → redeploy.
```

### 5.2 Pre-flight: sanity-check the `workspaces.domain_config` column

On first Neon snapshot, confirm the column exists (it should, since
`create_all()` ran it at startup):

```bash
psql "$DATABASE_URL" -c "\d workspaces" | grep domain_config
# should show: domain_config | jsonb | nullable
```

### 5.3 Verify

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

### 5.4 First user sign-up

1. Open `https://<fe>.onrender.com/` → redirects to `/sign-in`.
2. Click **Sign up**, enter an email + password (≥ `PASSWORD_MIN_LENGTH`).
3. The backend creates a `users` row, emits an `auth.signup` audit event,
   and returns a JWT — the browser stores it and redirects to `/`.

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

# Frontend — only NEXT_PUBLIC_API_BASE needs to be baked at build time
cd frontend
docker build -t opengraph-frontend \
  --build-arg NEXT_PUBLIC_API_BASE=http://host.docker.internal:8000 \
  .
docker run --rm -p 3000:3000 opengraph-frontend
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

- **`401 Unauthorized` right after rotating `JWT_SECRET`** — expected on
  every open tab. Refresh; middleware bounces to /sign-in.
- **`503 Authentication is not configured`** — `JWT_SECRET` is empty on
  the backend. Set it and redeploy.
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
