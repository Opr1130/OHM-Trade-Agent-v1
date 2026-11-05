"""Derived, reconstructable analytical projection for the O'Pip Control Center.

This package never owns trading, ranking, alerting, paper-admission or
protection authority. It reads append-only evidence that other services already
write, projects it into a bounded SQLite database, reconciles that projection
against the evidence and reports what it could not prove.

Canonical JSONL stays authoritative. If the projection and the evidence
disagree, the projection is marked unverified instead of being preferred.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

__all__ = ["SCHEMA_VERSION"]
