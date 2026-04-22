"""Cross-cutting observability helpers.

Right now this module only holds usage-accounting primitives — per-build and
per-chat counters that the builds/chat code paths feed into. Kept in its own
package so future work (traces, metrics, structured logs) has a natural home
without cluttering src/api or src/graph_builder.
"""
