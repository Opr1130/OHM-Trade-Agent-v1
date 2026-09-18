"""Paper registry genesis: virgin proof, refusal semantics, upgrade path.

The defect these tests exist for is a *migration boundary*, not a code path:
PR #245 made ``paper_trading/state.json`` a mandatory canonical learning-replica
completeness companion, but gave an already-running installation with zero paper
lifecycle activity no way to declare an initialized-empty registry. The replica
contract then correctly failed closed forever
(``CANONICAL_REPLICA_PAPER_STATE_MISSING``).

Every test here models the real production upgrade condition rather than a
green-field fixture, and every refusal direction is asserted fail-closed:

* virgin state must be *proven*, never assumed from the absence of state.json;
* evidence that exists (events, canonical outcomes, unresolved gaps) must block
  genesis, so an empty registry can never be manufactured over real activity;
* a registry that disappears after initialization must never be recreated, which
  is what the durable marker is for;
* the replica contract itself is never weakened - ``require_paper_state`` stays
  True, so genesis is the only way the export can succeed.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.paper_outcome import (
    PAPER_OUTCOME_PRIORITY,
    PAPER_OUTCOME_TERMINAL_RECORDED,
    build_terminal_outcome_payload,
    terminal_outcome_idempotency_key,
)
from app.opip.learning.canonical_replica import (
    REASON_PAPER_STATE_MISSING,
    ReplicaUnavailableError,
    export_replica_bundle,
)
from app.services.paper_registry_genesis import (
    BASIS_ADOPTED,
    BASIS_GENESIS,
    GENESIS_ADOPTED_EXISTING,
    GENESIS_ALREADY_INITIALIZED,
    GENESIS_INITIALIZED,
    GENESIS_MARKER_KIND,
    GENESIS_MARKER_VERSION,
    GENESIS_REFUSED,
    REASON_EVIDENCE_EXISTS,
    REASON_MARKER_CORRUPT,
    REASON_MARKER_WRITE_FAILED,
    REASON_NOT_PROVABLE,
    REASON_STATE_CORRUPT,
    REASON_STATE_LOST,
    REASON_STATE_PRESENT_VALID,
    ensure_paper_registry_initialized,
)

ROOT = Path(__file__).resolve().parents[1]
RELEASE_SHA = "d50aebbd19df2201bb617298254407add830d851"
NOW = datetime(2026, 9, 18, 3, 0, tzinfo=timezone.utc)
ENTER = datetime(2026, 9, 18, 2, 0, tzinfo=timezone.utc)
EXIT = datetime(2026, 9, 18, 2, 30, tzinfo=timezone.utc)

#: The authoritative empty registry payload. Spelled out once here because it is
#: the contract: an initialized-but-empty registry must be indistinguishable from
#: a registry that has simply never held a lifecycle.
EMPTY_REGISTRY = {"schema_version": 1, "paper_only": True, "lifecycles": {}}


def _virgin(tmp_path: Path, *, canonical: bool = True) -> dict[str, Path]:
    """A production shape immediately before PR #245: no registry, no activity."""
    data = tmp_path / "data"
    paths = {
        "state_file": data / "paper_trading" / "state.json",
        "event_file": data / "paper_trading" / "events.jsonl",
        "gap_spool_file": data / "paper_trading" / "evidence_gap_spool.json",
        "canonical_db": data / "opip" / "canonical" / "opip_canonical_v1.sqlite3",
        "marker_path": tmp_path / "host" / "paper-registry-initialized-v1",
    }
    if canonical:
        paths["canonical_db"].parent.mkdir(parents=True, exist_ok=True)
        CanonicalWriter(paths["canonical_db"]).close()
    return paths


def _ensure(paths: dict[str, Path], **overrides):
    kwargs = {**paths, **overrides}
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("release_sha", RELEASE_SHA)
    return ensure_paper_registry_initialized(**kwargs)


