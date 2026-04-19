"""
Process-wide workspace context via contextvars.

Motivation: the graph-builder pipeline is a dense tree of function calls that
all need to know which workspace they are operating on (for Memgraph filter,
Qdrant collection name, Blob path prefix, local /data subfolder, Neon FK,
etc.). Threading a workspace_id through every signature is invasive, so we
park it in a contextvar that FastAPI middleware and the build thread both set
on entry.

Usage:
    # In FastAPI dep:  set_current_workspace(ws_id)
    # Anywhere deep:   ws = require_current_workspace()
"""
from __future__ import annotations

import contextvars
from typing import Optional

_current_workspace: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "workspace_id", default=None
)


def set_current_workspace(workspace_id: Optional[str]) -> contextvars.Token:
    """Set the active workspace_id for the current async task / thread.

    Returns a Token that can be passed to :func:`reset_current_workspace`
    to restore the previous value (useful in tests).
    """
    return _current_workspace.set(workspace_id)


def reset_current_workspace(token: contextvars.Token) -> None:
    _current_workspace.reset(token)


def get_current_workspace() -> Optional[str]:
    """Return the active workspace_id or None if unset."""
    return _current_workspace.get()


def require_current_workspace() -> str:
    """Same as :func:`get_current_workspace` but raises if unset."""
    wid = _current_workspace.get()
    if not wid:
        raise RuntimeError(
            "No workspace is active. Set the X-Workspace-Id header, "
            "call set_current_workspace(...) before invoking the pipeline, "
            "or create a workspace first via POST /api/v1/workspaces."
        )
    return wid
