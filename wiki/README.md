# OpenGraph — User Wiki

Welcome. This wiki is written from a user's point of view. It answers "how do I X?" and "what does Y mean?" for every button, page, and flow in the app. If you're looking for architecture or API internals, see [DEPLOY.md](../DEPLOY.md) and the top-level [README.md](../README.md) in the repo root — this wiki intentionally avoids those.

**What OpenGraph is, in one sentence:** upload your knowledge base (policies, runbooks, integration docs), tune a few knobs, click *Build*, and get a typed graph you can explore visually or chat with using any OpenRouter model.

## Where do I start?

| I want to… | Read |
|---|---|
| Just try it — what am I doing here? | [01 — Getting started](01-getting-started.md) |
| Create an account or sign in | [02 — Accounts & sign-in](02-accounts-and-sign-in.md) |
| Understand workspaces | [03 — Workspaces](03-workspaces.md) |
| Pick a pre-built template | [04 — Templates](04-templates.md) |
| Upload my knowledge files | [05 — Uploading files](05-uploading-files.md) |
| Tell the graph about my domain | [06 — Domain profile](06-domain-profile.md) |
| Tune how the graph is built | [07 — Graph configuration](07-graph-config.md) |
| Run a build | [08 — Building graphs](08-building-graphs.md) |
| See my graph visually | [09 — Exploring graphs](09-exploring-graphs.md) |
| Chat with my graph | [10 — Chat playground](10-chat-playground.md) |
| Compare two graphs side-by-side | [11 — Playground (compare)](11-playground-compare.md) |
| Review my past activity | [12 — History](12-history.md) |
| Use OpenGraph from my own code | [13 — API keys](13-api-keys.md) · [14 — Public API](14-public-api.md) |
| Understand what I'm being charged for | [15 — Billing & limits](15-billing-and-limits.md) |
| Change my name, sign out | [16 — Profile & audit](16-profile-and-audit.md) |
| Publish a graph as a live API | [17 — Deploying a workspace](17-deploying-a-workspace.md) |
| Move around faster | [18 — Navigation & shortcuts](18-navigation-shortcuts.md) |
| Fix a specific error | [19 — Troubleshooting](19-troubleshooting.md) |
| Understand a term | [20 — Glossary](20-glossary.md) |
| See the common-question quick list | [21 — FAQ](21-faq.md) |

## The 6-step wizard at a glance

Most users spend most of their time here. Every workspace goes through the same linear flow the first time; after that, you can revisit any step to change things and rebuild.

```
  /upload  →  /domain  →  /graph-config  →  /build  →  /explore  →  /chat
    (1)         (2)            (3)           (4)         (5)         (6)
    files     context        knobs          run it       see it     ask it
```

1. **Upload** your knowledge files (PDFs, docs, JSON). The app parses them in the background via vision OCR.
2. **Domain profile** — five short fields that tell the LLM what domain this is (e.g., loan underwriting vs. hospital operations). These feed into every prompt.
3. **Graph configuration** — ~40 tunable knobs that control density, search quality, edge types, embedding model. Defaults are sane.
4. **Build** runs the pipeline: parse → extract nodes → build edges → embed → cross-link → write to graph store. Takes 30s–5 min.
5. **Explore** the graph visually in Cytoscape. Click nodes, filter by type, search semantically.
6. **Chat** with a LangGraph agent that traverses the graph to answer questions. Pick any of 342 OpenRouter models.

## What's not obvious on first use

- **A workspace is the unit of isolation.** Files, graphs, chats, API keys — all scoped to one workspace. You pick an active workspace via the sidebar workspace switcher.
- **You can always revisit a wizard step.** The sidebar lets you jump to any wizard page for the active workspace. "Save & continue" isn't a one-way street.
- **Rebuilds are cheap to trigger, expensive to run.** Config changes in the *Traversal* and *Search* tabs apply live with no rebuild. Everything else needs a rebuild.
- **The public API is opt-in, per workspace.** Building a graph doesn't expose it. You have to click **Deploy** on `/workspaces` to publish. See [17 — Deploying a workspace](17-deploying-a-workspace.md).
- **GitHub sign-in auto-links by email.** If you already have a password account at `alice@example.com` and sign in with a GitHub account whose verified primary email is also `alice@example.com`, the two identities merge. Your password keeps working; you just get a second way in.
- **Free trial caps are strict.** 1 workspace, 1 build, 5 chat turns. See [15 — Billing & limits](15-billing-and-limits.md) for the upgrade path.
