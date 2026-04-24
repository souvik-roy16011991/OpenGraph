# 01 — Getting started

This is the "first 10 minutes" guide. It assumes you've been given a link to an OpenGraph instance (production, staging, or local) and nothing else.

## What am I looking at?

OpenGraph turns a pile of knowledge documents — policies, runbooks, integration manuals, JSON catalogs — into a **typed knowledge graph** you can traverse visually or query with natural language. The workflow is:

1. Sign in.
2. Create or pick a **workspace** (one workspace = one graph).
3. Upload your documents.
4. Tell the app what domain these documents cover (a few sentences).
5. Click **Build**.
6. Chat with it or explore it.

Everything else in the app is in service of those six steps.

## The minimum happy path (10 minutes)

### 1. Get in

Go to the app URL. You'll land on `/sign-in`. Either:

- Click **Continue with GitHub** → approve the GitHub OAuth prompt → you're in.
- Or click **Don't have an account? Sign up** → fill email + password → you're in.

Details: [02 — Accounts & sign-in](02-accounts-and-sign-in.md).

### 2. Pick a starting point

After signing in you land at `/` (home). You have two sensible entry points:

- **Browse templates** — 29 pre-seeded domain profiles (healthcare, finance, legal, engineering, etc.). Clicking one creates a workspace with domain fields pre-filled. This is usually the fastest way to get going. → [04 — Templates](04-templates.md).
- **New graph** — click **+ New graph** in the sidebar. Start with a blank workspace. You'll fill the domain profile yourself in step 4. → [03 — Workspaces](03-workspaces.md).

### 3. Upload your documents

You're now at `/upload`. Two drop zones:

- **Knowledge base files** (left) — your domain content. Policies, procedures, how-tos, reference PDFs, prior tickets, transcripts.
- **Tool knowledge base files** (right) — catalogs of the systems your domain uses. API specs, integration docs, tool lists.

Supported: **PDF, DOCX, PPTX, TXT, JSON**. Large PDFs get routed through vision OCR — you'll see a parse-progress card while it runs. Details: [05 — Uploading files](05-uploading-files.md).

When everything shows up in the "Uploaded" lists below each zone, click **Save & continue**.

### 4. Write the domain profile

`/domain` has five fields. They all feed into the LLM's prompts, so write them like you're briefing a new analyst on their first day:

- **Domain name** — a slug, e.g. `loan_underwriting`. Machine-readable.
- **Domain display name** — the human version, e.g. `Loan Underwriting Operations`.
- **Organization name** — who this graph represents, e.g. `Credit Underwriting Desk, Midwest Region`.
- **Knowledge focus examples** — comma-separated topics your KB actually covers. Keep it concrete: `applicant income verification, FOIR calculation, bureau score interpretation, exception escalation`.
- **Tool focus examples** — concrete systems: `CIBIL, Experian, internal KYC platform, loan origination system`.

If you used a template, these are pre-filled — check them and continue. Details: [06 — Domain profile](06-domain-profile.md).

### 5. Graph configuration — skip it the first time

`/graph-config` has six tabs and roughly 40 sliders. **Defaults are good.** Click **Save & continue** without touching anything; you can come back and tune later. Details: [07 — Graph configuration](07-graph-config.md).

### 6. Build

`/build`. Click **Start build**. Leave both toggles as-is for your first run (don't skip embeddings, don't skip LLM cross-links).

You'll see:
- A status badge (queued → running → done)
- A stage label (Parse → Extract → Edges → Embed → Cross-link → Persist)
- A percent + progress bar
- A live log tail (the last ~50 lines from the worker)

Typical duration:
- **Small KB** (<50 pages, no OCR): 30–60 seconds
- **Medium KB** (a few hundred pages): 1–3 minutes
- **Large KB** (thousands of pages, heavy OCR): 5–15 minutes

When it's green, click **Explore the graph**. Details: [08 — Building graphs](08-building-graphs.md).

### 7. See it, then ask it

- `/explore` — visual graph. Click nodes, filter by type, search semantically. → [09 — Exploring graphs](09-exploring-graphs.md).
- `/chat` — natural language queries. Pick a model, ask questions. → [10 — Chat playground](10-chat-playground.md).

## After the first build, what next?

- **Publish it as an API** — on `/workspaces`, click **Deploy** on your workspace card. You get an API key and `/api/v1/ext/*` endpoints that your own code can call. → [17 — Deploying a workspace](17-deploying-a-workspace.md).
- **Compare two graphs** — `/playground` lets you run the same question against two workspaces side-by-side. Useful when tuning config. → [11 — Playground (compare)](11-playground-compare.md).
- **Upgrade from the free trial** — the trial is capped at 1 workspace / 1 build / 5 chats. The *Book a call* link on `/billing` moves you to pay-as-you-go. → [15 — Billing & limits](15-billing-and-limits.md).

## Mental model recap

- **Workspace** = one isolated graph + its files + its chat history + its config.
- **Template** = a pre-filled domain profile you clone into a new workspace.
- **Build** = the pipeline that turns uploaded files into graph nodes + edges + vector embeddings.
- **Explore** = read-only visual view of the graph.
- **Chat** = LLM agent that traverses the graph to answer questions.
- **Deploy** = expose the workspace at `/api/v1/ext/*` and mint an API key.

That's the whole app.