def _commit_terminal_outcome(db: Path) -> dict:
    """Commit one real terminal paper outcome to the canonical store."""
    from app.opip.canonical.writer import CanonicalWriter as Writer
    from app.opip.canonical.writer import WriterIntent

    payload = build_terminal_outcome_payload(
        engine="OHM_PAPER_SIM_V1",
        paper_trade_id="PAPER:" + "a" * 20,
        episode_id="EP:1",
        cohort_id="COH:1",
        strategy_version="OPIP-STRATEGY-V1",
        exchange="KRAKEN",
        native_symbol="BTCUSD",
        base_asset="BTC",
        direction="LONG",
        quote_currency="USD",
        terminal_status="CLOSED",
        exit_reason="STOP",
        exit_price=98.0,
        entry_timestamp=ENTER,
        exit_timestamp=EXIT,
        capital_committed=1000.0,
        gross_pnl=-25.0,
        fees_paid=4.0,
        net_pnl=-29.0,
        net_pnl_pct=-2.9,
        final_revision=7,
        terminal_event_id="PTE:" + "b" * 24,
        candidate_id="CAND:1",
        decision_context_id="DI-CONTEXT:" + "c" * 32,
    )
    writer = Writer(db)
    try:
        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority=PAPER_OUTCOME_PRIORITY,
                idempotency_key=terminal_outcome_idempotency_key(payload["outcome_id"]),
                event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
                payload=payload,
            )
        )
        assert ack.status == "OK", ack.detail
    finally:
        writer.close()
    return payload


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "app.services.paper_registry_genesis", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Group 1 - genesis semantics
# ---------------------------------------------------------------------------


def test_existing_valid_state_is_never_rewritten(tmp_path):
    """Case 1: a valid registry is a NO-OP, and provenance is still recorded."""
    paths = _virgin(tmp_path)
    paths["state_file"].parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(
        {"schema_version": 1, "paper_only": True, "lifecycles": {"PAPER:x": {"status": "CLOSED"}}},
        indent=2,
    )
    paths["state_file"].write_text(original, encoding="utf-8")
    before = paths["state_file"].read_bytes()
    before_mtime = paths["state_file"].stat().st_mtime_ns

    outcome = _ensure(paths)

    assert outcome.status == GENESIS_ADOPTED_EXISTING
    assert outcome.reason == REASON_STATE_PRESENT_VALID
    # Not rewritten, not reformatted, not re-saved.
    assert paths["state_file"].read_bytes() == before
    assert paths["state_file"].stat().st_mtime_ns == before_mtime
    # Provenance is recorded so a later disappearance cannot read as virginity.
    marker = json.loads(paths["marker_path"].read_text(encoding="utf-8"))
    assert marker["kind"] == GENESIS_MARKER_KIND
    assert marker["scope" if "scope" in marker else "basis"] == BASIS_ADOPTED


def test_missing_state_with_provable_virginity_is_initialized(tmp_path):
    """Cases 2-4: initialize only when virginity is proven, durably."""
    paths = _virgin(tmp_path)

    outcome = _ensure(paths)

    assert outcome.status == GENESIS_INITIALIZED
    assert outcome.refused is False
    # Case 3: the resulting registry is exactly the authoritative empty payload.
    assert json.loads(paths["state_file"].read_text(encoding="utf-8")) == EMPTY_REGISTRY
    # Case 4: the marker is durable and self-describing.
    marker = json.loads(paths["marker_path"].read_text(encoding="utf-8"))
    assert marker["kind"] == GENESIS_MARKER_KIND
    assert marker["schema_version"] == GENESIS_MARKER_VERSION
    assert marker["basis"] == BASIS_GENESIS
    assert marker["release_sha"] == RELEASE_SHA
    assert marker["paper_only"] is True
    assert marker["recorded_at_utc"].endswith("Z")
    # No secrets anywhere in the marker.
    assert not any(
        key.lower() in {"token", "key", "secret", "password"} for key in marker
    )


def test_repeat_genesis_is_idempotent(tmp_path):
    """Case 5: repeated genesis cannot corrupt state and never re-saves it."""
    paths = _virgin(tmp_path)
    first = _ensure(paths)
    assert first.status == GENESIS_INITIALIZED
    state_bytes = paths["state_file"].read_bytes()
    marker_bytes = paths["marker_path"].read_bytes()

    second = _ensure(paths)
    third = _ensure(paths)

    for outcome in (second, third):
        assert outcome.status == GENESIS_ALREADY_INITIALIZED
        assert outcome.reason == REASON_STATE_PRESENT_VALID
    assert paths["state_file"].read_bytes() == state_bytes
    assert paths["marker_path"].read_bytes() == marker_bytes
    assert json.loads(paths["state_file"].read_text(encoding="utf-8")) == EMPTY_REGISTRY


