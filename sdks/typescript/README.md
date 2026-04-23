# @opengraph/sdk

Official TypeScript/JavaScript SDK for the [OpenGraph](https://opengraph.example) developer API.

```bash
npm install @opengraph/sdk
# or
pnpm add @opengraph/sdk
# or
yarn add @opengraph/sdk
```

Works in Node ≥ 18 (global `fetch`) and modern browsers. Zero runtime deps.

## Quick start

```ts
import { OpenGraphClient } from "@opengraph/sdk";

const client = new OpenGraphClient({
  apiKey: "og_live_…",                           // or env OPENGRAPH_API_KEY
  baseUrl: "https://api.opengraph.example",      // or env OPENGRAPH_BASE_URL
});

const resp = await client.query({
  workspaceId: "…",                              // or env OPENGRAPH_WORKSPACE_ID
  query: "What are the tools used in the payments pipeline?",
});

console.log(resp.response);
resp.follow_up_suggestions.forEach((s) => console.log("→", s));
```

## Surface

```ts
await client.query({ workspaceId, query, sessionId?, llmModel?, debug? });

// Graph reads
await client.graph.stats({ workspaceId });
await client.graph.search({ q, workspaceId, topK, nodeType });
await client.graph.node(nodeId, { workspaceId });
await client.graph.traverse({ nodeId, workspaceId, maxDepth, edgeTypes });
await client.graph.tree({ workspaceId });
await client.graph.tools({ workspaceId, category, provider, search, limit });
await client.graph.chapters({ workspaceId });

// History
await client.history.chats({ workspaceId, limit });
await client.history.chat(sessionId, { workspaceId });
await client.history.builds({ workspaceId, limit });
await client.history.build(jobId);
```

## Errors

```ts
import {
  AuthError,        // 401
  NotFoundError,    // 404
  ValidationError,  // 400 / 422
  RateLimitError,   // 429 — .retryAfterSeconds
  ServerError,      // 5xx or network
  OpenGraphError,   // base
} from "@opengraph/sdk";

try {
  await client.query({ workspaceId, query: "…" });
} catch (err) {
  if (err instanceof RateLimitError) {
    await new Promise((r) => setTimeout(r, (err.retryAfterSeconds ?? 60) * 1000));
  } else if (err instanceof AuthError) {
    console.error("API key rejected — check /api-keys dashboard.");
  } else {
    throw err;
  }
}
```

## Configuration

Every option can come from an env var:

| Env var | Purpose |
|---|---|
| `OPENGRAPH_API_KEY` | Default API key when `apiKey` is omitted |
| `OPENGRAPH_BASE_URL` | Override the API base |
| `OPENGRAPH_WORKSPACE_ID` | Default workspace for every call |

## Retries

Transient failures (429, 502, 503, 504, network errors) retry with
exponential backoff and honour `Retry-After`. 4xx responses that aren't
429 throw immediately.

```ts
new OpenGraphClient({ apiKey: "…", maxRetries: 5 });
```

## License

MIT.
