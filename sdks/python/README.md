# opengraph-sdk

Official Python SDK for the [OpenGraph](https://opengraph.example) developer API.

```bash
pip install opengraph-sdk
```

## Quick start

```python
from opengraph_sdk import Client

client = Client(
    api_key="og_live_…",          # or env OPENGRAPH_API_KEY
    base_url="https://api.opengraph.example",  # or env OPENGRAPH_BASE_URL
)

resp = client.query(
    "What are the tools used in the payments pipeline?",
    workspace_id="…",               # or env OPENGRAPH_WORKSPACE_ID
)
print(resp.response)
for s in resp.follow_up_suggestions:
    print("→", s)
```

### Async

```python
import asyncio
from opengraph_sdk import AsyncClient

async def main():
    async with AsyncClient() as client:
        resp = await client.query("hello", workspace_id="…")
        print(resp.response)

asyncio.run(main())
```

## Surface

All methods live under the `Client` / `AsyncClient` classes.

| Call | Method | Endpoint |
|---|---|---|
| `client.query(q, workspace_id=…)` | POST | `/api/v1/ext/query` |
| `client.graph.stats(workspace_id=…)` | GET | `/api/v1/ext/graph/stats` |
| `client.graph.search(q, top_k=10)` | GET | `/api/v1/ext/graph/search` |
| `client.graph.node(id)` | GET | `/api/v1/ext/graph/node/{id}` |
| `client.graph.traverse(node_id, max_depth=3)` | POST | `/api/v1/ext/graph/traverse` |
| `client.graph.tree()` | GET | `/api/v1/ext/graph/tree` |
| `client.graph.tools()` | GET | `/api/v1/ext/graph/tools` |
| `client.graph.chapters()` | GET | `/api/v1/ext/graph/chapters` |
| `client.history.chats()` | GET | `/api/v1/ext/history/chats` |
| `client.history.chat(id)` | GET | `/api/v1/ext/history/chats/{id}` |
| `client.history.builds()` | GET | `/api/v1/ext/history/builds` |
| `client.history.build(id)` | GET | `/api/v1/ext/history/builds/{id}` |

## Errors

Every network / server error subclasses `OpenGraphError`:

```python
from opengraph_sdk import (
    AuthError,       # 401 — bad or missing API key
    NotFoundError,   # 404 — workspace/node doesn't exist (or isn't yours)
    ValidationError, # 400 / 422 — request payload rejected
    RateLimitError,  # 429 — per-key quota exhausted; .retry_after_seconds
    ServerError,     # 5xx or network — safe to retry with backoff
    OpenGraphError,  # base
)
```

## Configuration

Every option can come from an env var:

| Env var | Purpose |
|---|---|
| `OPENGRAPH_API_KEY` | Default API key if `api_key=` is omitted |
| `OPENGRAPH_BASE_URL` | Override the API base |
| `OPENGRAPH_WORKSPACE_ID` | Default workspace for every call |

Constructor overrides always win.

## Retries

Transient failures (429, 502, 503, 504, network errors) are retried with
exponential backoff (and honour `Retry-After` when present). 4xx responses
that aren't 429 are raised immediately — retrying won't fix them.

Override the retry count:

```python
Client(api_key=..., max_retries=5)
```

## License

MIT.