def test_concurrent_genesis_is_safe_and_deterministic(tmp_path):
    """Case 6: concurrent attempts cannot create conflicting state."""
    paths = _virgin(tmp_path)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: _ensure(paths), range(4)))

    assert all(not outcome.refused for outcome in results)
    # Exactly one attempt may report that it created the registry content.
    assert sum(o.status == GENESIS_INITIALIZED for o in results) >= 1
    # Whatever the interleaving, the durable end state is the empty contract.
    assert json.loads(paths["state_file"].read_text(encoding="utf-8")) == EMPTY_REGISTRY
    marker = json.loads(paths["marker_path"].read_text(encoding="utf-8"))
    assert marker["kind"] == GENESIS_MARKER_KIND


def test_marker_present_but_state_missing_fails_closed(tmp_path):
    """Case 7: a lost registry is an incident, never recreated as empty."""
    paths = _virgin(tmp_path)
    assert _ensure(paths).status == GENESIS_INITIALIZED
    paths["state_file"].unlink()

    outcome = _ensure(paths)

    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == REASON_STATE_LOST
    # The critical property: no empty registry is manufactured.
    assert not paths["state_file"].exists()


def test_historical_paper_event_blocks_genesis(tmp_path):
    """Case 8: a durable lifecycle event proves activity, so refuse."""
    paths = _virgin(tmp_path)
    paths["event_file"].parent.mkdir(parents=True, exist_ok=True)
    paths["event_file"].write_text(
        json.dumps({"event_type": "CREATED", "paper_trade_id": "PAPER:x"}) + "\n",
        encoding="utf-8",
    )

    outcome = _ensure(paths)

    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == REASON_EVIDENCE_EXISTS
    assert outcome.facts["paper_event_rows"] == 1
    assert not paths["state_file"].exists()


def test_canonical_terminal_outcome_blocks_genesis(tmp_path):
    """Case 9: canonical economic truth proves activity, so refuse."""
    paths = _virgin(tmp_path)
    payload = _commit_terminal_outcome(paths["canonical_db"])

    outcome = _ensure(paths)

    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == REASON_EVIDENCE_EXISTS
    assert outcome.facts["canonical_terminal_outcomes"] == 1
    assert outcome.facts["canonical_stream_present"] is True
    assert not paths["state_file"].exists()
    assert payload["outcome_id"]


def test_unresolved_paper_gap_blocks_genesis(tmp_path):
    """Case 10: an unresolved evidence gap makes completeness unprovable."""
    paths = _virgin(tmp_path)
    paths["gap_spool_file"].parent.mkdir(parents=True, exist_ok=True)
    paths["gap_spool_file"].write_text(
        json.dumps({"unresolved": [{"gap_id": "GAP:1"}], "updated_at": None}),
        encoding="utf-8",
    )

    outcome = _ensure(paths)

    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == REASON_EVIDENCE_EXISTS
    assert outcome.facts["unresolved_gap_rows"] == 1
    assert not paths["state_file"].exists()


def test_corrupt_state_is_reported_and_not_overwritten(tmp_path):
    """Case 11: a corrupt registry is refused, never replaced or quarantined."""
    paths = _virgin(tmp_path)
    paths["state_file"].parent.mkdir(parents=True, exist_ok=True)
    paths["state_file"].write_text("{not json", encoding="utf-8")
    before = paths["state_file"].read_bytes()

    outcome = _ensure(paths)

    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == REASON_STATE_CORRUPT
    # Byte-identical: genesis must not rewrite, replace or quarantine evidence.
    assert paths["state_file"].read_bytes() == before
    leftovers = [p.name for p in paths["state_file"].parent.iterdir()]
    assert leftovers == [paths["state_file"].name]


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param("[]", id="not-an-object"),
        pytest.param('{"schema_version": 1}', id="no-lifecycles"),
        pytest.param('{"lifecycles": []}', id="lifecycles-not-object"),
    ],
)
def test_structurally_invalid_state_is_refused(tmp_path, corrupt):
    """Case 11 variant: structural invalidity fails closed too."""
    paths = _virgin(tmp_path)
    paths["state_file"].parent.mkdir(parents=True, exist_ok=True)
    paths["state_file"].write_text(corrupt, encoding="utf-8")

    outcome = _ensure(paths)

    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == REASON_STATE_CORRUPT
    assert paths["state_file"].read_text(encoding="utf-8") == corrupt


