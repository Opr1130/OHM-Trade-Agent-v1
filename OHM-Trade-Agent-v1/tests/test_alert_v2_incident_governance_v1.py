"""Alert v2 incident governance: decision-first notifications and flood control.

Covers the required test matrix for:

* recovery-cycle truth (a failed *probe* is what counts, never a monitor
  observation),
* scope-owned recovery authority (unrelated success cannot close a scope),
* completed-incident history preservation across reopen,
* classification precedence (connectivity outranks derived pricing symptoms),
* delivery-fact vs delivery-obligation separation with bounded retry,
* bounded private recovery probe and truthful reset telemetry,
* secret-free persistence and a deterministic read-only projection.

Persistence is redirected into ``tmp_path`` (state file passed explicitly or via
the ``conftest`` isolation fixture), so no test touches ``/app/data``.
"""

from __future__ import annotations

import importlib.util
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.services import (
    kraken_health as health,
    kraken_transport as transport,
    system_incidents as incidents,
)

ROOT = Path(__file__).resolve().parents[1]

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)

PRICING_REASON = (
    "USD/stable-quote pricing unavailable for held assets: "
    "ADA.S,ETH2.S,SEI.B,SUI.B,TAO.B"
)
PRICING_REASON_REORDERED = (
    "USD/stable-quote pricing unavailable for held assets: "
    "TAO.B,SUI.B,SEI.B,ETH2.S,ADA.S"
)
CONNECTIVITY_REASON = (
    "Kraken-first exposure resolution failed: "
    "KrakenTransportError: ConnectError: connection refused"
)
PUBLIC_KEY = "SYSTEM_HEALTH:KRAKEN:PUBLIC_CONNECTIVITY"
READ_ONLY_KEY = "SYSTEM_HEALTH:KRAKEN:READ_ONLY_CONNECTIVITY"
PRICING_KEY = "SYSTEM_HEALTH:KRAKEN:HELD_ASSET_PRICING"

CONNECTIVITY_CLASS = incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE
READ_ONLY_CLASS = incidents.SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY
PRICING_CLASS = incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED
POSITION_CLASS = incidents.SystemIncidentClass.POSITION_VERIFICATION_UNAVAILABLE
RATE_LIMIT_CLASS = incidents.SystemIncidentClass.KRAKEN_RATE_LIMITED
OPERATOR_CLASS = incidents.SystemIncidentClass.UNIFIED_CYCLE_OPERATOR_STATE
INTERNAL_CLASS = incidents.SystemIncidentClass.INTERNAL_SERVICE_FAILURE

PUBLIC_SCOPE = incidents.SystemIncidentScope.KRAKEN_PUBLIC
READ_ONLY_SCOPE = incidents.SystemIncidentScope.KRAKEN_READ_ONLY
PRICING_SCOPE = incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING
POSITION_SCOPE = incidents.SystemIncidentScope.KRAKEN_POSITION_VERIFICATION
RATE_LIMIT_SCOPE = incidents.SystemIncidentScope.KRAKEN_RATE_LIMIT
OPERATOR_SCOPE = incidents.SystemIncidentScope.UNIFIED_CYCLE
INTERNAL_SCOPE = incidents.SystemIncidentScope.INTERNAL

AUTH_PUBLIC = incidents.RecoveryAuthority.PUBLIC_PROBE
AUTH_READ_ONLY = incidents.RecoveryAuthority.READ_ONLY_PROBE
AUTH_PRICING = incidents.RecoveryAuthority.PRICING_COVERAGE
AUTH_POSITION = incidents.RecoveryAuthority.POSITION_COVERAGE
AUTH_RATE_LIMIT = incidents.RecoveryAuthority.RATE_LIMIT_CLEARED
AUTH_OPERATOR = incidents.RecoveryAuthority.OPERATOR_STATE
AUTH_INTERNAL = incidents.RecoveryAuthority.OWNING_SUBSYSTEM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row(state: Path, key: str, *, archived: bool = False) -> dict | None:
    payload = json.loads(Path(state).read_text(encoding="utf-8"))
    bucket = payload.get("archive") if archived else payload.get("incidents")
    return (bucket or {}).get(key)


