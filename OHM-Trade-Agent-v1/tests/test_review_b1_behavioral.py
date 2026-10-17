"""B1 behavioural regression (review evidence, not a new acceptance criterion).

Proves that an already-admitted dispatch reuses the authoritative committed
decision context and adds no second context, reservation or capital allocation.

This lives in a review file rather than the ATDD-scoped acceptance suite because
the scope gate maps that suite 1:1 to adopted acceptance criteria; B1 is a defect
repair, whose regression belongs alongside the structural B1 review evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.canonical.client import InProcessWriterClient  # noqa: E402
from app.opip.canonical.server import CanonicalWriterServer  # noqa: E402
from tests.test_opip_paper_v2_execution_bc3 import NOW  # noqa: E402
from tests.test_opip_r5_e_research_paper_execution import (  # noqa: E402
    _counts,
    _dispatch,
    _portfolio,
    _seed_committed_admission,
)


@pytest.fixture(autouse=True)
def _frozen_producer_clock(monkeypatch):
    """Freeze the producer clock to the same NOW the BC3 harness uses."""
    monkeypatch.setattr(
        "app.services.paper_v2_execution.system_utc_clock", lambda: NOW
    )


@pytest.fixture
def env(tmp_path):
    server = CanonicalWriterServer(
        db_path=tmp_path / "canonical.sqlite3",
        socket_path=tmp_path / "canonical.sock",
    )
    try:
        yield server, InProcessWriterClient(server)
    finally:
        server.stop()


def test_b1_behavioral_already_admitted_commits_no_second_context(env, monkeypatch):
    """I1/I2: an already-admitted dispatch reuses the committed context and adds no new capital."""

    server, live_client = env
    import app.services.paper_v2_execution as producer

    # 1. establish the authoritative committed admission through the real producer
    #    path, BEFORE any counter is installed so the seed is never miscounted.
    _seed_committed_admission(env)
    baseline_counts = _counts(server.writer)
    baseline_portfolio = _portfolio(server.writer)

    # 2. counters cover only the dispatch under test.
    calls = {"commit_decision_context": 0, "admit": 0}
    real_commit = getattr(producer, "commit_decision_context", None)
    if real_commit is not None:
        def _counting_commit(*a, **k):
            calls["commit_decision_context"] += 1
            return real_commit(*a, **k)
        monkeypatch.setattr(
            producer, "commit_decision_context", _counting_commit, raising=False
        )
    real_admit = getattr(producer, "_admit", None)
    if real_admit is not None:
        def _counting_admit(*a, **k):
            calls["admit"] += 1
            return real_admit(*a, **k)
        monkeypatch.setattr(producer, "_admit", _counting_admit, raising=False)

    # 3. dispatch the already-admitted opportunity.
    outcome = _dispatch(env)

    after_counts = _counts(server.writer)
    after_portfolio = _portfolio(server.writer)

    assert outcome.status == "EXECUTED", (
        outcome.status, outcome.reason_code, outcome.detail,
    )
    assert calls["commit_decision_context"] == 0, (
        "the already-admitted dispatch committed a second decision context "
        f"(calls={calls['commit_decision_context']})"
    )
    assert calls["admit"] == 0, (
        "the already-admitted dispatch entered the producer admission path again "
        f"(calls={calls['admit']})"
    )
    for key in ("admission_request", "disposition"):
        assert after_counts[key] == baseline_counts[key], (
            f"the dispatch created a new durable {key} row "
            f"({baseline_counts[key]} -> {after_counts[key]})"
        )
    assert after_portfolio == baseline_portfolio, (
        "reserved capital or active reservations changed across the dispatch "
        f"({baseline_portfolio} -> {after_portfolio})"
    )
