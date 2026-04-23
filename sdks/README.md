# OpenGraph SDKs

First-party SDKs for the [OpenGraph](https://opengraph.example) developer
API. The public surface lives at `/api/v1/ext/*` — see the [API docs
dashboard page](../README.md) or the auto-generated Swagger UI at
`/docs` for the full reference.

## Packages

| Language | Package | Install | Source |
|---|---|---|---|
| Python | [`opengraph-sdk`](https://pypi.org/project/opengraph-sdk/) | `pip install opengraph-sdk` | [`sdks/python/`](./python/) |
| TypeScript / JavaScript | [`@opengraph/sdk`](https://www.npmjs.com/package/@opengraph/sdk) | `npm install @opengraph/sdk` | [`sdks/typescript/`](./typescript/) |

Both packages cover the same surface and return the same JSON shape, so
the same API key and workspace work from either language.

## Release process

SDKs release independently via Git tags so a bump in one package never
drags the other.

```bash
# Python (after bumping version in sdks/python/pyproject.toml)
git tag sdk-python-v0.2.0
git push origin sdk-python-v0.2.0

# TypeScript (after bumping version in sdks/typescript/package.json)
git tag sdk-typescript-v0.2.0
git push origin sdk-typescript-v0.2.0
```

GitHub Actions runs the test matrix, verifies the tag version matches
the package manifest, and publishes:

- Python → **PyPI** via [trusted publishing](https://docs.pypi.org/trusted-publishers/) (no long-lived token).
- TypeScript → **npm** with [provenance](https://docs.npmjs.com/generating-provenance-statements) attached.

## Contract compatibility

- **v1 field stability.** `/api/v1/ext/*` responses may gain new fields
  inside a minor release. Removing or retyping a field requires a v2
  path.
- **Cross-language parity.** When a new endpoint ships, both SDKs get the
  method in the same release. If you see a feature in one but not the
  other, it's a bug — file an issue.
- **Breaking change policy.** Any breaking change bumps the SDK major
  version. Keep `api_version="1"` in your client config if you want the
  SDK to refuse responses from a future incompatible server.

## Local development

```bash
# Python
cd sdks/python
pip install -e ".[dev]"
pytest

# TypeScript
cd sdks/typescript
npm install
npm test
npm run build
```

Running the full example end-to-end against your local backend:

```bash
OPENGRAPH_BASE_URL=http://localhost:8000 \
OPENGRAPH_API_KEY=og_live_... \
OPENGRAPH_WORKSPACE_ID=... \
python sdks/python/examples/query.py "your question here"

OPENGRAPH_BASE_URL=http://localhost:8000 \
OPENGRAPH_API_KEY=og_live_... \
OPENGRAPH_WORKSPACE_ID=... \
node sdks/typescript/examples/query.mjs "your question here"
```
