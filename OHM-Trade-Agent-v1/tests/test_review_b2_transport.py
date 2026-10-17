"""B2 regression (review evidence, not a new acceptance criterion).

Proves that a canonical-read transport failure (connection refused, timeout,
interrupted read) fails closed into OUTCOME_BLOCKED /
REASON_UNUSABLE_CANONICAL_STATE with zero canonical writes, rather than
propagating an uncaught exception or fabricating successful evidence.
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
from app.services.paper_v2_capital_admission_execution import (  # noqa: E402
    OUTCOME_BLOCKED,
    REASON_UNUSABLE_CANONICAL_STATE,
)
from tests.test_opip_paper_v2_execution_bc3 import NOW  # noqa: E402
from tests.test_opip_r5_e_research_paper_execution import (  # noqa: E402
    _counts,
    _dispatch,
)


@pytest.fixture(autouse=True)
def _frozen_producer_clock(monkeypatch):
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


class _TransportFailingClient:
    """A canonical client whose execution-state read raises a transport error."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def get_paper_v2_execution_state(self, disposition_id: str):
        raise self.error


def test_b2_connection_failure_blocks_and_writes_nothing(env):
    server, _live_client = env
    before = _counts(server.writer)

    outcome = _dispatch(env, client=_TransportFailingClient(OSError("connection refused")))

    after = _counts(server.writer)
    assert outcome.status == OUTCOME_BLOCKED
    assert outcome.reason_code == REASON_UNUSABLE_CANONICAL_STATE
    assert outcome.admission_consumed is False
    assert outcome.execution is None
    assert after == before


def test_b2_timeout_blocks_and_writes_nothing(env):
    server, _live_client = env
    before = _counts(server.writer)

    outcome = _dispatch(env, client=_TransportFailingClient(TimeoutError("read timed out")))

    after = _counts(server.writer)
    assert outcome.status == OUTCOME_BLOCKED
    assert outcome.reason_code == REASON_UNUSABLE_CANONICAL_STATE
    assert outcome.admission_consumed is False
    assert outcome.execution is None
    assert after == before


def test_b2_programming_errors_remain_visible(env):
    """A malformed canonical response (programming/data error) must not be masked."""

    class _MalformedClient:
        def get_paper_v2_execution_state(self, disposition_id: str):
            raise KeyError("status")

    with pytest.raises(KeyError):
        _dispatch(env, client=_MalformedClient())
