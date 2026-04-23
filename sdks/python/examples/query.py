"""Minimal SDK example — ask a graph a question.

Run with:

    OPENGRAPH_API_KEY=og_live_... \\
    OPENGRAPH_WORKSPACE_ID=... \\
    OPENGRAPH_BASE_URL=http://localhost:8000 \\
    python examples/query.py

The SDK picks up all three from env vars, so you never need to hardcode
credentials in source.
"""

from __future__ import annotations

import os
import sys

from opengraph_sdk import Client, OpenGraphError


def main() -> int:
    try:
        client = Client()
    except OpenGraphError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    workspace_id = os.environ.get("OPENGRAPH_WORKSPACE_ID")
    if not workspace_id:
        print(
            "Set OPENGRAPH_WORKSPACE_ID to a workspace you own, or pass "
            "workspace_id= explicitly.",
            file=sys.stderr,
        )
        return 1

    question = " ".join(sys.argv[1:]) or "What's in this knowledge base?"
    print(f"Q: {question}\n")

    try:
        resp = client.query(question, workspace_id=workspace_id)
    except OpenGraphError as exc:
        print(f"API error ({exc.status}): {exc}", file=sys.stderr)
        return 2

    print("A:", resp.response)
    if resp.follow_up_suggestions:
        print("\nFollow-ups:")
        for s in resp.follow_up_suggestions:
            print(f"  - {s}")
    if resp.usage:
        print(
            f"\nTokens: {resp.usage.llm_total_tokens} "
            f"(prompt {resp.usage.llm_prompt_tokens} / completion {resp.usage.llm_completion_tokens})"
        )
    print(f"Session id: {resp.session_id} · took {resp.duration_ms} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