def test_unprovable_canonical_store_fails_closed(tmp_path):
    """Case 12: an absent authoritative store is unprovable, not virgin."""
    paths = _virgin(tmp_path, canonical=False)

    outcome = _ensure(paths)

    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == REASON_NOT_PROVABLE
    assert "canonical outcome store is absent" in outcome.detail
    assert not paths["state_file"].exists()


def test_corrupt_gap_spool_fails_closed(tmp_path):
    """Case 12 variant: a corrupt spool is unprovable, not empty."""
    paths = _virgin(tmp_path)
    paths["gap_spool_file"].parent.mkdir(parents=True, exist_ok=True)
    paths["gap_spool_file"].write_text("{not json", encoding="utf-8")

    outcome = _ensure(paths)

    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == REASON_NOT_PROVABLE
    assert "gap spool" in outcome.detail
    assert not paths["state_file"].exists()


def test_unparseable_event_row_fails_closed(tmp_path):
    """Case 12 variant: a partial event line is activity, not absence."""
    paths = _virgin(tmp_path)
    paths["event_file"].parent.mkdir(parents=True, exist_ok=True)
    paths["event_file"].write_text('{"event_type": "CREATED"', encoding="utf-8")

    outcome = _ensure(paths)

    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == REASON_NOT_PROVABLE
    assert not paths["state_file"].exists()


def test_naive_now_is_rejected(tmp_path):
    """Provenance timestamps are UTC; a naive datetime is not localized."""
    paths = _virgin(tmp_path)
    with pytest.raises(ValueError):
        _ensure(paths, now=datetime(2026, 9, 18, 3, 0))


# ---------------------------------------------------------------------------
# Durable initialization provenance is a gate, not a side effect
# ---------------------------------------------------------------------------


def test_marker_write_failure_refuses_despite_valid_state(tmp_path, monkeypatch):
    """A registry without durable provenance must never be reported as success.

    The dangerous sequence this closes: state.json is created, the marker write
    fails, genesis reports failure - but state.json now exists, so the exporter can
    succeed and the export can verify. Without gating on provenance the deployment
    could report SUCCESS with no durable record that the registry was ever
    initialized, and a later disappearance of state.json would again be
    indistinguishable from virgin state.
    """
    from app.services import paper_registry_genesis as genesis

    paths = _virgin(tmp_path)

    def _boom(*_args, **_kwargs):
        raise OSError("injected marker persistence failure")

    monkeypatch.setattr(genesis, "save_json_atomic", _boom)
    outcome = _ensure(paths)

    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == genesis.REASON_MARKER_WRITE_FAILED
    assert outcome.refused is True
    # The registry itself did get created - that is exactly why provenance must be
    # gated separately rather than inferred from the registry's existence.
    assert paths["state_file"].exists()
    assert json.loads(paths["state_file"].read_text(encoding="utf-8")) == EMPTY_REGISTRY
    # ...and no marker exists, so provenance is genuinely absent.
    assert not paths["marker_path"].exists()


def test_next_attempt_recovers_by_adopting_and_writing_the_marker(tmp_path, monkeypatch):
    """Recovery: the refused attempt leaves a valid registry, so retry adopts it."""
    from app.services import paper_registry_genesis as genesis

    paths = _virgin(tmp_path)

    def _boom(*_args, **_kwargs):
        raise OSError("injected marker persistence failure")

    monkeypatch.setattr(genesis, "save_json_atomic", _boom)
    assert _ensure(paths).refused is True
    assert paths["state_file"].exists()
    assert not paths["marker_path"].exists()

    # Restore persistence: the retry must adopt the existing registry and record
    # provenance, not fail and not overwrite the registry.
    monkeypatch.undo()
    state_before = paths["state_file"].read_bytes()
    recovered = _ensure(paths)

    assert recovered.status == GENESIS_ADOPTED_EXISTING
    assert recovered.refused is False
    assert paths["marker_path"].exists()
    marker = json.loads(paths["marker_path"].read_text(encoding="utf-8"))
    assert marker["kind"] == GENESIS_MARKER_KIND
    assert marker["basis"] == BASIS_ADOPTED
    # The registry was adopted, not recreated.
    assert paths["state_file"].read_bytes() == state_before


