"""
Billing package — trial enforcement, credit ledger, rate card.

Phase 1 contents:
- ``rate_card``: pure-function pricing (``cost_build``, ``cost_chat``).
- ``ledger``: atomic credit grants + debits against ``billing_accounts``.
- ``enforcement``: pre-action checks that raise HTTP 402 on insufficient
  balance / trial cap exceeded.
- ``storage_sweeper``: daily storage-fee proration, idempotent via
  ``(workspace_id, date)`` dedupe key in ``credit_transactions``.

Phase 2 adds ``stripe_client``. Phase 4 adds ``secrets`` (Fernet for BYOK).
"""

from src.billing.enforcement import (
    PaymentRequired,
    USER_DAILY_PARSE_USD,
    USER_PARSE_QUEUE_CAP,
    check_build_allowed,
    check_chat_allowed,
    check_document_parse_allowed,
    check_workspace_create_allowed,
)
from src.billing.ledger import debit, grant, get_balance
from src.billing.rate_card import cost_build, cost_chat, cost_document_parse

__all__ = [
    "PaymentRequired",
    "USER_DAILY_PARSE_USD",
    "USER_PARSE_QUEUE_CAP",
    "check_build_allowed",
    "check_chat_allowed",
    "check_document_parse_allowed",
    "check_workspace_create_allowed",
    "debit",
    "grant",
    "get_balance",
    "cost_build",
    "cost_chat",
    "cost_document_parse",
]
