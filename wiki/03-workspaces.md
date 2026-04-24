# 03 — Workspaces

A **workspace** is the unit of isolation. One workspace = one knowledge graph + its uploaded files + its chat history + its config + its API keys. Nothing is shared across workspaces. Nothing is shared across users.

If you have a question like "how do I keep my legal team's graph separate from my ops team's graph?" — the answer is always "two workspaces."

## The `/workspaces` page

Sidebar → **My Graphs**, or go to `/workspaces` directly. You see a grid of cards, one per workspace.

Each card shows:

| Element | Meaning |
|---|---|
| **Title** | The name you gave the workspace |
| **Description** | Optional subtitle (you set this at creation or via inline edit) |
| **Status dot** | Green (deployed), green-faded (built, not deployed), blue-pulsing (currently building), red (last build errored), gray (never built) |
| **File count** | How many knowledge + tool files are uploaded |
| **Node count** | Size of the graph (set after a successful build) |
| **Updated X ago** | Time since last config change or build |

And four action buttons per card:

- **Open** — if built, goes to `/explore`; if not built, goes to `/upload`. Use this for day-to-day work.
- **Edit** (pencil icon) — goes to `/upload` for this workspace (lets you add/remove files, change domain, tune config, rebuild).
- **Rebuild** (play icon) — goes to `/build?autostart=1`, which kicks off a rebuild immediately. Disabled while a build is in-flight.
- **Deploy** — opens the *Deploy* dialog. If the workspace is built and not deployed, this publishes it to `/api/v1/ext/*` and mints an API key. If already deployed, it's a *Redeploy* (mints a fresh key, restamps the workspace as live). Disabled until the first successful build. See [17 — Deploying a workspace](17-deploying-a-workspace.md).
- **Delete** (trash icon) — see below.

The page polls every 10 seconds while you're on it, so build status updates without a manual refresh.

## Creating a workspace

### From scratch (blank)

1. Click **+ New graph** in the sidebar (or on the `/workspaces` page).
2. The *Create workspace* dialog opens:
   - **Name** *(required)* — how it shows up in the UI and workspace switcher.
   - **Description** *(optional)* — helps you remember what this one is for.
3. Click **Create**.

The app:
- Creates the workspace via `POST /api/v1/workspaces`.
- Sets it as your **active workspace**.
- Routes you to `/upload` so you can start adding files.

No domain profile yet — you'll fill that in at step 2 of the wizard (`/domain`).

### From a template

See [04 — Templates](04-templates.md). In short: go to `/templates`, pick one, click **Use this template**. A new workspace is created with the template's domain profile pre-filled. You still upload your own files — templates don't ship with content.

## The active workspace

At any time there's exactly one "active" workspace. The app shows it in the **workspace switcher** at the top of the sidebar. Everything workspace-scoped (upload, domain, graph config, build, explore, chat, history) uses the active workspace automatically.

### Changing active workspace

- **Sidebar workspace switcher** → click, pick a different workspace from the dropdown.
- The `/chat` page has its own independent picker — you can chat with workspace A while your wizard-flow active workspace is B. The two don't interfere.

### If no workspace is active

If you try to visit `/upload`, `/domain`, `/graph-config`, `/build`, `/explore`, or `/history` without an active workspace, the app shows a **workspace picker modal** over the page. Pick one or create a new one to continue.

`/workspaces`, `/templates`, and `/playground` don't require an active workspace — they let you pick inline.

## Editing a workspace's name & description

Two ways:

1. On `/workspaces`, click the pencil icon on the card → opens the wizard at `/upload` with that workspace active. The name isn't edited there; this path is for *content* editing.
2. Inline: some versions of the card have click-to-edit on the title. If not, use the API: `PATCH /api/v1/workspaces/{id}` with `{name, description}`.

## Deleting a workspace

1. On `/workspaces`, click the **trash icon** on the card.
2. A confirmation dialog appears listing what will be deleted:
   - N knowledge files
   - N tool files
   - N graph nodes
3. Click **Delete**.

The backend runs a cascade:

- Deletes the Neon rows (workspace, files, builds, chats, configs).
- Deletes the Memgraph nodes + edges tagged with this `workspace_id`.
- Deletes the Qdrant collection (or the filter-scoped rows in shared mode).
- Deletes the Vercel Blob files under this workspace's prefix.

**This is irreversible.** There's no undo, no trash, no 30-day grace period. If the cascade fails partway (e.g. a side store is down), the workspace is soft-deleted and a background saga retries the cleanup — but from your perspective it disappears immediately.

If the deleted workspace was your active workspace, the switcher resets; pick another one or create a new one.

## What's inside a workspace

Think of a workspace as a folder with these contents:

- **Files** — the KB JSONs + raw documents you uploaded. Stored in Vercel Blob at a path prefix scoped to this workspace.
- **Domain profile** — five fields you set on `/domain`. Seeds every LLM prompt that touches this workspace's graph.
- **Graph config** — six sections of knobs. The exact values used the last time you built.
- **Graph** — nodes + edges in Memgraph, tagged with this workspace's ID.
- **Vectors** — embeddings in Qdrant, one collection per workspace (unless the operator flipped `QDRANT_SHARED_COLLECTION`).
- **Chat sessions** — every chat you've had, preserved. See [12 — History](12-history.md).
- **Build history** — every build, with its config snapshot at the time and its log tail. See [12 — History](12-history.md).
- **Config history** — every time you changed domain or graph config. See [12 — History](12-history.md).
- **Upload audit** — every file upload, with filename, sha256, timestamp.
- **API keys** *(if workspace-scoped)* — keys minted by **Deploy** on this workspace. Account-scoped keys work against any workspace you own.

## Workspace limits

The free trial allows **1 workspace per user**. On Enterprise plans the default cap is **25 workspaces per user** (operator-configurable via `MAX_WORKSPACES_PER_USER`). Hit the cap and you'll get a 402 when you try to create the next one.

See [15 — Billing & limits](15-billing-and-limits.md).

## FAQ

**Q: Can I move a workspace from one account to another?** Not in the UI. Operators can reassign ownership via the SQL console.

**Q: Can I clone a workspace?** Not today. The closest thing is: create a new workspace from the same template and re-upload the same files.

**Q: If I delete a workspace, are my API keys for it still valid?** No — workspace-scoped keys become orphans (they'll 404 on the workspace lookup). Account-scoped keys continue to work for other workspaces.

**Q: Does deleting a workspace reduce my trial counters?** No. Trial limits are lifetime-cumulative — deleting a workspace you already built doesn't give you the build back.

**Q: I accidentally deleted a workspace — can I recover it?** Not through the UI. The backend does soft-delete briefly before running the cascade; if you email support within a few minutes, recovery may be possible. After the cascade completes, no.

**Q: What if the workspace dot is red?** The last build failed. Click the card → *Edit* → the `/build` page shows the error. Fix it (usually a bad KB file or a tuning knob that's too aggressive) and click *Rebuild*. See [08 — Building graphs](08-building-graphs.md).