def test_valid_marker_is_never_rewritten(tmp_path):
    """Repeat genesis preserves the original marker bytes and provenance.

    Rewriting a valid marker would silently restamp initialized_at_utc and release
    provenance on every deploy, which is provenance drift rather than idempotency.
    """
    paths = _virgin(tmp_path)
    first = _ensure(paths)
    assert first.status == GENESIS_INITIALIZED
    marker_bytes = paths["marker_path"].read_bytes()
    marker_before = json.loads(marker_bytes.decode("utf-8"))
    mtime_before = paths["marker_path"].stat().st_mtime_ns
    # A LATER timestamp must not leak into the preserved marker.
    later = datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc)

    second = _ensure(paths, now=later)
    third = _ensure(paths, now=later)

    for outcome in (second, third):
        assert outcome.status == GENESIS_ALREADY_INITIALIZED
        assert outcome.refused is False
        assert outcome.facts["marker_preserved"] is True
    assert paths["marker_path"].read_bytes() == marker_bytes
    assert paths["marker_path"].stat().st_mtime_ns == mtime_before
    assert json.loads(paths["marker_path"].read_text(encoding="utf-8")) == marker_before


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param("{not json", id="not-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param('{"kind": "something_else"}', id="wrong-kind"),
        pytest.param(
            '{"kind": "%s", "schema_version": 99}' % GENESIS_MARKER_KIND,
            id="unsupported-version",
        ),
        pytest.param(
            '{"kind": "%s", "schema_version": 1, "paper_only": true}' % GENESIS_MARKER_KIND,
            id="missing-recorded-at",
        ),
        pytest.param(
            '{"kind": "%s", "schema_version": 1, "paper_only": false,'
            ' "recorded_at_utc": "2026-09-18T03:00:00Z"}' % GENESIS_MARKER_KIND,
            id="not-paper-only",
        ),
    ],
)
def test_corrupt_marker_with_valid_state_fails_closed(tmp_path, corrupt):
    """Unreadable provenance is refused, never silently overwritten."""
    paths = _virgin(tmp_path)
    assert _ensure(paths).status == GENESIS_INITIALIZED
    paths["marker_path"].write_text(corrupt, encoding="utf-8")
    before = paths["marker_path"].read_bytes()

    outcome = _ensure(paths)

    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == REASON_MARKER_CORRUPT
    assert outcome.facts["marker_state"] == "MALFORMED"
    # Not repaired, not replaced: corrupt provenance stays visible.
    assert paths["marker_path"].read_bytes() == before
    # And the registry is untouched.
    assert json.loads(paths["state_file"].read_text(encoding="utf-8")) == EMPTY_REGISTRY


def test_corrupt_marker_with_missing_state_still_fails_closed(tmp_path):
    """A marker of any kind proves initialization, so a missing registry refuses."""
    paths = _virgin(tmp_path)
    assert _ensure(paths).status == GENESIS_INITIALIZED
    paths["marker_path"].write_text("{not json", encoding="utf-8")
    paths["state_file"].unlink()

    outcome = _ensure(paths)

    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == REASON_STATE_LOST
    assert not paths["state_file"].exists()


def test_marker_state_and_provenance_facts_are_reported(tmp_path):
    """The deploy gate depends on these facts, so they must be explicit."""
    paths = _virgin(tmp_path)
    created = _ensure(paths)
    assert created.facts["marker_state"] == "VALID"
    assert created.facts["marker_written"] is True
    assert created.facts["marker_preserved"] is False

    preserved = _ensure(paths)
    assert preserved.facts["marker_state"] == "VALID"
    assert preserved.facts["marker_preserved"] is True


