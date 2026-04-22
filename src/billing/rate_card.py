"""
Pure-function pricing.

Takes the usage payloads already captured by the observability work
(``BuildJobRow.stats.usage``, ``ChatMessage.response.usage``) and returns
integer credits. No I/O, no async. The ledger calls these to compute
deductions.

1 credit = $0.01 USD. Rate card locked in the pricing plan:

    LLM input       0.30 credits / 1K tokens   (waived on BYOK)
    LLM output      0.60 credits / 1K tokens   (waived on BYOK)
    Embedding       0.01 credits / 1K tokens   (waived on BYOK)
    Vector storage  10 credits / GB / month    (daily prorated)
    Graph storage   20 credits / GB / month    (daily prorated)
    Build baseline   5 credits / successful build
    Chat baseline    0.5 credits / chat turn

Values are computed as ints by rounding half-up from the exact decimal
price so sub-credit usage never rounds to zero (a 100-token chat still
costs something).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants (credits per unit)
# ---------------------------------------------------------------------------

# Per 1K tokens
LLM_INPUT_CREDIT_PER_1K = Decimal("0.30")
LLM_OUTPUT_CREDIT_PER_1K = Decimal("0.60")
EMBEDDING_CREDIT_PER_1K = Decimal("0.01")

# Per GB per month (applied daily by storage_sweeper at 1/30)
VECTOR_STORAGE_CREDIT_PER_GB_MONTH = Decimal("10")
GRAPH_STORAGE_CREDIT_PER_GB_MONTH = Decimal("20")

BUILD_BASELINE_CREDITS = 5
CHAT_BASELINE_CREDITS = Decimal("0.5")

# Failed builds under this credit amount are forgiven entirely — the user
# hit a trivial error and shouldn't pay for it. Above this threshold we
# still debit tokens burned (real work done); no baseline fee either way.
FAILED_BUILD_FORGIVENESS_THRESHOLD = 50

_BYTES_PER_GB = 1024 ** 3


def _round(value: Decimal) -> int:
    """Round a Decimal half-up to nearest int. Never returns negative."""
    if value <= 0:
        return 0
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# ---------------------------------------------------------------------------
# Build cost
# ---------------------------------------------------------------------------

def cost_build(
    usage: Optional[dict[str, Any]],
    has_byok: bool = False,
    failed: bool = False,
) -> int:
    """Compute the credit cost of a single build.

    ``usage`` is the ``stats.usage`` sub-object written by the worker:
    ``llm_prompt_tokens``, ``llm_completion_tokens``, ``embedding_prompt_tokens``.

    When ``has_byok`` is True the user is paying OpenRouter directly, so we
    waive the LLM + embedding token credits. Baseline + storage proration
    (handled elsewhere) still apply.

    ``failed`` suppresses the baseline fee and forgives costs below
    ``FAILED_BUILD_FORGIVENESS_THRESHOLD`` outright.
    """
    usage = usage or {}

    token_credits = Decimal(0)
    if not has_byok:
        input_k = Decimal(int(usage.get("llm_prompt_tokens") or 0)) / Decimal(1000)
        output_k = Decimal(int(usage.get("llm_completion_tokens") or 0)) / Decimal(1000)
        embed_k = Decimal(int(usage.get("embedding_prompt_tokens") or 0)) / Decimal(1000)
        token_credits = (
            input_k * LLM_INPUT_CREDIT_PER_1K
            + output_k * LLM_OUTPUT_CREDIT_PER_1K
            + embed_k * EMBEDDING_CREDIT_PER_1K
        )

    baseline = 0 if failed else BUILD_BASELINE_CREDITS
    total = _round(token_credits) + baseline

    if failed and total <= FAILED_BUILD_FORGIVENESS_THRESHOLD:
        return 0
    return total


# ---------------------------------------------------------------------------
# Chat cost
# ---------------------------------------------------------------------------

def cost_chat(
    usage: Optional[dict[str, Any]],
    has_byok: bool = False,
) -> int:
    """Compute the credit cost of a single chat turn.

    ``usage`` is the ``ChatUsage`` shape written by the chat handler:
    ``llm_prompt_tokens``, ``llm_completion_tokens``. BYOK waives tokens;
    baseline always applies.
    """
    usage = usage or {}

    token_credits = Decimal(0)
    if not has_byok:
        input_k = Decimal(int(usage.get("llm_prompt_tokens") or 0)) / Decimal(1000)
        output_k = Decimal(int(usage.get("llm_completion_tokens") or 0)) / Decimal(1000)
        token_credits = (
            input_k * LLM_INPUT_CREDIT_PER_1K
            + output_k * LLM_OUTPUT_CREDIT_PER_1K
        )

    return _round(token_credits + CHAT_BASELINE_CREDITS)


# ---------------------------------------------------------------------------
# Daily storage cost (called by sweeper; one call per workspace per day)
# ---------------------------------------------------------------------------

def cost_storage_daily(
    graph_payload_bytes: int,
    vector_count: int,
    vector_dimension: int,
) -> int:
    """Compute the per-day storage credits for a workspace.

    Vector storage is approximated as ``vectors × dimension × 4 bytes``
    (float32). Graph storage comes straight from the captured
    ``graph_payload_bytes``. Both are prorated at 1/30 of the monthly rate.
    """
    vector_bytes = max(0, int(vector_count or 0)) * max(0, int(vector_dimension or 0)) * 4
    graph_bytes = max(0, int(graph_payload_bytes or 0))

    if vector_bytes == 0 and graph_bytes == 0:
        return 0

    vector_gb = Decimal(vector_bytes) / Decimal(_BYTES_PER_GB)
    graph_gb = Decimal(graph_bytes) / Decimal(_BYTES_PER_GB)

    monthly = (
        vector_gb * VECTOR_STORAGE_CREDIT_PER_GB_MONTH
        + graph_gb * GRAPH_STORAGE_CREDIT_PER_GB_MONTH
    )
    daily = monthly / Decimal(30)
    return _round(daily)
