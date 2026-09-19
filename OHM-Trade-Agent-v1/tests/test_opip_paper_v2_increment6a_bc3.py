"""B/C-3 Increment 6A: decision-snapshot proof and handoff prerequisites.

Proves the truthful evidence prerequisites required before Paper-v2 routing can be
wired: a durable canonical decision-snapshot record with a real content binding,
decision-time qualification policy identity, honest build/process provenance,
producer-level activation and direction defense, and a snapshot handoff that
cannot be asserted without the payload that proves it.

Increment 6A does not wire the scan router and does not activate Paper v2.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.exchanges.kraken import KrakenClient
from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.models import WriterAck
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.contracts.episode_snapshot import (
    CANONICAL_EPISODE_SNAPSHOT_RECORD_TYPE,
    CANONICAL_EPISODE_SNAPSHOT_SCHEMA_VERSION,
    canonical_episode_id,
    canonical_snapshot_id,
    validate_canonical_episode_snapshot,
)
from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.paper_execution_runtime import (
    DECISION_SNAPSHOT_EPISODE_RECORD_TYPE,
    DECISION_SNAPSHOT_EPISODE_SCHEMA_VERSION,
    PAPER_DECISION_SNAPSHOT_RECORDED,
    decision_snapshot_idempotency_key,
    validate_decision_snapshot_payload,
)
from app.opip.contracts.process_identity import (
    PROCESS_INSTANCE_PREFIX,
    process_instance_id,
)
from app.opip.contracts.serialization import (
    EPISODE_SNAPSHOT_HASH_PREFIX,
    episode_snapshot_hash,
)
from app.opip.decision.versioning import GATE_POLICY_VERSION, gate_policy_fingerprint
from app.services.canonical_episode_capture import (
    RECORD_TYPE,
    canonical_episode_snapshot_hash,
)
from app.services.paper_v2_decision_snapshot import (
    DecisionSnapshot,
    build_decision_snapshot_payload,
    commit_decision_snapshot,
    decision_snapshot_intent,
    submit_decision_snapshot,
)
from app.services.paper_v2_execution import (
    PaperV2ExecutionError,
    PaperV2Opportunity,
    run_paper_v2_opportunity,
)

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"
REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"

CTX_EVENT = "decision_intelligence.context.recorded"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _snapshot_payload(*, decision: datetime = NOW, symbol: str = "SOLUSD") -> dict:
    """A real production-shaped canonical episode snapshot, built by its builder."""
    from app.services.canonical_episode_capture import build_canonical_episode_snapshots

    observation = SimpleNamespace(
        symbol=symbol,
        base_asset=symbol.removesuffix("USD"),
        kraken_public_symbol=f"{symbol.removesuffix('USD')}/USD",
        last_price=100.0,
        ticker_last=100.0,
        volume_24h=100_000.0,
        notional_24h_usd_approx=1_000_000.0,
        high_24h=105.0,
        low_24h=95.0,
        lift_from_24h_low_pct=5.0,
        distance_from_24h_high_pct=4.0,
    )
    candidate = SimpleNamespace(
        symbol=symbol,
        universe_size=3,
        stage="BREAKOUT_CANDIDATE",
        pattern="REACCELERATION",
        opportunity_score=78.0,
        explosion_potential_score=74.0,
        tradeability_score=72.0,
        pattern_strength_score=80.0,
        volume_acceleration_score=70.0,
        relative_strength_score=88.0,
        persistence_scans=3,
        exhaustion_penalty=10.0,
        exhaustion_band="LOW",
        relative_strength_percentile=95.0,
        suppressed=False,
        reasons=(),
        components={"near_high": 75.0},
    )
    return build_canonical_episode_snapshots(
        [observation],
        candidates=[candidate],
        decision_at=decision,
        signal_quality_enabled=True,
        scan_source="LIVE_FULL_MARKET",
    )[0]


def _wrapper(snapshot: dict | None = None, **overrides) -> dict:
    inner = snapshot if snapshot is not None else _snapshot_payload()
    payload = {
        "schema_version": 1,
        "engine": "OPIP_PAPER_V2",
        "snapshot_id": inner["snapshot_id"],
        "episode_id": inner["episode_id"],
        "cohort_id": inner["cohort_id"],
        "snapshot_hash": canonical_episode_snapshot_hash(inner),
        "snapshot_payload": inner,
    }
    payload.update(overrides)
    return payload


class _Settings:
    opip_paper_v2_mode = "active"
    paper_v2_quote_max_age_seconds = 15
    paper_trade_fee_rate = 0.004
    paper_trade_slippage_bps = 10.0
    paper_v2_tp1_fraction = 0.5
    paper_v2_max_hold_seconds = 86_400


class _CountingTransport:
    """Counts every public book request, so 'no market-data read' is provable."""

    def __init__(self, *, count_ref) -> None:
        self._count_ref = count_ref

    def request(self, endpoint, params, timeout_seconds):
        self._count_ref.append(endpoint)
        return {
            "symbol": "SOL/USD",
            "bids": [{"price": 99.9, "qty": 10.0, "publication_ts": "2026-09-19T11:59:59Z"}],
            "asks": [{"price": 100.1, "qty": 12.0, "publication_ts": "2026-09-19T11:59:59Z"}],
        }

    def telemetry_snapshot(self):
        return {}


def _kraken(calls: list) -> KrakenClient:
    return KrakenClient(transport=_CountingTransport(count_ref=calls))


def _version(**overrides) -> InstrumentVersion:
    fields = {
        "venue": "kraken",
        "base_asset": "SOL",
        "quote_currency": "USD",
        "venue_instrument_id": "SOL/USD",
        "version": 1,
        "reference_data_version": "ref-1",
        "observed_at_utc": NOW - timedelta(minutes=5),
    }
    fields.update(overrides)
    return InstrumentVersion(**fields)


def _opportunity(**overrides) -> PaperV2Opportunity:
    snapshot = overrides.pop("snapshot_payload", None)
    snapshot_at = overrides.pop("snapshot_at", NOW)
    if snapshot is None:
        snapshot = _snapshot_payload(decision=snapshot_at)
    fields = {
        "candidate_id": "OPIPC:candidate-1",
        "episode_id": snapshot["episode_id"],
        "cohort_id": snapshot["cohort_id"],
        "direction": "LONG",
        "instrument_version_id": INSTRUMENT_VERSION_ID,
        "snapshot_payload": snapshot,
        "evaluation_time": snapshot_at,
        "evidence_cutoff": snapshot_at,
        "source_record_refs": ("source:1",),
        "qualification_policy_version": GATE_POLICY_VERSION,
        "qualification_policy_fingerprint": gate_policy_fingerprint(),
        "instrument_version": _version(),
        "quote_currency": "USD",
        "requested_capital": 500.0,
        "requested_reservation_amount": 500.0,
        "decision_time": snapshot_at,
        "native_symbol": "SOL/USD",
        "requested_quantity": 5.0,
        "requested_notional": 500.0,
        "stop_price": 90.0,
        "target_prices": (110.0, 120.0),
    }
    fields.update(overrides)
    return PaperV2Opportunity(**fields)


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


def _rows(writer, event_type: str) -> list[dict]:
    rows = writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
        "SELECT payload_json FROM events WHERE event_type = ? ORDER BY local_sequence",
        (event_type,),
    ).fetchall()
    return [json.loads(str(row["payload_json"])) for row in rows]


def _event_ids(writer, event_type: str) -> list[str]:
    rows = writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
        "SELECT event_id FROM events WHERE event_type = ? ORDER BY local_sequence",
        (event_type,),
    ).fetchall()
    return [str(row["event_id"]) for row in rows]


def _total_event_count(writer) -> int:
    return int(
        writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
            "SELECT COUNT(*) FROM events"
        ).fetchone()[0]
    )


# ---------------------------------------------------------------------------
# 1. The canonical decision-snapshot contract
# ---------------------------------------------------------------------------


def test_snapshot_contract_pins_the_production_record_type():
    """The contract's accepted inner record type is the builder's own."""
    assert DECISION_SNAPSHOT_EPISODE_RECORD_TYPE == RECORD_TYPE
    assert DECISION_SNAPSHOT_EPISODE_SCHEMA_VERSION == 1


def test_valid_snapshot_wrapper_validates():
    payload = _wrapper()
    normalized = validate_decision_snapshot_payload(payload)
    assert normalized["snapshot_id"] == payload["snapshot_id"]
    assert normalized["snapshot_hash"].startswith(f"{EPISODE_SNAPSHOT_HASH_PREFIX}:")


def test_wrapper_round_trips_as_json_within_the_writer_payload_limit():
    """The real payload must fit the existing canonical payload limit."""
    from app.opip.canonical.writer import MAX_PAYLOAD_BYTES

    wrapper = _wrapper()
    provenance = {
        "producing_component": "paper_v2_execution",
        "artifact_or_build_id": "ACF:" + "a" * 64,
        "process_instance_id": "PROC:12345678-1234-1234-1234-123456789012",
        "emitted_at": "2026-09-19T12:00:00Z",
        "source_record_refs": ["EVT:1", "EVT:2"],
        "schema_version": 1,
    }
    size = len(
        json.dumps({**wrapper, "provenance": provenance}, separators=(",", ":")).encode()
    )
    assert size < MAX_PAYLOAD_BYTES, f"{size} must be below {MAX_PAYLOAD_BYTES}"


def test_snapshot_commits_canonically(env):
    server, client = env
    snapshot = DecisionSnapshot.from_payload(_snapshot_payload())
    proof = commit_decision_snapshot(snapshot, client=client)
    assert proof.status == "OK"
    assert proof.event_id
    rows = _rows(server.writer, PAPER_DECISION_SNAPSHOT_RECORDED)
    assert len(rows) == 1
    assert rows[0]["snapshot_id"] == snapshot.snapshot_id


def test_snapshot_intent_uses_the_snapshot_identity_key(env):
    snapshot = DecisionSnapshot.from_payload(_snapshot_payload())
    intent = decision_snapshot_intent(build_decision_snapshot_payload(snapshot))
    assert intent.priority == "LOW"
    assert intent.ops_handoff is None
    assert intent.idempotency_key == f"{PAPER_DECISION_SNAPSHOT_RECORDED}:{snapshot.snapshot_id}"
    assert intent.idempotency_key == decision_snapshot_idempotency_key(
        build_decision_snapshot_payload(snapshot)
    )


def test_exact_snapshot_retry_is_duplicate_ok(env):
    server, client = env
    snapshot = DecisionSnapshot.from_payload(_snapshot_payload())
    first = commit_decision_snapshot(snapshot, client=client)
    second = commit_decision_snapshot(snapshot, client=client)
    assert first.status == "OK"
    assert second.status == "DUPLICATE_OK"
    assert second.event_id == first.event_id
    assert len(_rows(server.writer, PAPER_DECISION_SNAPSHOT_RECORDED)) == 1


def test_changed_content_under_one_snapshot_identity_is_refused(env):
    """A snapshot record is immutable once written, keyed by identity not content."""
    server, client = env
    original = _snapshot_payload()
    submit_decision_snapshot(_wrapper(original), client=client)

    mutated = {**original, "reference_price": 999.0}
    divergent = _wrapper(mutated, snapshot_hash=canonical_episode_snapshot_hash(mutated))
    # The divergent payload is internally valid - content hash matches - but it
    # reuses a committed snapshot identity, so the same-key conflict must bite.
    assert divergent["snapshot_id"] == original["snapshot_id"]
    ack = client.submit(decision_snapshot_intent(divergent))
    assert ack.status == "REJECTED"
    assert ack.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"
    assert len(_rows(server.writer, PAPER_DECISION_SNAPSHOT_RECORDED)) == 1


@pytest.mark.parametrize("substituted", ["SNAP", "none", ""])
def test_snapshot_identity_cannot_be_substituted_for_the_content_hash(substituted):
    snapshot = _snapshot_payload()
    if substituted == "SNAP":
        wrong = snapshot["snapshot_id"]
    elif substituted == "none":
        wrong = "deadbeef" * 4
    else:
        wrong = ""
    with pytest.raises(ValueError, match="snapshot_hash|content hash"):
        validate_decision_snapshot_payload(_wrapper(snapshot, snapshot_hash=wrong))


def test_fabricated_content_hash_is_refused():
    snapshot = _snapshot_payload()
    fake = f"{EPISODE_SNAPSHOT_HASH_PREFIX}:" + "b" * 32
    with pytest.raises(ValueError, match="content hash"):
        validate_decision_snapshot_payload(_wrapper(snapshot, snapshot_hash=fake))


@pytest.mark.parametrize("field_name", ["snapshot_id", "episode_id", "cohort_id"])
def test_wrapper_inner_identity_mismatch_is_refused(field_name):
    with pytest.raises(ValueError, match=f"wrapper {field_name} must equal"):
        validate_decision_snapshot_payload(_wrapper(**{field_name: "SNAP:mismatch"}))


def test_wrapper_rejects_an_unknown_inner_record_type():
    snapshot = {**_snapshot_payload(), "record_type": "SOMETHING_ELSE"}
    with pytest.raises(ValueError, match="record_type"):
        validate_decision_snapshot_payload(_wrapper(snapshot))


@pytest.mark.parametrize("bad", [None, "not-a-mapping", 42, ["a"], ("a",)])
def test_non_mapping_nested_payload_is_refused(bad):
    with pytest.raises(ValueError, match="snapshot_payload must be"):
        validate_decision_snapshot_payload(_wrapper(snapshot_payload=bad))


def test_unexpected_or_missing_wrapper_fields_are_refused():
    payload = _wrapper()
    with pytest.raises(ValueError, match="invalid decision snapshot fields"):
        validate_decision_snapshot_payload({**payload, "extra": 1})
    trimmed = dict(payload)
    del trimmed["snapshot_hash"]
    with pytest.raises(ValueError, match="invalid decision snapshot fields"):
        validate_decision_snapshot_payload(trimmed)


def test_decision_snapshot_object_refuses_an_inconsistent_declared_hash():
    snapshot = _snapshot_payload()
    with pytest.raises(ValueError, match="content hash"):
        DecisionSnapshot(
            snapshot_payload=snapshot,
            snapshot_id=snapshot["snapshot_id"],
            episode_id=snapshot["episode_id"],
            cohort_id=snapshot["cohort_id"],
            snapshot_hash=f"{EPISODE_SNAPSHOT_HASH_PREFIX}:" + "c" * 32,
        )


def test_snapshot_from_payload_binds_the_decision_subject():
    snapshot = _snapshot_payload()
    with pytest.raises(ValueError, match="episode does not match"):
        DecisionSnapshot.from_payload(snapshot, expected_episode_id="EP:other")
    with pytest.raises(ValueError, match="cohort does not match"):
        DecisionSnapshot.from_payload(snapshot, expected_cohort_id="COHORT:other")


# ---------------------------------------------------------------------------
# 2. Snapshot proof is bound into the committed DecisionContext
# ---------------------------------------------------------------------------


def test_context_is_not_written_when_snapshot_commit_is_not_proven(env):
    """A snapshot that is not durably proven blocks the context entirely."""
    server, client = env

    class _RejectingSnapshot:
        def __getattr__(self, name):
            return getattr(client, name)

        def submit(self, intent):
            if intent.event_type == PAPER_DECISION_SNAPSHOT_RECORDED:
                return WriterAck(status="REJECTED", error_code="INVALID_INTENT")
            return client.submit(intent)

    with pytest.raises(PaperV2ExecutionError, match="decision snapshot failed"):
        run_paper_v2_opportunity(
            _opportunity(),
            client=_RejectingSnapshot(),
            kraken_client=_kraken([]),
            settings=_Settings(),
            now=NOW,
        )
    # No context is written. Instrument registration legitimately precedes the
    # snapshot in the required sequence, so it is the only permitted record.
    assert _rows(server.writer, CTX_EVENT) == []
    assert _total_event_count(server.writer) == 1
    assert _event_ids(server.writer, PAPER_DECISION_SNAPSHOT_RECORDED) == []


def test_committed_context_uses_the_snapshot_that_was_durably_recorded(env):
    server, _ = env
    result = run_paper_v2_opportunity(
        _opportunity(),
        client=env[1],
        kraken_client=_kraken([]),
        settings=_Settings(),
        now=NOW,
    )
    assert result.status == "EXECUTED"

    recorded = _rows(server.writer, PAPER_DECISION_SNAPSHOT_RECORDED)
    assert len(recorded) == 1
    contexts = _rows(server.writer, CTX_EVENT)
    assert len(contexts) == 1
    assert contexts[0]["snapshot_id"] == recorded[0]["snapshot_id"]
    assert contexts[0]["snapshot_hash"] == recorded[0]["snapshot_hash"]


def test_context_provenance_cites_the_real_snapshot_and_registration_records(env):
    server, _ = env
    run_paper_v2_opportunity(
        _opportunity(),
        client=env[1],
        kraken_client=_kraken([]),
        settings=_Settings(),
        now=NOW,
    )
    context = _rows(server.writer, CTX_EVENT)[0]
    refs = context["provenance"]["source_record_refs"]

    snapshot_event_ids = _event_ids(server.writer, PAPER_DECISION_SNAPSHOT_RECORDED)
    assert len(snapshot_event_ids) == 1
    assert snapshot_event_ids[0] in refs

    # The instrument-registration proof is also a real durable record.
    from app.opip.contracts.events import MARKET_INSTRUMENT_VERSION_RECORDED

    registration_ids = _event_ids(
        server.writer, MARKET_INSTRUMENT_VERSION_RECORDED
    )
    assert registration_ids, "expected a committed instrument-registration record"
    for event_id in registration_ids:
        assert event_id in refs

    # The upstream ref is preserved, and the proofs lead deterministically.
    assert "source:1" in refs
    assert refs.index(snapshot_event_ids[0]) < refs.index("source:1")


def test_context_provenance_deduplicates_refs_deterministically(env):
    """A caller ref equal to a proof is dropped, not duplicated."""
    server, _ = env
    snapshot = _snapshot_payload()
    run_paper_v2_opportunity(
        _opportunity(snapshot_payload=snapshot, source_record_refs=("source:1", "source:1")),
        client=env[1],
        kraken_client=_kraken([]),
        settings=_Settings(),
        now=NOW,
    )
    refs = _rows(server.writer, CTX_EVENT)[0]["provenance"]["source_record_refs"]
    assert len(refs) == len(set(refs))
    assert refs == sorted(refs, key=refs.index)


def test_empty_snapshot_proof_is_refused_before_the_context_is_built(env):
    """An empty proof cannot be smuggled in to satisfy the signature."""
    server, client = env
    from app.opip.canonical.decision_context_bridge import (
        DecisionContextFacts,
        build_decision_context_payload,
    )

    facts = DecisionContextFacts(
        candidate_id="c",
        episode_id="EP:1",
        instrument_version_id=INSTRUMENT_VERSION_ID,
        instrument_registration_event_id="EVT:registry",
        snapshot_record_event_id="   ",
        snapshot_id="SNAP:1",
        snapshot_hash="PSNAP:" + "d" * 32,
        evaluation_time=NOW,
        evidence_cutoff=NOW,
        policy_version=GATE_POLICY_VERSION,
        policy_fingerprint=gate_policy_fingerprint(),
        producing_component="paper_v2_execution",
        artifact_or_build_id="ACF:" + "a" * 64,
        process_instance_id="PROC:1",
        emitted_at=NOW,
        source_record_refs=("source:1",),
    )
    with pytest.raises(ValueError, match="snapshot_record_event_id"):
        build_decision_context_payload(facts)
    assert _rows(server.writer, CTX_EVENT) == []


# ---------------------------------------------------------------------------
# 3. Decision-time qualification policy identity
# ---------------------------------------------------------------------------


def test_qualification_policy_fingerprint_survives_a_live_policy_change(env, monkeypatch):
    """The context records the policy that qualified it, not the live one."""
    server, _ = env
    captured_version = "OPIP-GATE-POLICY-CAPTURED-A"
    captured_fingerprint = "GPF:" + "a" * 16
    live_version = "OPIP-GATE-POLICY-LIVE-B"
    live_fingerprint = "GPF:" + "b" * 16

    # The live policy changes between qualification and execution.
    import app.opip.decision.versioning as versioning

    monkeypatch.setattr(versioning, "GATE_POLICY_VERSION", live_version)
    monkeypatch.setattr(
        versioning, "gate_policy_fingerprint", lambda: live_fingerprint
    )

    run_paper_v2_opportunity(
        _opportunity(
            qualification_policy_version=captured_version,
            qualification_policy_fingerprint=captured_fingerprint,
        ),
        client=env[1],
        kraken_client=_kraken([]),
        settings=_Settings(),
        now=NOW,
    )
    context = _rows(server.writer, CTX_EVENT)[0]
    assert context["policy_version"] == captured_version
    assert context["policy_fingerprint"] == captured_fingerprint
    # Emphatically not the live values.
    assert context["policy_version"] != live_version
    assert context["policy_fingerprint"] != live_fingerprint


def test_producer_does_not_read_the_live_policy_at_all():
    """Static proof: the producer module owns no live-policy lookup."""
    source = (APP_ROOT / "services" / "paper_v2_execution.py").read_text(
        encoding="utf-8"
    )
    assert "gate_policy_fingerprint(" not in source
    assert "GATE_POLICY_VERSION" not in source


# ---------------------------------------------------------------------------
# 4. Truthful build and process provenance
# ---------------------------------------------------------------------------


def test_build_identity_is_the_shared_app_code_fingerprint(env):
    from app.opip.decision.versioning import app_code_fingerprint

    server, _ = env
    run_paper_v2_opportunity(
        _opportunity(),
        client=env[1],
        kraken_client=_kraken([]),
        settings=_Settings(),
        now=NOW,
    )
    context = _rows(server.writer, CTX_EVENT)[0]
    assert context["provenance"]["artifact_or_build_id"] == app_code_fingerprint()


def test_build_identity_is_producer_owned_not_caller_supplied():
    """A caller has no field to override the build identity with."""
    import dataclasses

    names = {field.name for field in dataclasses.fields(PaperV2Opportunity)}
    assert "artifact_or_build_id" not in names
    assert "process_instance_id" not in names


def test_process_instance_id_is_stable_within_a_process():
    first = process_instance_id()
    second = process_instance_id()
    assert first == second
    assert first
    assert first.startswith(f"{PROCESS_INSTANCE_PREFIX}:")


def test_process_instance_id_survives_a_module_reload_in_the_same_process():
    """A reload is not a restart, so it must not mint a second identity.

    A module global would be rebound by ``reload``, handing one process two
    identities. The sentinel is process-scoped instead, keyed by PID.
    """
    import app.opip.contracts.process_identity as module

    first = process_instance_id()
    reloaded = importlib.reload(module)
    try:
        assert reloaded.process_instance_id() == first
    finally:
        importlib.reload(module)


def test_process_instance_id_differs_for_a_real_new_process(tmp_path):
    """A genuinely different process receives a different identifier."""
    import subprocess

    here = process_instance_id()
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.opip.contracts.process_identity import process_instance_id;"
            " print(process_instance_id())",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    child_id = child.stdout.strip()
    assert child_id.startswith(f"{PROCESS_INSTANCE_PREFIX}:")
    assert child_id != here


def test_forked_child_does_not_inherit_the_parent_identity(tmp_path):
    """A forked child inherits memory but not the PID, so it mints its own."""
    import os
    import subprocess

    here = process_instance_id()
    script = (
        "import os, sys\n"
        "from app.opip.contracts.process_identity import process_instance_id\n"
        "if os.fork() == 0:\n"
        "    print(process_instance_id())\n"
        "    os._exit(0)\n"
        "os.wait()\n"
    )
    if not hasattr(os, "fork"):
        pytest.skip("fork is unavailable on this platform")
    child = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    if child.returncode != 0 or not child.stdout.strip():
        pytest.skip("fork unavailable in this environment")
    assert child.stdout.strip() != here


def test_process_instance_id_never_enters_semantic_identity(env):
    """The emitter identity cannot change what a record means."""
    server, _ = env
    run_paper_v2_opportunity(
        _opportunity(),
        client=env[1],
        kraken_client=_kraken([]),
        settings=_Settings(),
        now=NOW,
    )
    context = _rows(server.writer, CTX_EVENT)[0]
    emitter_id = context["provenance"]["process_instance_id"]
    assert emitter_id.startswith(f"{PROCESS_INSTANCE_PREFIX}:")

    from app.opip.decision_intelligence.events import context_identity_v2

    base = {key: value for key, value in context.items() if key != "context_id"}
    other = {
        **base,
        "provenance": {**base["provenance"], "process_instance_id": "PROC:other"},
    }
    # Two processes emitting the same decision produce the same semantic identity.
    assert context_identity_v2(base) == context_identity_v2(other)
    assert emitter_id not in context["context_id"]


def test_committed_context_provenance_carries_a_proc_domain_process_id(env):
    server, _ = env
    run_paper_v2_opportunity(
        _opportunity(),
        client=env[1],
        kraken_client=_kraken([]),
        settings=_Settings(),
        now=NOW,
    )
    context = _rows(server.writer, CTX_EVENT)[0]
    assert context["provenance"]["process_instance_id"].startswith(
        f"{PROCESS_INSTANCE_PREFIX}:"
    )


# ---------------------------------------------------------------------------
# 5. Producer-level activation and direction defense
# ---------------------------------------------------------------------------


class _InactiveSettings(_Settings):
    opip_paper_v2_mode = "off"


@pytest.mark.parametrize("mode", ["off", "OFF", "", "shadow", "unexpected"])
def test_inactive_paper_v2_prevents_every_write_and_read(env, mode):
    server, _ = env
    calls: list[str] = []

    class _Mode:
        opip_paper_v2_mode = mode

    with pytest.raises(PaperV2ExecutionError, match="not active"):
        run_paper_v2_opportunity(
            _opportunity(),
            client=env[1],
            kraken_client=_kraken(calls),
            settings=_Mode(),
            now=NOW,
        )
    assert calls == [], "no public book read may happen when Paper v2 is inactive"
    assert _total_event_count(server.writer) == 0


@pytest.mark.parametrize("direction", ["SHORT", "short", "FLAT", "", "LONG_ISH", None])
def test_unsupported_direction_is_refused_before_any_write_or_read(env, direction):
    server, _ = env
    calls: list[str] = []
    with pytest.raises(PaperV2ExecutionError, match="unsupported opportunity direction"):
        run_paper_v2_opportunity(
            _opportunity(direction=direction),
            client=env[1],
            kraken_client=_kraken(calls),
            settings=_Settings(),
            now=NOW,
        )
    assert calls == [], "no public book read may happen for an unsupported direction"
    assert _total_event_count(server.writer) == 0


def test_short_is_never_silently_mapped_onto_a_buy(env):
    """A SHORT produces no ENTRY intent rather than a BUY."""
    server, _ = env
    with pytest.raises(PaperV2ExecutionError):
        run_paper_v2_opportunity(
            _opportunity(direction="SHORT"),
            client=env[1],
            kraken_client=_kraken([]),
            settings=_Settings(),
            now=NOW,
        )
    assert _rows(server.writer, "paper_execution.order_intent.recorded") == []


def test_only_long_is_a_supported_direction():
    from app.services.paper_v2_execution import SUPPORTED_OPPORTUNITY_DIRECTIONS

    assert SUPPORTED_OPPORTUNITY_DIRECTIONS == {"LONG"}


def test_active_long_execution_still_completes_the_canonical_lifecycle(env):
    """Control: the gates do not disturb the existing B/C-3 lifecycle."""
    from app.opip.contracts.paper_execution_events import (
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        PAPER_FILL_RECORDED,
        PAPER_ORDER_INTENT_RECORDED,
        PAPER_PROTECTION_PLAN_RECORDED,
    )

    server, _ = env
    result = run_paper_v2_opportunity(
        _opportunity(),
        client=env[1],
        kraken_client=_kraken([]),
        settings=_Settings(),
        now=NOW,
    )
    assert result.status == "EXECUTED"
    assert result.fill_id and result.protection_plan_id and result.entry_order_intent_id
    writer = server.writer
    assert len(_rows(writer, PAPER_DECISION_SNAPSHOT_RECORDED)) == 1
    assert len(_rows(writer, CTX_EVENT)) == 1
    assert len(_rows(writer, PAPER_ORDER_INTENT_RECORDED)) == 1
    assert len(_rows(writer, PAPER_EXECUTION_ATTEMPT_RECORDED)) == 1
    assert len(_rows(writer, PAPER_FILL_RECORDED)) == 1
    assert len(_rows(writer, PAPER_PROTECTION_PLAN_RECORDED)) == 1


def test_snapshot_is_committed_before_the_context(env):
    server, _ = env
    run_paper_v2_opportunity(
        _opportunity(),
        client=env[1],
        kraken_client=_kraken([]),
        settings=_Settings(),
        now=NOW,
    )
    snapshot_seq = server.writer._conn.execute(  # noqa: SLF001
        "SELECT MIN(local_sequence) FROM events WHERE event_type = ?",
        (PAPER_DECISION_SNAPSHOT_RECORDED,),
    ).fetchone()[0]
    context_seq = server.writer._conn.execute(  # noqa: SLF001
        "SELECT MIN(local_sequence) FROM events WHERE event_type = ?",
        (CTX_EVENT,),
    ).fetchone()[0]
    assert snapshot_seq < context_seq


def test_producer_restart_is_idempotent_across_a_restart(tmp_path):
    """A restart replays the same snapshot and context rather than duplicating."""
    opportunity = _opportunity()
    db_path = tmp_path / "canonical.sqlite3"
    first_server = CanonicalWriterServer(
        db_path=db_path, socket_path=tmp_path / "a.sock"
    )
    try:
        run_paper_v2_opportunity(
            opportunity,
            client=InProcessWriterClient(first_server),
            kraken_client=_kraken([]),
            settings=_Settings(),
            now=NOW,
        )
    finally:
        first_server.stop()

    second_server = CanonicalWriterServer(
        db_path=db_path, socket_path=tmp_path / "b.sock"
    )
    try:
        run_paper_v2_opportunity(
            opportunity,
            client=InProcessWriterClient(second_server),
            kraken_client=_kraken([]),
            settings=_Settings(),
            now=NOW,
        )
        assert len(_rows(second_server.writer, PAPER_DECISION_SNAPSHOT_RECORDED)) == 1
        assert len(_rows(second_server.writer, CTX_EVENT)) == 1
    finally:
        second_server.stop()


# ---------------------------------------------------------------------------
# Finding 1: the snapshot proof must prove a real canonical episode snapshot
# ---------------------------------------------------------------------------


def _minimal_fabricated_inner() -> dict:
    """Only the fields the wrapper compares - no production facts at all."""
    real = _snapshot_payload()
    return {
        "record_type": CANONICAL_EPISODE_SNAPSHOT_RECORD_TYPE,
        "schema_version": 1,
        "snapshot_id": real["snapshot_id"],
        "episode_id": real["episode_id"],
        "cohort_id": real["cohort_id"],
    }


def test_minimal_fabricated_inner_payload_is_rejected_even_with_correct_hash():
    """A mapping that merely claims the record type is not an episode snapshot.

    The wrapper's identity and content-hash checks are satisfied exactly here, so
    only real shape validation can refuse it.
    """
    inner = _minimal_fabricated_inner()
    # The PSNAP hash is internally correct for this payload.
    wrapper = _wrapper(inner, snapshot_hash=episode_snapshot_hash(inner))
    assert wrapper["snapshot_id"] == inner["snapshot_id"]
    assert wrapper["snapshot_hash"] == episode_snapshot_hash(inner)

    with pytest.raises(ValueError, match="invalid canonical episode snapshot fields"):
        validate_decision_snapshot_payload(wrapper)
    with pytest.raises(ValueError, match="invalid canonical episode snapshot fields"):
        DecisionSnapshot.from_payload(inner)


def test_missing_real_production_field_is_rejected():
    inner = {key: value for key, value in _snapshot_payload().items()}
    del inner["ml_feature_seed"]
    with pytest.raises(ValueError, match="invalid canonical episode snapshot fields"):
        validate_decision_snapshot_payload(
            _wrapper(inner, snapshot_hash=episode_snapshot_hash(inner))
        )


def test_extra_inner_field_is_rejected():
    inner = {**_snapshot_payload(), "unexpected_field": 1}
    with pytest.raises(ValueError, match="invalid canonical episode snapshot fields"):
        validate_decision_snapshot_payload(
            _wrapper(inner, snapshot_hash=episode_snapshot_hash(inner))
        )


def test_bogus_episode_identity_is_rejected():
    inner = {**_snapshot_payload(), "episode_id": "EP:" + "0" * 24}
    with pytest.raises(ValueError, match="episode_id must be the canonical episode"):
        validate_decision_snapshot_payload(
            _wrapper(
                inner,
                episode_id=inner["episode_id"],
                snapshot_hash=episode_snapshot_hash(inner),
            )
        )


def test_bogus_snapshot_identity_is_rejected():
    inner = {**_snapshot_payload(), "snapshot_id": "SNAP:" + "0" * 32}
    with pytest.raises(ValueError, match="snapshot_id must be the canonical snapshot"):
        validate_decision_snapshot_payload(
            _wrapper(
                inner,
                snapshot_id=inner["snapshot_id"],
                snapshot_hash=episode_snapshot_hash(inner),
            )
        )


def test_real_builder_output_still_validates_unchanged():
    inner = _snapshot_payload()
    validated = validate_canonical_episode_snapshot(inner)
    assert validated == inner
    # And the builder's own identities are the contract's identities.
    assert inner["episode_id"] == canonical_episode_id(
        schema_version=inner["schema_version"],
        cohort_id=inner["cohort_id"],
        symbol=inner["symbol"],
    )
    assert inner["snapshot_id"] == canonical_snapshot_id(
        schema_version=inner["schema_version"], episode_id=inner["episode_id"]
    )


def test_producer_and_canonical_validator_share_one_snapshot_contract():
    """Neither layer may carry a second, drifting definition of the shape."""
    from app.services import canonical_episode_capture as producer
    from app.opip.contracts import paper_execution_runtime as canonical

    assert producer.RECORD_TYPE == CANONICAL_EPISODE_SNAPSHOT_RECORD_TYPE
    assert producer.SCHEMA_VERSION == CANONICAL_EPISODE_SNAPSHOT_SCHEMA_VERSION
    assert (
        canonical.DECISION_SNAPSHOT_EPISODE_RECORD_TYPE
        == CANONICAL_EPISODE_SNAPSHOT_RECORD_TYPE
    )
    assert (
        canonical.DECISION_SNAPSHOT_EPISODE_SCHEMA_VERSION
        == CANONICAL_EPISODE_SNAPSHOT_SCHEMA_VERSION
    )
    # The producer routes identity through the shared primitives.
    assert producer._canonical_episode_identity is canonical_episode_id
    assert producer._canonical_snapshot_identity is canonical_snapshot_id


def test_producer_self_validates_so_it_cannot_emit_a_non_canonical_record():
    source = (
        APP_ROOT / "services" / "canonical_episode_capture.py"
    ).read_text(encoding="utf-8")
    assert "return validate_canonical_episode_snapshot(payload)" in source


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("measurement_only", False),
        ("advisory_only", False),
        ("affects_ranking", True),
        ("affects_telegram", True),
        ("affects_pending_setup", True),
        ("trade_authority_changed", True),
        ("production_execution_gate_changed", True),
    ],
)
def test_authority_flags_must_carry_their_production_meaning(field_name, value):
    inner = {**_snapshot_payload(), field_name: value}
    with pytest.raises(ValueError, match=field_name):
        validate_canonical_episode_snapshot(inner)


def test_non_kraken_source_exchange_is_rejected():
    inner = {**_snapshot_payload(), "source_exchange": "OTHER"}
    with pytest.raises(ValueError, match="source_exchange"):
        validate_canonical_episode_snapshot(inner)


@pytest.mark.parametrize("value", ["not-a-timestamp", "", "2026-09-19T12:00:00", 42])
def test_decision_at_utc_must_be_an_aware_timestamp(value):
    inner = {**_snapshot_payload(), "decision_at_utc": value}
    with pytest.raises(ValueError, match="decision_at_utc"):
        validate_canonical_episode_snapshot(inner)


def test_non_finite_content_is_rejected():
    inner = {**_snapshot_payload(), "components": {"bad": float("inf")}}
    with pytest.raises(ValueError, match="not canonically serializable"):
        validate_canonical_episode_snapshot(inner)


@pytest.mark.parametrize("field_name", ["cohort_id", "episode_id", "snapshot_id", "symbol"])
def test_non_canonical_identity_strings_are_rejected(field_name):
    inner = {**_snapshot_payload(), field_name: "  padded  "}
    with pytest.raises(ValueError, match=field_name):
        validate_canonical_episode_snapshot(inner)


# ---------------------------------------------------------------------------
# Finding 2: the snapshot decision boundary is the context evidence cutoff
# ---------------------------------------------------------------------------


def test_equivalent_decision_time_representations_are_accepted(env):
    """A 'Z' suffix and an offset denote the same instant, so both bind."""
    server, _ = env
    payload = _snapshot_payload()
    payload = {**payload, "decision_at_utc": "2026-09-19T12:00:00+00:00"}
    run_paper_v2_opportunity(
        _opportunity(snapshot_payload=payload, evidence_cutoff=NOW),
        client=env[1],
        kraken_client=_kraken([]),
        settings=_Settings(),
        now=NOW,
    )
    assert len(_rows(server.writer, PAPER_DECISION_SNAPSHOT_RECORDED)) == 1
    assert len(_rows(server.writer, CTX_EVENT)) == 1


def test_offset_representation_of_the_same_instant_is_accepted(env):
    server, _ = env
    payload = {**_snapshot_payload(), "decision_at_utc": "2026-09-19T14:00:00+02:00"}
    run_paper_v2_opportunity(
        _opportunity(snapshot_payload=payload, evidence_cutoff=NOW),
        client=env[1],
        kraken_client=_kraken([]),
        settings=_Settings(),
        now=NOW,
    )
    assert len(_rows(server.writer, CTX_EVENT)) == 1


def test_snapshot_boundary_mismatch_is_rejected_before_every_write_and_read(env):
    """A snapshot at T1 with evidence_cutoff T2 cannot become lineage."""
    server, _ = env
    calls: list[str] = []
    snapshot_at = NOW - timedelta(minutes=5)
    payload = _snapshot_payload(decision=snapshot_at)
    with pytest.raises(
        PaperV2ExecutionError, match="boundary does not match the evidence cutoff"
    ):
        run_paper_v2_opportunity(
            _opportunity(snapshot_payload=payload, evidence_cutoff=NOW),
            client=env[1],
            kraken_client=_kraken(calls),
            settings=_Settings(),
            now=NOW,
        )
    assert calls == [], "no public book read may happen before the boundary check"
    assert _total_event_count(server.writer) == 0, (
        "no canonical write may happen before the boundary check"
    )
    assert _rows(server.writer, PAPER_DECISION_SNAPSHOT_RECORDED) == []
    assert _event_ids(server.writer, "market.instrument_version.recorded") == []


def test_evaluation_time_may_be_later_than_the_evidence_cutoff(env):
    """The cutoff is the closed boundary; evaluation may follow it."""
    server, _ = env
    payload = _snapshot_payload()
    run_paper_v2_opportunity(
        _opportunity(
            snapshot_payload=payload,
            evidence_cutoff=NOW,
            evaluation_time=NOW + timedelta(seconds=30),
        ),
        client=env[1],
        kraken_client=_kraken([]),
        settings=_Settings(),
        now=NOW,
    )
    context = _rows(server.writer, CTX_EVENT)[0]
    assert context["evidence_cutoff"] == "2026-09-19T12:00:00Z"
    assert context["evaluation_time"] == "2026-09-19T12:00:30Z"


def test_no_snapshot_expiration_window_is_introduced():
    """Freshness stays the quote-evidence responsibility, captured at execution.

    The decision snapshot is historical evidence: the contract binds it to the
    decision instant and nothing else. No age or expiry concept may appear on the
    snapshot path, and the boundary check must be an instant comparison rather
    than a window.
    """
    adapter = (APP_ROOT / "services" / "paper_v2_decision_snapshot.py").read_text(
        encoding="utf-8"
    )
    for token in ("max_age", "expiry", "expires", "stale", "snapshot_age", "timedelta"):
        assert token not in adapter, token

    producer = (APP_ROOT / "services" / "paper_v2_execution.py").read_text(
        encoding="utf-8"
    )
    # The boundary check compares the two instants directly.
    assert "decision_snapshot.decision_at != cutoff" in producer
    # The quote-evidence freshness bound is untouched and still present.
    assert "paper_v2_quote_max_age_seconds" in producer


# ---------------------------------------------------------------------------
# Finding 4: the WriterIntent vocabulary knows the snapshot event
# ---------------------------------------------------------------------------


def test_writer_intent_vocabulary_includes_the_decision_snapshot_event():
    import typing

    from app.opip.canonical.models import EventType

    vocabulary = set(typing.get_args(EventType))
    assert PAPER_DECISION_SNAPSHOT_RECORDED in vocabulary
    # The rest of the Paper v2 vocabulary is unchanged by this correction.
    for existing in (
        "paper_execution.quote_evidence.recorded",
        "paper_execution.order_intent.recorded",
        "paper_execution.attempt.recorded",
        "paper_execution.fill.recorded",
        "paper_protection.plan.recorded",
        "paper_protection.state.recorded",
        "paper_protection.trigger.recorded",
        "paper_execution.reconciliation.recorded",
    ):
        assert existing in vocabulary, existing


def test_writer_intent_round_trips_the_decision_snapshot_event_through_uds():
    """The event survives canonical frame serialization byte-for-byte."""
    import socket

    from app.opip.canonical.models import WriterIntent
    from app.opip.canonical.protocol import recv_json, send_json

    snapshot = DecisionSnapshot.from_payload(_snapshot_payload())
    intent = decision_snapshot_intent(build_decision_snapshot_payload(snapshot))
    assert intent.event_type == PAPER_DECISION_SNAPSHOT_RECORDED

    client_sock, server_sock = socket.socketpair()
    try:
        send_json(client_sock, intent.to_dict())
        received = recv_json(server_sock)
    finally:
        client_sock.close()
        server_sock.close()

    assert received["event_type"] == PAPER_DECISION_SNAPSHOT_RECORDED
    restored = WriterIntent.from_dict(received)
    assert restored.event_type == PAPER_DECISION_SNAPSHOT_RECORDED
    assert restored == intent
    assert restored.payload["snapshot_id"] == snapshot.snapshot_id


def test_decision_snapshot_event_is_registered_in_the_runtime_accepted_set():
    from app.opip.canonical.writer import ACCEPTED_EVENT_TYPES
    from app.opip.contracts.paper_execution_runtime import PAPER_V2_WRITER_EVENT_TYPES

    assert PAPER_DECISION_SNAPSHOT_RECORDED in PAPER_V2_WRITER_EVENT_TYPES
    assert PAPER_DECISION_SNAPSHOT_RECORDED in ACCEPTED_EVENT_TYPES


# ---------------------------------------------------------------------------
# 6. Exclusively-out-of-scope surfaces remain untouched
# ---------------------------------------------------------------------------


def test_scan_opportunities_has_no_paper_v2_execution_caller():
    """The scan orchestrator delegates; it never calls the producer directly.

    Increment 6B wires the router seam, so the scan does reference the Paper-v2
    route. What must remain true is that it stays thin: it does not call the
    execution producer itself, and it does not import the decision-intelligence
    plane. Those are the invariants this test now guards.
    """
    source = (APP_ROOT / "jobs" / "scan_opportunities.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.ImportFrom) and node.module:
            referenced.add(node.module)
            referenced.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            referenced.update(alias.name for alias in node.names)
    # The producer is reached only through the router.
    assert "run_paper_v2_opportunity" not in referenced
    assert "paper_v2_execution" not in referenced
    assert "paper_v2_decision_snapshot" not in referenced
    # And the runtime import boundary is unchanged.
    assert "app.opip.decision_intelligence" not in referenced


def test_paper_v2_mode_defaults_off():
    from app.core.config import Settings

    assert Settings.model_fields["opip_paper_v2_mode"].default == "off"


def test_p1_shadow_outbox_remains_disabled_and_retired():
    from app.services.p1_shadow_outbox import p1_shadow_outbox_enabled

    assert p1_shadow_outbox_enabled({}) is False
    assert p1_shadow_outbox_enabled({"P1_SHADOW_OUTBOX_ENABLED": "false"}) is False
    assert p1_shadow_outbox_enabled({"P1_SHADOW_OUTBOX_ENABLED": "0"}) is False

    compose = (REPO_ROOT.parent / "docker-compose.yml")
    if compose.exists():
        text = compose.read_text(encoding="utf-8")
        assert "P1_SHADOW_OUTBOX_ENABLED=true" not in text
    # Paper v2 does not revive the retired snapshot JSONL outbox: the decision
    # snapshot is canonical-writer evidence, not a JSONL append.
    source = (APP_ROOT / "services" / "paper_v2_decision_snapshot.py").read_text(
        encoding="utf-8"
    )
    assert "p1_shadow_outbox" not in source
    assert "append_canonical_episode_snapshots" not in source
    assert "_append_jsonl" not in source


def test_feature_bus_remains_independent_and_off():
    from app.core.config import Settings

    assert Settings.model_fields["opip_feature_bus_mode"].default == "off"
    # The decision-snapshot path touches neither the feature bus nor its events.
    source = (APP_ROOT / "services" / "paper_v2_decision_snapshot.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    assert not any("feature" in name for name in modules), modules
    assert "feature.snapshot.recorded" not in source


def test_decision_intelligence_import_boundary_is_unchanged():
    """Runtime roots still must not import the DI plane directly."""
    runtime_roots = (
        APP_ROOT / "services",
        APP_ROOT / "jobs",
        APP_ROOT / "api",
        APP_ROOT / "opip" / "discovery",
        APP_ROOT / "opip" / "decision",
        APP_ROOT / "opip" / "risk",
    )
    di_root = "app.opip.decision_intelligence"
    offenders: list[str] = []
    for root in runtime_roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name == di_root or name.startswith(di_root + "."):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{name}")
    assert offenders == []


def test_snapshot_adapter_holds_no_execution_or_exchange_authority():
    path = APP_ROOT / "services" / "paper_v2_decision_snapshot.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr.lower())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            referenced.add(node.name.lower())
    for token in (
        "place_order",
        "create_order",
        "submit_order",
        "kraken_private",
        "admit_paper",
        "risk_decision",
    ):
        assert not any(token in name for name in referenced), token


def test_episode_snapshot_hash_has_a_single_implementation():
    """The service entry point and the contracts primitive agree exactly."""
    snapshot = _snapshot_payload()
    assert canonical_episode_snapshot_hash(snapshot) == episode_snapshot_hash(snapshot)
    assert canonical_episode_snapshot_hash(snapshot) == episode_snapshot_hash(
        json.loads(json.dumps(snapshot, sort_keys=True))
    )
    # An independent recomputation of the documented rule confirms the domain.
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
    expected = "PSNAP:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:32]
    assert episode_snapshot_hash(snapshot) == expected