def test_genesis_cli_exit_code_is_nonzero_on_marker_failure(tmp_path, monkeypatch):
    """The CLI contract: a provenance failure must not exit 0.

    ohm-deploy derives its structured genesis status from this exit code, so a
    zero exit here would defeat the deployment gate.
    """
    from app.services import paper_registry_genesis as genesis

    paths = _virgin(tmp_path)

    def _boom(*_args, **_kwargs):
        raise OSError("injected marker persistence failure")

    monkeypatch.setattr(genesis, "save_json_atomic", _boom)
    outcome = genesis.ensure_paper_registry_initialized(
        state_file=paths["state_file"],
        event_file=paths["event_file"],
        gap_spool_file=paths["gap_spool_file"],
        canonical_db=paths["canonical_db"],
        marker_path=paths["marker_path"],
        release_sha=RELEASE_SHA,
        now=NOW,
    )
    assert outcome.refused is True
    assert outcome.reason == genesis.REASON_MARKER_WRITE_FAILED


def test_evidence_committed_after_the_precheck_still_blocks_genesis(tmp_path, monkeypatch):
    """The proof must be re-run UNDER the state lock, not only before it.

    Regression cover: the virginity evidence was checked once before acquiring
    the lock and never rechecked, so durable evidence committed between the check
    and the write could be overwritten by an empty registry - which would then
    falsely certify completeness. The in-lock recheck is what prevents that, so
    this proves the second call actually gates creation.
    """
    from app.services import paper_registry_genesis as genesis

    calls = {"n": 0}
    real_prove = genesis._prove_virgin

    def prove_with_late_evidence(**kwargs):
        calls["n"] += 1
        proof = real_prove(**kwargs)
        if calls["n"] == 1:
            # First (pre-lock) pass sees a clean tree.
            return proof
        # Second (in-lock) pass must observe evidence that "arrived" meanwhile.
        if not proof.virgin:
            return proof
        return genesis._VirginProof(
            virgin=False,
            reason=genesis.REASON_EVIDENCE_EXISTS,
            detail="paper event committed during genesis",
            facts={**proof.facts, "paper_event_rows": 1},
        )

    monkeypatch.setattr(genesis, "_prove_virgin", prove_with_late_evidence)

    paths = _virgin(tmp_path)
    outcome = _ensure(paths)

    assert calls["n"] >= 2, "the proof must be consulted again inside the lock"
    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == REASON_EVIDENCE_EXISTS
    assert not paths["state_file"].exists()
    assert not paths["marker_path"].exists()


def test_evidence_present_before_the_lock_is_refused_without_creating(tmp_path):
    """The fast path refuses before taking the lock, and writes nothing."""
    paths = _virgin(tmp_path)
    paths["event_file"].parent.mkdir(parents=True, exist_ok=True)
    paths["event_file"].write_text('{"event_type":"CREATED"}\n', encoding="utf-8")
    outcome = _ensure(paths)
    assert outcome.status == GENESIS_REFUSED
    assert not paths["marker_path"].exists()


def test_genesis_cli_reports_structured_status(tmp_path):
    """The CLI is the deploy's contract, so it must be machine-readable."""
    paths = _virgin(tmp_path)
    result = _run_cli(
        "ensure",
        "--state-file",
        str(paths["state_file"]),
        "--event-file",
        str(paths["event_file"]),
        "--gap-spool",
        str(paths["gap_spool_file"]),
        "--canonical-db",
        str(paths["canonical_db"]),
        "--marker",
        str(paths["marker_path"]),
        "--release-sha",
        RELEASE_SHA,
    )
    assert result.returncode == 0, result.stderr
    assert "OPIP_PAPER_REGISTRY_GENESIS=INITIALIZED" in result.stdout
    assert "OPIP_PAPER_REGISTRY_GENESIS_REASON=PAPER_REGISTRY_GENESIS_INITIALIZED" in result.stdout
    assert json.loads(paths["state_file"].read_text(encoding="utf-8")) == EMPTY_REGISTRY

    # A refusal must be non-zero AND labelled, so the deploy cannot mistake it
    # for success.
    paths2 = _virgin(tmp_path / "second")
    paths2["event_file"].parent.mkdir(parents=True, exist_ok=True)
    paths2["event_file"].write_text('{"event_type":"CREATED"}\n', encoding="utf-8")
    refused = _run_cli(
        "ensure",
        "--state-file",
        str(paths2["state_file"]),
        "--event-file",
        str(paths2["event_file"]),
        "--gap-spool",
        str(paths2["gap_spool_file"]),
        "--canonical-db",
        str(paths2["canonical_db"]),
        "--marker",
        str(paths2["marker_path"]),
        "--release-sha",
        RELEASE_SHA,
    )
    assert refused.returncode == 4
    assert "OPIP_PAPER_REGISTRY_GENESIS=REFUSED" in refused.stdout
    assert "PAPER_REGISTRY_GENESIS_REFUSED_EVIDENCE_EXISTS" in refused.stdout


