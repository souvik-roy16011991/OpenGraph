"""Vision-driven document ingestion pipeline.

Public entrypoint: :func:`parse_document` in :mod:`pipeline` — takes raw
bytes + filename + detected kind and returns a ``ParsedDocResult`` that
matches the JSON shape ``src.graph_builder.parser`` already consumes.

See [parsing-code-test/parse_kb.py](parsing-code-test/parse_kb.py) for
the standalone CLI that this module is ported from.
"""

from __future__ import annotations

from src.kb.doc_parser.mime import DocKind, SUPPORTED_DOC_KINDS, sniff
from src.kb.doc_parser.pipeline import ParsedDocResult, parse_document

__all__ = [
    "DocKind",
    "SUPPORTED_DOC_KINDS",
    "sniff",
    "parse_document",
    "ParsedDocResult",
]