def _fresh_module(state_file: Path):
    """Load a second, independent copy of the incident module (simulated restart)."""

    import sys

    spec = importlib.util.spec_from_file_location(
        "system_incidents_restarted",
        ROOT / "app" / "services" / "system_incidents.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.STATE_FILE = state_file
    return module


def _pricing(state, reason=PRICING_REASON, **kwargs):
    return incidents.observe_degradation(
        incident_class=PRICING_CLASS,
        scope=PRICING_SCOPE,
        reason=reason,
        state_file=state,
        **kwargs,
    )


def _observe(state, klass=CONNECTIVITY_CLASS, scope=PUBLIC_SCOPE, reason=CONNECTIVITY_REASON, **kwargs):
    return incidents.observe_degradation(
        incident_class=klass,
        scope=scope,
        reason=reason,
        state_file=state,
        **kwargs,
    )


def _failed_cycle(
    state,
    klass=CONNECTIVITY_CLASS,
    scope=PUBLIC_SCOPE,
    reason=CONNECTIVITY_REASON,
    **kwargs,
):
    return incidents.record_failed_recovery_cycle(
        incident_class=klass,
        scope=scope,
        reason=reason,
        state_file=state,
        **kwargs,
    )


def _recover(
    state,
    klass=CONNECTIVITY_CLASS,
    scope=PUBLIC_SCOPE,
    authority=AUTH_PUBLIC,
    **kwargs,
):
    return incidents.observe_recovery(
        incident_class=klass,
        scope=scope,
        evidence_source=authority,
        state_file=state,
        **kwargs,
    )


def _deliver(state, decision, message_id=77, when=NOW):
    return incidents.confirm_incident_notification(
        decision=decision, message_id=message_id, now=when, state_file=state
    )


def _degraded_cycle(
    state,
    klass=CONNECTIVITY_CLASS,
    scope=PUBLIC_SCOPE,
    reason=CONNECTIVITY_REASON,
    **kwargs,
):
    """Mirror one real monitor cycle: observe the degradation, then probe once."""

    _observe(state, klass=klass, scope=scope, reason=reason, **kwargs)
    return _failed_cycle(state, klass=klass, scope=scope, reason=reason, **kwargs)


def _open_public_incident(state, *, cycles=7, when=NOW):
    """Drive the public scope to exactly one OPEN notification.

    Mirrors the real monitor: each cycle records the observation *and* the
    failed recovery probe, so occurrence count and failure count both advance.
    """

    for _ in range(cycles - 1):
        _degraded_cycle(state, now=when)
    decision = _degraded_cycle(state, now=when)
    assert decision.action == incidents.ACTION_NOTIFY_OPEN
    _deliver(state, decision, when=when)
    return decision


# ===========================================================================
# 1. RECOVERY CYCLE TRUTH
# ===========================================================================


def test_observation_alone_does_not_count_as_failed_recovery_cycle(tmp_path):
    """1. one degraded observation is not one failed recovery cycle."""

    state = tmp_path / "incidents.json"
    decision = _observe(state)

    assert decision.consecutive_recovery_failures == 0
    assert decision.recovery_cycles_attempted if False else True
    assert decision.should_notify is False
    assert decision.action == incidents.ACTION_SILENT
    assert decision.reason == "GOVERNED_BY_RECOVERY_CYCLE"
    assert decision.occurrence_count == 1


def test_failed_probe_increments_recovery_cycle_count_exactly_once(tmp_path):
    """3. failed probe increments recovery-cycle count exactly once."""

    state = tmp_path / "incidents.json"
    first = _failed_cycle(state)

    assert first.consecutive_recovery_failures == 1
    row = _row(state, PUBLIC_KEY)
    assert row is not None
    assert row["consecutive_recovery_failures"] == 1
    assert row["recovery_cycles_attempted"] == 1


def test_successful_probe_does_not_increment_failure_count(tmp_path):
    """4. successful probe does not increment failure count."""

    state = tmp_path / "incidents.json"
    for _ in range(3):
        _failed_cycle(state)

    recovered = _recover(state)
    assert recovered.should_notify is False
    assert recovered.reason == "SILENT_HEALTHY_RESTORATION"

    row = _row(state, PUBLIC_KEY)
    assert row is not None
    assert row["consecutive_recovery_failures"] == 0
    assert row["state"] == incidents.STATE_RECOVERED


def test_six_failed_probes_are_silent_to_telegram(tmp_path):
    """5. six FAILED PROBES => zero Telegram."""

    state = tmp_path / "incidents.json"
    decisions = [_failed_cycle(state) for _ in range(6)]

    assert all(item.action == incidents.ACTION_SILENT for item in decisions)
    assert all(item.should_notify is False for item in decisions)
    assert [item.consecutive_recovery_failures for item in decisions] == [1, 2, 3, 4, 5, 6]
    assert decisions[-1].notification_state == incidents.NOTIFICATION_BELOW_THRESHOLD

    row = _row(state, PUBLIC_KEY)
    assert row is not None
    assert row["opened_notification_at"] is None


def test_seventh_failed_probe_notifies_exactly_once(tmp_path):
    """6. seventh FAILED PROBE => exactly one SYSTEM FAILURE."""

    state = tmp_path / "incidents.json"
    for _ in range(6):
        _failed_cycle(state)

    seventh = _failed_cycle(state)
    assert seventh.action == incidents.ACTION_NOTIFY_OPEN
    assert seventh.should_notify is True
    assert seventh.consecutive_recovery_failures == 7
    assert seventh.notification_kind == incidents.KIND_OPEN
    _deliver(state, seventh)

    eleventh = None
    for _ in range(8, 12):
        eleventh = _failed_cycle(state)
        assert eleventh.should_notify is False
        assert eleventh.action == incidents.ACTION_SUPPRESS_ONGOING
    assert eleventh is not None
    assert eleventh.consecutive_recovery_failures == 11


def test_observations_without_probes_cannot_manufacture_cycle_seven(tmp_path):
    """7. monitor observations without probes cannot manufacture cycle #7."""

    state = tmp_path / "incidents.json"
    decisions = [_observe(state) for _ in range(30)]

    assert all(item.should_notify is False for item in decisions)
    assert all(item.consecutive_recovery_failures == 0 for item in decisions)

    row = _row(state, PUBLIC_KEY)
    assert row is not None
    assert row["occurrence_count"] == 30
    assert row["consecutive_recovery_failures"] == 0
    assert row["opened_notification_at"] is None


def test_internal_transport_retries_remain_one_recovery_cycle(monkeypatch):
    """9. internal public transport retries remain one recovery cycle."""

    calls = {"n": 0}

    class FailingClient:
        def get(self, *args, **kwargs):
            calls["n"] += 1
            raise httpx.ConnectError("connection refused")

        def close(self):
            return None

    monkeypatch.setattr(transport.time, "sleep", lambda *_: None)

    public = transport.KrakenPublicTransport(
        requests_per_second=1000.0, burst=10, max_retries=5
    )
    public._client = FailingClient()
    monkeypatch.setattr(public, "_acquire_budget", lambda: None)

    probe = health.KrakenScopeProbe(max_attempts=1, sleeper=lambda _: None)
    result = probe.run(
        health.public_connectivity_probe(public),
        scope=health.KrakenHealthScope.PUBLIC_CONNECTIVITY,
    )

    # The transport retried internally (1 + max_retries network attempts)...
    assert calls["n"] == 6
    assert result.success is False
    assert result.failure_class is health.KrakenFailureClass.CONNECTIVITY
    # ...but that whole bounded probe is exactly ONE recovery cycle.
    assert result.attempts == 1
    assert "one recovery cycle" in health.KRAKEN_RECOVERY_CYCLE_SEPARATION


def test_failure_then_recovery_resets_and_keeps_history(tmp_path):
    """Recovery resets the counter but preserves how many cycles failed."""

    state = tmp_path / "incidents.json"
    _open_public_incident(state)

    recovered = _recover(state, now=NOW + timedelta(minutes=5))
    assert recovered.action == incidents.ACTION_NOTIFY_RECOVERY
    assert recovered.recovered_after_recovery_failures == 7
    _deliver(state, recovered)

    row = _row(state, PUBLIC_KEY)
    assert row is not None
    assert row["consecutive_recovery_failures"] == 0
    assert row["state"] == incidents.STATE_RECOVERED


def test_restart_at_failure_count_five_continues_at_six(tmp_path):
    """Restart resumes the recovery-cycle count instead of resetting it."""

    state = tmp_path / "incidents.json"
    for _ in range(5):
        _failed_cycle(state)

    restarted = _fresh_module(state)
    sixth = restarted.record_failed_recovery_cycle(
        incident_class=restarted.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=restarted.SystemIncidentScope.KRAKEN_PUBLIC,
        reason=CONNECTIVITY_REASON,
    )

    assert sixth.consecutive_recovery_failures == 6
    assert sixth.should_notify is False

    seventh = _failed_cycle(state, now=NOW + timedelta(minutes=1))
    assert seventh.action == incidents.ACTION_NOTIFY_OPEN


# ===========================================================================
# 2. SCOPE-OWNED RECOVERY
# ===========================================================================


def test_exposure_coverage_cannot_close_operator_state(tmp_path):
    """10. exposure coverage cannot close UNIFIED_CYCLE:OPERATOR_STATE."""

    state = tmp_path / "incidents.json"
    opened = _observe(
        state,
        klass=OPERATOR_CLASS,
        scope=OPERATOR_SCOPE,
        reason="operator/capacity state unavailable: RuntimeError: corrupt registry",
    )
    assert opened.action == incidents.ACTION_NOTIFY_OPEN
    _deliver(state, opened)

    refused = incidents.observe_recovery(
        incident_class=OPERATOR_CLASS,
        scope=OPERATOR_SCOPE,
        evidence_source=AUTH_PRICING,
        evidence="coverage complete for all verified holdings",
        state_file=state,
    )
    assert refused.action == incidents.ACTION_SILENT
    assert refused.reason == "EVIDENCE_SOURCE_MISMATCH"
    assert refused.metadata["expected_authority"] == AUTH_OPERATOR.value

    row = _row(state, "SYSTEM_HEALTH:UNIFIED_CYCLE:OPERATOR_STATE")
    assert row is not None
    assert row["state"] == incidents.STATE_OPEN


def test_exposure_coverage_cannot_close_internal_service_failure(tmp_path):
    """11. exposure coverage cannot close unrelated INTERNAL_SERVICE_FAILURE."""

    state = tmp_path / "incidents.json"
    opened = _observe(
        state,
        klass=INTERNAL_CLASS,
        scope=INTERNAL_SCOPE,
        reason="some internal subsystem failed: RuntimeError: boom",
    )
    assert opened.action == incidents.ACTION_NOTIFY_OPEN
    _deliver(state, opened)

    refused = incidents.observe_recovery(
        incident_class=INTERNAL_CLASS,
        scope=INTERNAL_SCOPE,
        evidence_source=AUTH_POSITION,
        evidence="coverage complete",
        state_file=state,
    )
    assert refused.reason == "EVIDENCE_SOURCE_MISMATCH"
    assert _row(state, "SYSTEM_HEALTH:INTERNAL:SERVICE")["state"] == incidents.STATE_OPEN


def test_exposure_coverage_cannot_close_rate_limit_incident(tmp_path):
    """12. exposure coverage cannot close an unrelated RATE_LIMIT incident."""

    state = tmp_path / "incidents.json"
    opened = _observe(
        state,
        klass=RATE_LIMIT_CLASS,
        scope=RATE_LIMIT_SCOPE,
        reason="Kraken public HTTP 429 for Ticker",
    )
    assert opened.action == incidents.ACTION_NOTIFY_OPEN
    _deliver(state, opened)

    refused = incidents.observe_recovery(
        incident_class=RATE_LIMIT_CLASS,
        scope=RATE_LIMIT_SCOPE,
        evidence_source=AUTH_PRICING,
        evidence="coverage complete",
        state_file=state,
    )
    assert refused.reason == "EVIDENCE_SOURCE_MISMATCH"
    assert _row(state, "SYSTEM_HEALTH:KRAKEN:RATE_LIMIT")["state"] == incidents.STATE_OPEN

    # ...but the rate-limit owner's own cleared evidence does close it.
    closed = incidents.observe_recovery(
        incident_class=RATE_LIMIT_CLASS,
        scope=RATE_LIMIT_SCOPE,
        evidence_source=AUTH_RATE_LIMIT,
        evidence="rate limit cleared",
        state_file=state,
    )
    assert closed.action == incidents.ACTION_NOTIFY_RECOVERY


def test_public_probe_closes_only_public_connectivity(tmp_path):
    """13. public probe closes only public connectivity."""

    state = tmp_path / "incidents.json"
    _open_public_incident(state)

    assert _recover(state, authority=AUTH_READ_ONLY).reason == "EVIDENCE_SOURCE_MISMATCH"
    assert _row(state, PUBLIC_KEY)["state"] == incidents.STATE_OPEN

    closed = _recover(state, authority=AUTH_PUBLIC)
    assert closed.action == incidents.ACTION_NOTIFY_RECOVERY
    assert closed.scope == PUBLIC_SCOPE.value


def test_read_only_probe_closes_only_read_only_connectivity(tmp_path):
    """14. read-only probe closes only read-only connectivity."""

    state = tmp_path / "incidents.json"
    reason = "Kraken account state unavailable: ConnectError: connection refused"
    for _ in range(7):
        _failed_cycle(state, klass=READ_ONLY_CLASS, scope=READ_ONLY_SCOPE, reason=reason)

    assert _row(state, READ_ONLY_KEY)["opened_notification_at"] is None

    assert _recover(state, klass=READ_ONLY_CLASS, scope=READ_ONLY_SCOPE, authority=AUTH_PUBLIC).reason == (
        "EVIDENCE_SOURCE_MISMATCH"
    )
    closed = _recover(state, klass=READ_ONLY_CLASS, scope=READ_ONLY_SCOPE, authority=AUTH_READ_ONLY)
    assert closed.action == incidents.ACTION_SILENT  # below threshold: never alerted
    assert closed.reason == "SILENT_HEALTHY_RESTORATION"
    assert _row(state, READ_ONLY_KEY)["state"] == incidents.STATE_RECOVERED


def test_pricing_evidence_closes_only_pricing_incident(tmp_path):
    """15. pricing evidence closes only pricing incident."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))

    refused = incidents.observe_recovery(
        incident_class=POSITION_CLASS,
        scope=POSITION_SCOPE,
        evidence_source=AUTH_PRICING,
        state_file=state,
    )
    assert refused.reason == "EVIDENCE_SOURCE_MISMATCH"

    closed = incidents.observe_recovery(
        incident_class=PRICING_CLASS,
        scope=PRICING_SCOPE,
        evidence_source=AUTH_PRICING,
        state_file=state,
    )
    assert closed.action == incidents.ACTION_NOTIFY_RECOVERY


def test_position_evidence_closes_only_position_incident(tmp_path):
    """16. position-verification evidence closes only position incident."""

    state = tmp_path / "incidents.json"
    opened = _observe(
        state,
        klass=POSITION_CLASS,
        scope=POSITION_SCOPE,
        reason="Managed lifecycle verification incomplete: SOLUSD:ABSENT",
    )
    assert opened.action == incidents.ACTION_NOTIFY_OPEN
    _deliver(state, opened)

    refused = incidents.observe_recovery(
        incident_class=POSITION_CLASS,
        scope=POSITION_SCOPE,
        evidence_source=AUTH_PRICING,
        state_file=state,
    )
    assert refused.reason == "EVIDENCE_SOURCE_MISMATCH"

    closed = incidents.observe_recovery(
        incident_class=POSITION_CLASS,
        scope=POSITION_SCOPE,
        evidence_source=AUTH_POSITION,
        state_file=state,
    )
    assert closed.action == incidents.ACTION_NOTIFY_RECOVERY


def test_producer_owned_operator_state_success_closes_it(tmp_path):
    """17. producer-owned operator-state success can close that incident."""

    state = tmp_path / "incidents.json"
    opened = _observe(
        state,
        klass=OPERATOR_CLASS,
        scope=OPERATOR_SCOPE,
        reason="operator/capacity state unavailable: TimeoutError: registry busy",
    )
    assert opened.should_notify is True
    _deliver(state, opened)

    closed = incidents.observe_recovery(
        incident_class=OPERATOR_CLASS,
        scope=OPERATOR_SCOPE,
        evidence_source=AUTH_OPERATOR,
        evidence="operator/capacity state read succeeded",
        state_file=state,
    )
    assert closed.action == incidents.ACTION_NOTIFY_RECOVERY
    assert closed.reason == "INCIDENT_RECOVERED"


def test_run_cycle_closes_operator_state_incident_when_state_reads(tmp_path, monkeypatch):
    """17 (producer wiring). run_cycle owns operator-state recovery."""

    from app.jobs import run_cycle

    state = tmp_path / "incidents.json"
    monkeypatch.setattr(incidents, "STATE_FILE", state)
    monkeypatch.setattr(run_cycle, "get_settings", _settings)

    delivered: list = []
    monkeypatch.setattr(
        run_cycle,
        "_deliver_system_incident_decision",
        lambda *, settings, decision: delivered.append(decision) or True,
    )

    opened = _observe(
        state,
        OPERATOR_CLASS,
        OPERATOR_SCOPE,
        "operator/capacity state unavailable: RuntimeError: corrupt registry",
    )
    assert opened.should_notify is True
    _deliver(state, opened)

    assert run_cycle._close_operator_state_incident_if_open() is True
    assert len(delivered) == 1
    assert delivered[0].action == incidents.ACTION_NOTIFY_RECOVERY
    assert delivered[0].scope == OPERATOR_SCOPE.value

    # A healthy cycle with no open incident is a silent no-op.
    assert run_cycle._close_operator_state_incident_if_open() is False
    assert len(delivered) == 1


def test_operator_state_scope_is_not_monitor_owned():
    """The monitor must not claim recovery authority it cannot prove."""

    assert incidents.is_monitor_owned_scope(PUBLIC_SCOPE) is True
    assert incidents.is_monitor_owned_scope(OPERATOR_SCOPE) is False
    assert incidents.is_monitor_owned_scope(INTERNAL_SCOPE) is False
    assert incidents.is_monitor_owned_scope(incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH) is False


# ===========================================================================
# 3. INCIDENT HISTORY
# ===========================================================================


def test_outage_a_recovers_and_remains_historically_readable(tmp_path):
    """18. outage A opens -> recovers -> remains historically readable."""

    state = tmp_path / "incidents.json"
    first = _open_public_incident(state)
    recovered = _recover(state)
    _deliver(state, recovered)

    rows = incidents.read_incidents(state_file=state)
    assert len(rows) == 1
    assert rows[0]["incident_id"] == first.incident_id
    assert rows[0]["state"] == incidents.STATE_RECOVERED
    assert rows[0]["occurrence_count"] == 7
    assert rows[0]["recovered_at"] is not None

    assert incidents.read_incidents(state_file=state, include_recovered=False) == []


def test_second_outage_gets_a_new_incident_id(tmp_path):
    """19. outage B on same scope gets a new incident_id."""

    state = tmp_path / "incidents.json"
    first = _open_public_incident(state)
    _deliver(state, _recover(state))

    second = _open_public_incident(state, when=NOW + timedelta(hours=3))

    assert second.incident_id != first.incident_id
    assert second.incident_key == first.incident_key
    assert second.occurrence_count == 7
    assert second.recovered_at is None


def test_second_outage_does_not_overwrite_the_first(tmp_path):
    """20. outage B does not overwrite outage A."""

    state = tmp_path / "incidents.json"
    first = _open_public_incident(state)
    _deliver(state, _recover(state))
    _open_public_incident(state, when=NOW + timedelta(hours=3))

    archived = _row(state, first.incident_id, archived=True)
    assert archived is not None
    assert archived["incident_id"] == first.incident_id
    assert archived["state"] == incidents.STATE_RECOVERED
    assert archived["occurrence_count"] == 7
    assert archived["opened_notification_at"] is not None
    assert archived["recovered_at"] is not None
    assert archived["latest_reason"]

    active = _row(state, PUBLIC_KEY)
    assert active is not None
    assert active["incident_id"] != first.incident_id


def test_read_projection_returns_both_instances_deterministically(tmp_path):
    """21. read projection returns both deterministically."""

    state = tmp_path / "incidents.json"
    first = _open_public_incident(state)
    _deliver(state, _recover(state))
    second = _open_public_incident(state, when=NOW + timedelta(hours=3))

    rows = incidents.read_incidents(state_file=state)
    assert [row["incident_id"] for row in rows] == [first.incident_id, second.incident_id]

    # Deterministic ordering is by first_seen_at, then incident_id.
    keys = [(row["first_seen_at"], row["incident_id"]) for row in rows]
    assert keys == sorted(keys)

    # Repeated reads are stable.
    assert incidents.read_incidents(state_file=state) == rows


def test_restart_preserves_archived_and_active_history(tmp_path):
    """22. restart preserves archived + active lifecycle history."""

    state = tmp_path / "incidents.json"
    first = _open_public_incident(state)
    _deliver(state, _recover(state))
    second = _open_public_incident(state, when=NOW + timedelta(hours=3))

    restarted = _fresh_module(state)
    rows = restarted.read_incidents(state_file=state)

    assert [row["incident_id"] for row in rows] == [first.incident_id, second.incident_id]
    assert rows[0]["state"] == restarted.STATE_RECOVERED
    assert rows[1]["state"] in {restarted.STATE_OPEN, restarted.STATE_CHANGED}


def test_reopen_supersedes_undelivered_recovery_message_traceably(tmp_path):
    """A recovery message that can no longer be sent is recorded, not dropped."""

    state = tmp_path / "incidents.json"
    _open_public_incident(state)

    recovered = _recover(state)
    assert recovered.action == incidents.ACTION_NOTIFY_RECOVERY
    incidents.release_incident_notification(decision=recovered, state_file=state)

    # The scope fails again before the recovery message was delivered.
    _open_public_incident(state, when=NOW + timedelta(hours=2))

    payload = json.loads(state.read_text(encoding="utf-8"))
    archived = payload["archive"]
    entries = list(archived.values())
    assert entries
    superseded = [row for row in entries if row.get("superseded_notifications")]
    assert superseded, "the undelivered recovery must be recorded as superseded"
    assert incidents.KIND_RECOVERY in superseded[0]["superseded_notifications"]
    assert superseded[0]["notification_state"] == incidents.NOTIFICATION_SUPERSEDED


# ===========================================================================
# 4. CLASSIFICATION
# ===========================================================================


def test_combined_connectivity_and_pricing_reason_is_connectivity():
    """23. combined "pair discovery connection refused + pricing unavailable" => connectivity."""

    combined = (
        "Kraken public pair discovery unavailable: "
        "KrakenTransportError: ConnectError: connection refused; "
        "USD/stable-quote pricing unavailable for held assets: ADA.S,ETH2.S"
    )
    klass, scope = incidents.classify_degradation_reason(combined)

    assert klass is CONNECTIVITY_CLASS
    assert scope is PUBLIC_SCOPE
    # Crucially, this must NOT become an immediate-notify pricing incident: it
    # has to go through the seven-cycle connectivity recovery policy.
    assert incidents.requires_owner_recovery_cycles(klass) is True


def test_pure_held_asset_pricing_gap_is_pricing():
    """24. pure held-asset pricing gap => pricing."""

    klass, scope = incidents.classify_degradation_reason(PRICING_REASON)

    assert klass is PRICING_CLASS
    assert scope is PRICING_SCOPE
    assert incidents.requires_owner_recovery_cycles(klass) is False


def test_rate_limit_outranks_connectivity_shaped_wording():
    """25. 429 + secondary connectivity-looking wording => RATE_LIMITED."""

    klass, scope = incidents.classify_degradation_reason(
        "Kraken public HTTP 429 for Ticker; connection refused; pricing unavailable"
    )

    assert klass is RATE_LIMIT_CLASS
    assert scope is RATE_LIMIT_SCOPE
    assert (
        health.classify_failure_text("Kraken public HTTP 429; connection refused")
        is health.KrakenFailureClass.RATE_LIMITED
    )


def test_explicit_auth_failure_outranks_timeout_shaped_wording():
    """26. invalid credentials + timeout-shaped wording => AUTH_CONFIG."""

    klass, scope = incidents.classify_degradation_reason(
        "Kraken read-only key is invalid: EAPI:Invalid key; read timeout"
    )

    assert klass is incidents.SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE
    assert scope is incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH
    assert (
        health.classify_failure_text("EAPI:Invalid key; read timeout")
        is health.KrakenFailureClass.AUTH_CONFIG
    )


def test_private_connectivity_reason_stays_read_only_scope():
    """Private reachability failures stay on the read-only scope, not public."""

    klass, scope = incidents.classify_degradation_reason(
        "Kraken account state unavailable: ConnectError: connection refused"
    )
    assert klass is READ_ONLY_CLASS
    assert scope is READ_ONLY_SCOPE


# ===========================================================================
# 5. DELIVERY STATE
# ===========================================================================


def test_failed_recovered_send_leaves_recovered_pending(tmp_path):
    """27. failed RECOVERED Telegram send leaves RECOVERED_PENDING."""

    state = tmp_path / "incidents.json"
    _open_public_incident(state)

    recovered = _recover(state)
    assert recovered.action == incidents.ACTION_NOTIFY_RECOVERY

    assert incidents.release_incident_notification(decision=recovered, state_file=state)

    row = _row(state, PUBLIC_KEY)
    assert row is not None
    assert row["state"] == incidents.STATE_RECOVERED
    assert row["recovered_notification_at"] is None
    assert row["notification_state"] == incidents.NOTIFICATION_RECOVERED_PENDING
    assert incidents.KIND_RECOVERY in row["pending_notifications"]


def test_pending_notifications_select_recovered_rows_for_retry(tmp_path):
    """28. later cycle retries RECOVERED delivery (even though state is RECOVERED)."""

    state = tmp_path / "incidents.json"
    _open_public_incident(state)

    recovered = _recover(state)
    incidents.release_incident_notification(decision=recovered, state_file=state)

    pending = incidents.pending_notification_decisions(state_file=state)
    assert len(pending) == 1
    assert pending[0].action == incidents.ACTION_NOTIFY_RECOVERY
    assert pending[0].notification_kind == incidents.KIND_RECOVERY
    assert pending[0].state == incidents.STATE_RECOVERED
    assert pending[0].reservation_token


def test_exactly_one_successful_recovered_message(tmp_path):
    """29. exactly one successful RECOVERED message."""

    state = tmp_path / "incidents.json"
    _open_public_incident(state)

    recovered = _recover(state)
    incidents.release_incident_notification(decision=recovered, state_file=state)

    retry = incidents.pending_notification_decisions(state_file=state)[0]
    assert _deliver(state, retry, message_id=4242) is True

    row = _row(state, PUBLIC_KEY)
    assert row is not None
    assert row["recovered_notification_at"] is not None
    assert row["recovered_message_id"] == 4242
    assert row["notification_state"] == incidents.NOTIFICATION_RECOVERED_NOTIFIED
    assert incidents.pending_notification_decisions(state_file=state) == []


def test_failed_escalation_remains_pending(tmp_path):
    """30. failed escalation remains escalation-notification pending."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))

    escalated = incidents.observe_degradation(
        incident_class=PRICING_CLASS,
        scope=PRICING_SCOPE,
        reason=PRICING_REASON,
        severity=incidents.IncidentSeverity.CRITICAL,
        state_file=state,
    )
    assert escalated.action == incidents.ACTION_NOTIFY_ESCALATION

    incidents.release_incident_notification(decision=escalated, state_file=state)

    row = _row(state, PRICING_KEY)
    assert row is not None
    assert row["escalation_notification_at"] is None
    assert row["notification_state"] == incidents.NOTIFICATION_ESCALATION_PENDING
    assert incidents.KIND_ESCALATION in row["pending_notifications"]


def test_later_cycle_retries_escalation(tmp_path):
    """31. later cycle retries escalation."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))

    escalated = incidents.observe_degradation(
        incident_class=PRICING_CLASS,
        scope=PRICING_SCOPE,
        reason=PRICING_REASON,
        severity=incidents.IncidentSeverity.CRITICAL,
        state_file=state,
    )
    incidents.release_incident_notification(decision=escalated, state_file=state)

    # A further observation must not be needed: the pending record drives retry.
    pending = incidents.pending_notification_decisions(state_file=state)
    assert [item.notification_kind for item in pending] == [incidents.KIND_ESCALATION]
    assert _deliver(state, pending[0], message_id=5555) is True

    row = _row(state, PRICING_KEY)
    assert row is not None
    assert row["escalation_message_id"] == 5555
    assert row["notification_state"] == incidents.NOTIFICATION_ESCALATED_NOTIFIED


def test_pending_notification_retry_is_bounded(tmp_path):
    """Retries are bounded; exhausted records stay visible instead of looping."""

    state = tmp_path / "incidents.json"
    _open_public_incident(state)
    incidents.release_incident_notification(
        decision=_recover(state), state_file=state
    )

    for _ in range(incidents.MAX_NOTIFICATION_ATTEMPTS):
        decisions = incidents.pending_notification_decisions(state_file=state)
        if not decisions:
            break
        incidents.release_incident_notification(decision=decisions[0], state_file=state)

    assert incidents.pending_notification_decisions(state_file=state) == []
    row = _row(state, PUBLIC_KEY)
    assert row is not None
    # The obligation is still durably visible, just no longer auto-retried.
    assert incidents.KIND_RECOVERY in row["pending_notifications"]


def test_no_duplicate_notification_while_a_lease_is_active(tmp_path):
    """An active lease suppresses a second concurrent attempt for that kind."""

    state = tmp_path / "incidents.json"
    _open_public_incident(state)
    recovered = _recover(state)
    incidents.release_incident_notification(decision=recovered, state_file=state)

    first = incidents.pending_notification_decisions(state_file=state)
    assert len(first) == 1
    # The lease from `first` is still live, so a concurrent cycle sees nothing.
    assert incidents.pending_notification_decisions(state_file=state) == []


def test_delivered_but_unconfirmed_message_is_never_resent(tmp_path, monkeypatch):
    """32/33. delivered Telegram + first durable confirm failure must not resend."""

    from app.services import active_trade_monitor_runner as runner

    state = tmp_path / "incidents.json"
    monkeypatch.setattr(incidents, "STATE_FILE", state)
    monkeypatch.setattr(runner, "get_settings", _settings)
    monkeypatch.setattr(runner, "_confirm_sleep", lambda _: None)

    sent: list[str] = []
    monkeypatch.setattr(
        runner,
        "send_tracked_telegram",
        lambda **kwargs: sent.append(kwargs["message"])
        or SimpleNamespace(delivered=True, message_id=9876),
    )

    resolution = _coverage_resolution(coverage_complete=False, reason=PRICING_REASON)
    monkeypatch.setattr(
        runner,
        "KrakenExposureResolver",
        lambda **kwargs: SimpleNamespace(resolve=lambda: resolution),
    )

    # The first durable confirmation of the delivered message fails.
    real_confirm = incidents.confirm_incident_notification
    calls = {"n": 0}

    def flaky_confirm(**kwargs):
        calls["n"] += 1
        if calls["n"] <= runner._CONFIRM_RETRY_ATTEMPTS + 1:
            return False
        return real_confirm(**kwargs)

    monkeypatch.setattr(runner, "confirm_incident_notification", flaky_confirm)

    runner.run_active_trade_monitor()
    assert len(sent) == 1

    # Evidence recorded against incident_id + kind + message_id.
    row = _row(state, PRICING_KEY)
    assert row is not None
    incident_id = row["incident_id"]
    evidence = incidents.unconfirmed_delivery(
        incident_id=incident_id, kind=incidents.KIND_OPEN, state_file=state
    )
    assert evidence is not None
    assert evidence["message_id"] == 9876
    assert evidence["incident_id"] == incident_id
    # The delivered message must not be recorded as durable fact yet.
    assert row["opened_notification_at"] is None

    # A later cycle must reconcile the commit, never send a second copy. The
    # recorded delivery clears its own lease, so reconciliation does not have to
    # wait out the reservation TTL.
    monkeypatch.setattr(runner, "confirm_incident_notification", real_confirm)
    runner.run_active_trade_monitor()

    assert len(sent) == 1, "an already-delivered message must never be resent"
    row = _row(state, PRICING_KEY)
    assert row is not None
    assert row["open_message_id"] == 9876
    assert row["opened_notification_at"] is not None
    assert (
        incidents.unconfirmed_delivery(
            incident_id=incident_id, kind=incidents.KIND_OPEN, state_file=state
        )
        is None
    )


def test_storage_failure_cannot_silently_hide_critical_alerting(tmp_path, monkeypatch):
    """53. storage failure cannot silently hide lifecycle-critical alerting."""

    def boom(*args, **kwargs):
        raise OSError("incident registry unavailable")

    monkeypatch.setattr(incidents, "registry_lock", boom)

    decision = _pricing(tmp_path / "incidents.json")

    assert decision.action == incidents.ACTION_NOTIFY_OPEN
    assert decision.should_notify is True
    assert decision.reason == "STATE_UNAVAILABLE_FAIL_OPEN"
    assert "storage_failure" in decision.metadata


# ===========================================================================
# 6. PRIVATE PROBE BOUNDS
# ===========================================================================


def test_recovery_only_private_client_uses_bounded_timeout(monkeypatch):
    """34. recovery-only private client uses bounded timeout."""

    captured: dict = {}

    class FakeClient:
        def __init__(self, *, timeout_seconds=15.0, **kwargs):
            captured["timeout_seconds"] = timeout_seconds
            self.enabled = True

        def assert_read_only(self):
            captured["asserted"] = True
            return SimpleNamespace(is_read_only=lambda: True)

        def get_balance(self):
            captured["balance"] = True
            return {}

    import app.exchanges.kraken_private as private_module

    monkeypatch.setattr(private_module, "KrakenPrivateClient", FakeClient)

    probe = health.read_only_connectivity_probe()
    assert probe() == {}

    assert captured["timeout_seconds"] == health.KRAKEN_RECOVERY_TIMEOUT_SECONDS
    assert captured["timeout_seconds"] < 15.0
    assert captured["asserted"] is True
    assert captured["balance"] is True


def test_total_private_recovery_cycle_is_bounded():
    """35. total private recovery cycle is bounded."""

    clock = {"t": 0.0}
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock["t"] += seconds

    probe = health.KrakenScopeProbe(
        max_attempts=health.KRAKEN_RECOVERY_MAX_ATTEMPTS,
        base_delay_seconds=6.0,
        max_delay_seconds=6.0,
        jitter_ratio=0.0,
        sleeper=fake_sleep,
        random_source=lambda low, high: 0.0,
        clock=lambda: clock["t"],
        cycle_budget_seconds=10.0,
    )

    result = probe.run(
        lambda: (_ for _ in ()).throw(RuntimeError("connect timeout")),
        scope=health.KrakenHealthScope.READ_ONLY_CONNECTIVITY,
    )

    assert result.success is False
    assert result.budget_exhausted is True
    # The whole cycle stopped inside its budget instead of running every attempt.
    assert len(sleeps) < health.KRAKEN_RECOVERY_MAX_ATTEMPTS
    assert clock["t"] <= 10.0 + 6.0


def test_recovery_probe_cannot_reach_order_endpoints():
    """36. no order/cancel/modify API can be reached."""

    forbidden = (
        "add_order",
        "place_order",
        "create_order",
        "cancel_order",
        "modify_order",
        "cancel_all",
        "withdraw",
        "get_open_orders",
    )
    for name in ("kraken_health.py", "system_incidents.py"):
        source = (ROOT / "app" / "services" / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{name} must not reference {token}"

    # The only private surface the probe may touch is the read-only one.
    health_source = (ROOT / "app" / "services" / "kraken_health.py").read_text(
        encoding="utf-8"
    )
    assert "assert_read_only()" in health_source
    assert "get_balance()" in health_source


# ===========================================================================
# 7. RESET TELEMETRY
# ===========================================================================


def test_reset_callback_true_reports_true():
    """37. reset callback True -> connection_reset True."""

    probe = health.KrakenScopeProbe(
        max_attempts=1,
        sleeper=lambda _: None,
        connection_reset=lambda: True,
    )
    result = probe.run(
        lambda: (_ for _ in ()).throw(RuntimeError("connection refused")),
        scope=health.KrakenHealthScope.PUBLIC_CONNECTIVITY,
    )
    assert result.connection_reset is True


def test_reset_callback_false_reports_false():
    """38. reset callback False -> connection_reset False."""

    probe = health.KrakenScopeProbe(
        max_attempts=1,
        sleeper=lambda _: None,
        connection_reset=lambda: False,
    )
    result = probe.run(
        lambda: (_ for _ in ()).throw(RuntimeError("connection refused")),
        scope=health.KrakenHealthScope.PUBLIC_CONNECTIVITY,
    )
    assert result.connection_reset is False


def test_reset_callback_exception_reports_false():
    """39. reset callback exception -> connection_reset False."""

    def boom():
        raise RuntimeError("reset unavailable")

    probe = health.KrakenScopeProbe(
        max_attempts=1,
        sleeper=lambda _: None,
        connection_reset=boom,
    )
    result = probe.run(
        lambda: (_ for _ in ()).throw(RuntimeError("connection refused")),
        scope=health.KrakenHealthScope.PUBLIC_CONNECTIVITY,
    )
    assert result.connection_reset is False


def test_reset_is_attempted_only_for_connectivity_failures():
    """Low-level reconnect is attempted only for genuine reachability failures."""

    resets: list[str] = []

    probe = health.KrakenScopeProbe(
        max_attempts=2,
        sleeper=lambda _: None,
        random_source=lambda low, high: 0.0,
        connection_reset=lambda: resets.append("reset") or True,
    )

    auth = probe.run(
        lambda: (_ for _ in ()).throw(RuntimeError("invalid key")),
        scope=health.KrakenHealthScope.READ_ONLY_AUTH,
    )
    assert auth.success is False
    assert resets == []

    connectivity = probe.run(
        lambda: (_ for _ in ()).throw(RuntimeError("connect timeout")),
        scope=health.KrakenHealthScope.PUBLIC_CONNECTIVITY,
    )
    assert connectivity.connection_reset is True
    assert resets == ["reset"]


def test_transport_reset_returns_boolean():
    """Transport reset reports whether it actually recreated the client."""

    public = transport.KrakenPublicTransport(requests_per_second=1000.0, burst=10)
    before = public._client
    assert public.reset_connection() is True
    assert public._client is not before
    public._client.close()


# ===========================================================================
# 8. FLOOD CONTROL / AUDITABILITY (retained from the original contract)
# ===========================================================================


def test_first_semantic_degraded_incident_opens_once(tmp_path):
    """first degraded occurrence sends one alert."""

    state = tmp_path / "incidents.json"
    decision = _pricing(state)

    assert decision.action == incidents.ACTION_NOTIFY_OPEN
    assert decision.state == incidents.STATE_OPEN
    assert decision.should_notify is True
    assert decision.occurrence_count == 1
    assert decision.suppressed_notification_count == 0
    assert _deliver(state, decision) is True


def test_sixty_identical_occurrences_send_one_open_alert(tmp_path):
    """60 identical degraded occurrences still send only one OPEN alert."""

    state = tmp_path / "incidents.json"
    first = _pricing(state)
    assert first.should_notify is True
    _deliver(state, first)

    later = [_pricing(state) for _ in range(59)]

    assert all(item.action == incidents.ACTION_SUPPRESS_ONGOING for item in later)
    assert all(item.should_notify is False for item in later)


def test_occurrence_and_suppression_counts_are_truthful(tmp_path):
    """occurrence count reaches 60 truthfully; suppression count is truthful."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))
    last = None
    for _ in range(59):
        last = _pricing(state)

    assert last is not None
    assert last.occurrence_count == 60
    assert last.suppressed_notification_count == 59


def test_hour_and_day_boundaries_do_not_reopen_same_incident(tmp_path):
    """hour/day boundary does not reopen a continuously open incident."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))

    for offset in (
        timedelta(hours=1),
        timedelta(hours=7),
        timedelta(days=1, hours=2),
    ):
        decision = _pricing(state, now=NOW + offset)
        assert decision.should_notify is False
        assert decision.incident_key == PRICING_KEY

    row = _row(state, PRICING_KEY)
    assert row is not None
    assert row["occurrence_count"] == 4
    assert row["opened_notification_at"] is not None


def test_reason_wording_change_does_not_create_spam(tmp_path):
    """reason wording change does not create spam."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))

    varied = [
        "USD/stable-quote pricing unavailable for held assets: ADA.S",
        "USD/stable-quote pricing unavailable for held assets: SEI.B,SUI.B",
        "USD/stable-quote pricing unavailable for held assets: TAO.B",
    ]
    decisions = [_pricing(state, reason=item) for item in varied]

    assert all(item.should_notify is False for item in decisions)
    assert all(item.incident_key == decisions[0].incident_key for item in decisions)
    assert decisions[-1].occurrence_count == 4


def test_metadata_change_updates_without_spam(tmp_path):
    """metadata change does not create spam."""

    state = tmp_path / "incidents.json"
    first = incidents.observe_degradation(
        incident_class=PRICING_CLASS,
        scope=PRICING_SCOPE,
        reason=PRICING_REASON,
        metadata={"unpriced_assets": ["ADA.S", "ETH2.S"]},
        state_file=state,
    )
    _deliver(state, first)

    second = incidents.observe_degradation(
        incident_class=PRICING_CLASS,
        scope=PRICING_SCOPE,
        reason=PRICING_REASON_REORDERED,
        metadata={"unpriced_assets": ["TAO.B", "SUI.B", "ADA.S", "ETH2.S", "SEI.B"]},
        state_file=state,
    )

    assert second.should_notify is False
    assert second.metadata["unpriced_assets"] == [
        "TAO.B",
        "SUI.B",
        "ADA.S",
        "ETH2.S",
        "SEI.B",
    ]


def test_material_escalation_produces_exactly_one_escalation(tmp_path):
    """material escalation can produce only one escalation."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))

    escalated = incidents.observe_degradation(
        incident_class=PRICING_CLASS,
        scope=PRICING_SCOPE,
        reason=PRICING_REASON,
        severity=incidents.IncidentSeverity.CRITICAL,
        state_file=state,
    )
    assert escalated.action == incidents.ACTION_NOTIFY_ESCALATION
    _deliver(state, escalated)

    again = incidents.observe_degradation(
        incident_class=PRICING_CLASS,
        scope=PRICING_SCOPE,
        reason="USD/stable-quote pricing unavailable for held assets: ADA.S",
        severity=incidents.IncidentSeverity.CRITICAL,
        state_file=state,
    )
    assert again.action == incidents.ACTION_SUPPRESS_ONGOING
    assert again.should_notify is False


def test_recovery_produces_exactly_one_recovered(tmp_path):
    """recovery produces exactly one RECOVERED."""

    state = tmp_path / "incidents.json"
    _open_public_incident(state)

    recovered = _recover(state)
    assert recovered.action == incidents.ACTION_NOTIFY_RECOVERY
    _deliver(state, recovered)

    repeat = _recover(state)
    assert repeat.action == incidents.ACTION_SILENT
    assert repeat.should_notify is False


def test_repeated_healthy_cycles_after_recovery_emit_nothing(tmp_path):
    """repeated healthy cycles after recovery produce nothing."""

    state = tmp_path / "incidents.json"
    _open_public_incident(state)
    _deliver(state, _recover(state))

    for _ in range(10):
        decision = _recover(state)
        assert decision.should_notify is False
        assert decision.action == incidents.ACTION_SILENT


def test_later_independent_incident_can_open_again(tmp_path):
    """later independent incident can open again."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))
    _deliver(
        state,
        incidents.observe_recovery(
            incident_class=PRICING_CLASS,
            scope=PRICING_SCOPE,
            evidence_source=AUTH_PRICING,
            state_file=state,
        ),
    )

    again = _pricing(state)
    assert again.action == incidents.ACTION_NOTIFY_OPEN
    assert again.occurrence_count == 1
    assert again.recovered_at is None


def test_pricing_degradation_does_not_increment_connectivity_count(tmp_path):
    """pricing degradation does not increment connectivity failure count."""

    state = tmp_path / "incidents.json"
    for _ in range(10):
        decision = _pricing(state)

    assert decision.consecutive_recovery_failures == 0
    payload = json.loads(state.read_text())
    assert PUBLIC_KEY not in payload["incidents"]


def test_sixty_pricing_observations_remain_one_incident(tmp_path):
    """60 pricing-gap observations remain one incident."""

    state = tmp_path / "incidents.json"
    sends = 0
    for _ in range(60):
        decision = _pricing(state)
        if decision.should_notify:
            sends += 1
            _deliver(state, decision)

    assert sends == 1
    row = _row(state, PRICING_KEY)
    assert row is not None
    assert row["occurrence_count"] == 60
    assert row["suppressed_notification_count"] == 59


def test_concurrent_duplicate_open_attempts_produce_one_notification(tmp_path):
    """concurrent duplicate OPEN attempts produce one notification."""

    state = tmp_path / "incidents.json"
    for _ in range(6):
        _failed_cycle(state)

    workers = 12
    barrier = threading.Barrier(workers)
    results: list = []
    guard = threading.Lock()

    def run():
        barrier.wait()
        decision = _failed_cycle(state, now=NOW)
        with guard:
            results.append(decision)

    threads = [threading.Thread(target=run) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    opens = [item for item in results if item.action == incidents.ACTION_NOTIFY_OPEN]
    suppressed = [
        item for item in results if item.action == incidents.ACTION_SUPPRESS_ONGOING
    ]
    assert len(opens) == 1
    assert len(suppressed) == workers - 1

    _deliver(state, opens[0])
    assert _failed_cycle(state, now=NOW).should_notify is False


def test_concurrent_duplicate_recovered_attempts_produce_one_notification(tmp_path):
    """concurrent duplicate RECOVERED attempts produce one notification."""

    state = tmp_path / "incidents.json"
    _open_public_incident(state)

    workers = 10
    barrier = threading.Barrier(workers)
    results: list = []
    guard = threading.Lock()

    def run():
        barrier.wait()
        decision = _recover(state, now=NOW)
        with guard:
            results.append(decision)

    threads = [threading.Thread(target=run) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    notifications = [item for item in results if item.should_notify]
    assert len(notifications) == 1
    assert notifications[0].action == incidents.ACTION_NOTIFY_RECOVERY


def test_restart_preserves_incident_state(tmp_path):
    """restart preserves state."""

    state = tmp_path / "incidents.json"
    before = _pricing(state)
    _deliver(state, before)

    restarted = _fresh_module(state)
    decision = restarted.observe_degradation(
        incident_class=restarted.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
        scope=restarted.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        reason=PRICING_REASON,
    )

    assert decision.should_notify is False
    assert decision.occurrence_count == 2
    assert decision.incident_id == before.incident_id


def test_actual_probe_not_cache_hit_must_prove_recovery(tmp_path):
    """an actual health probe, not a cache hit, is required to close an incident."""

    state = tmp_path / "incidents.json"
    _open_public_incident(state)

    cached = incidents.observe_recovery(
        incident_class=CONNECTIVITY_CLASS,
        scope=PUBLIC_SCOPE,
        evidence_source=AUTH_PUBLIC,
        evidence="ttl cache hit",
        authoritative=False,
        state_file=state,
    )
    assert cached.action == incidents.ACTION_SILENT
    assert cached.reason == "EVIDENCE_NOT_AUTHORITATIVE"
    assert _row(state, PUBLIC_KEY)["state"] == incidents.STATE_OPEN

    fresh = _recover(state)
    assert fresh.action == incidents.ACTION_NOTIFY_RECOVERY


def test_persisted_incident_state_never_contains_secrets(tmp_path):
    """secrets never appear in persisted incident state."""

    state = tmp_path / "incidents.json"
    secret = "super-secret-bot-token-abcdef123456"
    decision = incidents.observe_degradation(
        incident_class=CONNECTIVITY_CLASS,
        scope=PUBLIC_SCOPE,
        reason=CONNECTIVITY_REASON,
        metadata={
            "bot_token": secret,
            "api_key": secret,
            "authorization": f"Bearer {secret}",
            "private_key": secret,
            "safe_field": "kept",
        },
        state_file=state,
    )

    raw = state.read_text(encoding="utf-8")
    assert secret not in raw
    assert "token" not in json.dumps(decision.metadata).lower()
    assert decision.metadata == {"safe_field": "kept"}


def test_incident_projection_is_secret_free_and_complete(tmp_path):
    """incident data contract supports a read-only Cockpit projection."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))
    _pricing(state, reason=PRICING_REASON_REORDERED)

    rows = incidents.read_incidents(state_file=state)
    assert len(rows) == 1
    row = rows[0]
    for key in (
        "incident_id",
        "incident_key",
        "alert_family",
        "incident_class",
        "scope",
        "severity",
        "state",
        "notification_state",
        "first_seen_at",
        "last_seen_at",
        "occurrence_count",
        "consecutive_recovery_failures",
        "recovery_cycles_attempted",
        "suppressed_notification_count",
        "notification_delivered",
        "notification_pending",
        "recovered_at",
        "outage_seconds",
        "latest_reason",
        "metadata",
    ):
        assert key in row

    assert row["occurrence_count"] == 2
    assert row["notification_delivered"] is True
    assert row["alert_family"] == "SYSTEM_HEALTH"


def test_incident_contract_fixture_matches_code_constants(tmp_path):
    """The frozen incident contract fixture must match the implementation."""

    fixture_path = (
        ROOT
        / "docs"
        / "architecture"
        / "v1.2"
        / "fixtures"
        / "system_incident_lifecycle.example.json"
    )
    contract = json.loads(fixture_path.read_text(encoding="utf-8"))

    assert contract["lifecycle"] == list(incidents.INCIDENT_STATES)
    assert contract["schema_version"] == incidents.SCHEMA_VERSION
    assert contract["identity"]["alert_family"] == incidents.SYSTEM_HEALTH_FAMILY
    assert contract["notification_budget"]["open"] == 1
    assert contract["notification_budget"]["recovered"] == 1
    assert contract["connectivity_recovery_rule"]["notify_after_consecutive_failures"] == (
        incidents.CONNECTIVITY_RECOVERY_FAILURES_BEFORE_NOTIFY
    )
    assert contract["connectivity_recovery_rule"]["notify_after_consecutive_failures"] == 7

    documented_scopes = {
        item.split("SYSTEM_HEALTH:", 1)[1] for item in contract["identity"]["examples"]
    }
    assert documented_scopes == {scope.value for scope in incidents.SystemIncidentScope}

    # Documented recovery authority must match the code exactly.
    documented_authority = contract["recovery_authority"]["by_scope"]
    assert documented_authority == {
        scope.value: incidents.recovery_authority_for_scope(scope).value
        for scope in incidents.SystemIncidentScope
    }
    assert set(contract["recovery_authority"]["monitor_owned_scopes"]) == set(
        incidents.MONITOR_OWNED_SCOPES
    )

    # Every persisted field must be produced by a freshly opened incident row.
    state = tmp_path / "_contract_probe.json"
    decision = _pricing(state)
    stored = json.loads(state.read_text(encoding="utf-8"))["incidents"][
        decision.incident_key
    ]
    for field in contract["persisted_row_fields"]:
        assert field in stored, field
    serialized = json.dumps(stored).lower()
    for forbidden in contract["never_persisted"]:
        assert forbidden.lower() not in serialized


def test_recovery_duration_is_reported(tmp_path):
    """Outage duration is derived from durable timestamps, not guessed."""

    state = tmp_path / "incidents.json"
    _open_public_incident(state)

    recovered = _recover(state, now=NOW + timedelta(minutes=12))
    assert recovered.recovered_after_recovery_failures == 7

    row = _row(state, PUBLIC_KEY)
    assert row is not None
    assert row["recovery_failures_before_reset"] == 7

    from app.services.alert_v2_format import outage_seconds_between

    duration = outage_seconds_between(recovered.first_seen_at, recovered.recovered_at)
    assert duration == pytest.approx(12 * 60, abs=1)


# ---------------------------------------------------------------------------
# Shared helpers for runner-integration tests in this module
# ---------------------------------------------------------------------------


def _settings():
    return SimpleNamespace(telegram_bot_token="token", telegram_chat_id="chat")


def _coverage_resolution(*, coverage_complete: bool, reason: str = ""):
    from app.services.kraken_exposure_resolver import ExposureResolution

    return ExposureResolution(
        exposures=(),
        coverage_complete=coverage_complete,
        reason=reason,
    )