# ---------------------------------------------------------------------------
# Group 2 - the real upgrade path
# ---------------------------------------------------------------------------


def _run_export_cli(*, paths: dict[str, Path], staging: Path) -> subprocess.CompletedProcess:
    """Invoke the exact CLI command the exporter shell script runs.

    Paths are passed explicitly rather than derived, so the helper cannot drift
    from the production layout (an earlier version derived them and silently
    pointed at ``data/opip/paper_trading`` instead of ``data/paper_trading``).
    """
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "app.opip.learning.canonical_replica",
            "export",
            "--source-db",
            str(paths["canonical_db"]),
            "--staging",
            str(staging),
            "--release-sha",
            RELEASE_SHA,
            # The exporter shell always passes these; `require_paper_state`
            # intentionally stays at its True default.
            "--paper-state",
            str(paths["state_file"]),
            "--paper-gap",
            str(paths["gap_spool_file"]),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_real_upgrade_path_from_pre_245_production_shape(tmp_path):
    """Group 2: the real genesis + the real export contract produce a valid bundle.

    The system starts exactly as it was immediately before PR #245: an
    initialized canonical store, zero terminal paper outcomes, no paper lifecycle
    event stream, no gap spool, and NO registry. Before genesis the export must
    fail closed; after genesis it must produce a complete canonical replica.
    """
    paths = _virgin(tmp_path)

    # Pre-genesis: the real export contract refuses, exactly as production did.
    with pytest.raises(ReplicaUnavailableError) as exc:
        export_replica_bundle(
            source_db=paths["canonical_db"],
            staging_dir=tmp_path / "staging",
            source_release_sha=RELEASE_SHA,
            paper_state_source=paths["state_file"],
            paper_gap_source=paths["gap_spool_file"],
            backup_work_dir=tmp_path / "work1",
            now=NOW,
        )
    assert exc.value.reason == REASON_PAPER_STATE_MISSING

    # The approved migration.
    assert _ensure(paths).status == GENESIS_INITIALIZED

    # Post-genesis: the same real contract now produces a complete bundle.
    staging = tmp_path / "staging2"
    manifest = export_replica_bundle(
        source_db=paths["canonical_db"],
        staging_dir=staging,
        source_release_sha=RELEASE_SHA,
        paper_state_source=paths["state_file"],
        paper_gap_source=paths["gap_spool_file"],
        backup_work_dir=tmp_path / "work2",
        now=NOW,
    )

    assert manifest["replica_schema_version"] == 1
    assert manifest["source_release_sha"] == RELEASE_SHA
    assert manifest["canonical_db"]["present"] is True
    # The bundled completeness companion is the real empty registry.
    assert manifest["paper_state"]["present"] is True
    bundled = json.loads((staging / "paper_trading" / "state.json").read_text("utf-8"))
    assert bundled == EMPTY_REGISTRY
    # The gap spool was legitimately absent. The bundle still carries a
    # certified-empty spool (always a readable contract) and the manifest
    # records the production source absence separately, keeping "absent" and
    # "lost during export" distinguishable.
    assert manifest["paper_gap_spool"]["present"] is True
    assert manifest["paper_gap_source_present"] is False
    certified_empty = json.loads(
        (staging / "paper_trading" / "evidence_gap_spool.json").read_text("utf-8")
    )
    assert certified_empty["unresolved"] == []


@pytest.mark.skipif(
    os.name == "nt",
    reason=(
        "POSIX-only: the exporter's canonical snapshot uses the PR-A0 "
        "authoritative directory-durability primitive, which correctly refuses "
        "on Windows. Run via the Linux CI root/CLI path."
    ),
)
def test_real_upgrade_path_through_the_exporter_cli(tmp_path):
    """Group 2 (CLI boundary): the exact command the exporter shell invokes.

    Proves the shell-facing CLI contract end to end, which the in-process test
    above cannot: the exporter runs
    ``python -m app.opip.learning.canonical_replica export`` with
    ``require_paper_state`` left at its True default.
    """
    paths = _virgin(tmp_path)

    before = _run_export_cli(paths=paths, staging=tmp_path / "s1")
    assert before.returncode == 78, before.stderr
    assert "CANONICAL_REPLICA_PAPER_STATE_MISSING" in before.stderr

    assert _ensure(paths).status == GENESIS_INITIALIZED

    # Guard the helper itself. A path-arithmetic bug here previously pointed the
    # CLI at ``data/opip/paper_trading`` instead of ``data/paper_trading`` and
    # surfaced as a confusing refusal rather than a clear fixture failure.
    assert paths["state_file"].is_file(), paths["state_file"]
    assert paths["state_file"] == tmp_path / "data" / "paper_trading" / "state.json"

    after = _run_export_cli(paths=paths, staging=tmp_path / "s2")
    assert after.returncode == 0, after.stderr
    assert "O'Pip canonical replica export: OK" in after.stdout
    assert json.loads(tmp_path.joinpath("s2", "paper_trading", "state.json").read_text("utf-8")) == EMPTY_REGISTRY


def test_export_contract_is_not_weakened(tmp_path):
    """Genesis is the only path to a bundle; the contract itself is untouched."""
    import inspect

    signature = inspect.signature(export_replica_bundle)
    assert signature.parameters["require_paper_state"].default is True
    paths = _virgin(tmp_path)
    with pytest.raises(ReplicaUnavailableError) as exc:
        export_replica_bundle(
            source_db=paths["canonical_db"],
            staging_dir=tmp_path / "staging",
            source_release_sha=RELEASE_SHA,
            paper_state_source=paths["state_file"],
            paper_gap_source=None,
            now=NOW,
        )
    assert exc.value.reason == REASON_PAPER_STATE_MISSING


# ---------------------------------------------------------------------------
# Group 3 - evidence-loss negative upgrade
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "seed",
    [
        pytest.param("event", id="paper-lifecycle-event"),
        pytest.param("gap", id="unresolved-gap"),
        pytest.param("outcome", id="canonical-terminal-outcome"),
    ],
)
def test_evidence_loss_upgrade_refuses_and_stays_fail_closed(tmp_path, seed):
    """Group 3: evidence exists but the registry is gone -> refuse, stay closed."""
    paths = _virgin(tmp_path)
    if seed == "event":
        paths["event_file"].parent.mkdir(parents=True, exist_ok=True)
        paths["event_file"].write_text('{"event_type":"CREATED"}\n', encoding="utf-8")
    elif seed == "gap":
        paths["gap_spool_file"].parent.mkdir(parents=True, exist_ok=True)
        paths["gap_spool_file"].write_text(
            json.dumps({"unresolved": [{"gap_id": "GAP:1"}]}), encoding="utf-8"
        )
    else:
        _commit_terminal_outcome(paths["canonical_db"])

    outcome = _ensure(paths)
    assert outcome.status == GENESIS_REFUSED
    assert outcome.reason == REASON_EVIDENCE_EXISTS
    assert not paths["state_file"].exists()

    # And the export must remain fail-closed: no bundle claiming a complete
    # canonical replica may be produced.
    with pytest.raises(ReplicaUnavailableError) as exc:
        export_replica_bundle(
            source_db=paths["canonical_db"],
            staging_dir=tmp_path / "staging",
            source_release_sha=RELEASE_SHA,
            paper_state_source=paths["state_file"],
            paper_gap_source=paths["gap_spool_file"],
            backup_work_dir=tmp_path / "work",
            now=NOW,
        )
    assert exc.value.reason == REASON_PAPER_STATE_MISSING
    assert not (tmp_path / "staging" / "replica_manifest.json").exists()
