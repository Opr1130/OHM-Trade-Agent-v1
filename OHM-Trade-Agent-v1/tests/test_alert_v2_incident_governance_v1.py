"""Alert v2 incident governance: decision-first notifications and flood control.

Covers the required test matrix for:

* incident lifecycle / flood control (one semantic incident -> at most one OPEN,
  one escalation, one RECOVERED, with every occurrence still auditable),
* Kraken connectivity auto-recovery and the owner's "notify on failure #7" rule,
* rate-limit / auth / pricing / position classification separation,
* delivery failure vs incident state,
* persistence, restart and concurrency,
* secret-free incident persistence and read-only projection.

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
PRICING_KEY = "SYSTEM_HEALTH:KRAKEN:HELD_ASSET_PRICING"


def _row(state: Path, key: str) -> dict | None:
    payload = json.loads(Path(state).read_text(encoding="utf-8"))
    return payload.get("incidents", {}).get(key)


def _fresh_module(state_file: Path):
    """Load a second, independent copy of the incident module.

    This models a process restart: the fresh module object shares no in-memory
    state with the one already imported, so anything it knows must come from the
    durable state file. The module is registered in ``sys.modules`` first because
    ``dataclasses`` resolves string annotations through the owning module.
    """

    import sys

    spec = importlib.util.spec_from_file_location(
        "system_incidents_restarted",
        ROOT / "app" / "services" / "system_incidents.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if module.__dict__.get("SystemIncidentClass") is None:
            sys.modules.pop(spec.name, None)
    module.STATE_FILE = state_file
    return module


def _pricing(state, reason=PRICING_REASON, **kwargs):
    return incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
        scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        reason=reason,
        state_file=state,
        **kwargs,
    )


def _connectivity(state, when=NOW, **kwargs):
    return incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=incidents.SystemIncidentScope.KRAKEN_PUBLIC,
        reason=CONNECTIVITY_REASON,
        now=when,
        state_file=state,
        **kwargs,
    )


def _deliver(state, decision, message_id=77, when=NOW):
    """Simulate a successful Telegram delivery for a decision."""

    return incidents.confirm_incident_notification(
        decision=decision, message_id=message_id, now=when, state_file=state
    )


# ===========================================================================
# INCIDENT / FLOOD CONTROL
# ===========================================================================


def test_first_semantic_degraded_incident_opens_once(tmp_path):
    """1. first degraded occurrence sends one alert."""

    state = tmp_path / "incidents.json"
    decision = _pricing(state)

    assert decision.action == incidents.ACTION_NOTIFY_OPEN
    assert decision.state == incidents.STATE_OPEN
    assert decision.should_notify is True
    assert decision.occurrence_count == 1
    assert decision.suppressed_notification_count == 0
    assert _deliver(state, decision) is True


def test_sixty_identical_occurrences_send_one_open_alert(tmp_path):
    """2. 60 identical degraded occurrences still send only one OPEN alert."""

    state = tmp_path / "incidents.json"
    first = _pricing(state)
    assert first.should_notify is True
    _deliver(state, first)

    later = [_pricing(state) for _ in range(59)]

    assert all(item.action == incidents.ACTION_SUPPRESS_ONGOING for item in later)
    assert all(item.should_notify is False for item in later)


def test_occurrence_count_is_truthful(tmp_path):
    """3. occurrence count reaches 60 truthfully."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))
    for _ in range(59):
        last = _pricing(state)

    assert last.occurrence_count == 60


def test_suppressed_notification_count_is_truthful(tmp_path):
    """4. suppressed notification count is truthful."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))
    last = None
    for _ in range(59):
        last = _pricing(state)

    assert last is not None
    assert last.occurrence_count == 60
    assert last.suppressed_notification_count == 59


def test_hour_boundary_does_not_reopen_same_incident(tmp_path):
    """5. hour boundary does not reopen same incident."""

    state = tmp_path / "incidents.json"
    for _ in range(6):
        _connectivity(state)
    _deliver(state, _connectivity(state))

    for hour in range(1, 8):
        decision = _connectivity(state, when=NOW + timedelta(hours=hour))
        assert decision.should_notify is False
        assert decision.action == incidents.ACTION_SUPPRESS_ONGOING
        assert decision.incident_key == PUBLIC_KEY

    row = _row(state, PUBLIC_KEY)
    assert row is not None
    assert row["occurrence_count"] == 14


def test_day_boundary_does_not_reopen_continuously_open_incident(tmp_path):
    """6. day boundary does not reopen continuously open incident."""

    state = tmp_path / "incidents.json"
    for _ in range(6):
        _connectivity(state)
    _deliver(state, _connectivity(state))

    next_day = _connectivity(state, when=NOW + timedelta(days=1, hours=2))
    assert next_day.should_notify is False

    row = _row(state, PUBLIC_KEY)
    assert row is not None
    assert row["state"] in {incidents.STATE_CHANGED, incidents.STATE_OPEN}
    assert row["opened_notification_at"] is not None


def test_reason_wording_change_does_not_create_spam(tmp_path):
    """7. reason wording change does not create spam."""

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
    """8. metadata change does not create spam."""

    state = tmp_path / "incidents.json"
    first = incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
        scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        reason=PRICING_REASON,
        metadata={"unpriced_assets": ["ADA.S", "ETH2.S"]},
        state_file=state,
    )
    _deliver(state, first)

    second = incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
        scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
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
    """9. material escalation can produce only one escalation."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))

    escalated = incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
        scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        reason=PRICING_REASON,
        severity=incidents.IncidentSeverity.CRITICAL,
        state_file=state,
    )
    assert escalated.action == incidents.ACTION_NOTIFY_ESCALATION
    _deliver(state, escalated)

    again = incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
        scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        reason="USD/stable-quote pricing unavailable for held assets: ADA.S",
        severity=incidents.IncidentSeverity.CRITICAL,
        state_file=state,
    )
    assert again.action == incidents.ACTION_SUPPRESS_ONGOING
    assert again.should_notify is False


def test_recovery_produces_exactly_one_recovered(tmp_path):
    """10. recovery produces exactly one RECOVERED."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))

    recovered = incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
        scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        state_file=state,
    )
    assert recovered.action == incidents.ACTION_NOTIFY_RECOVERY
    _deliver(state, recovered)

    repeat = incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
        scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        state_file=state,
    )
    assert repeat.action == incidents.ACTION_SILENT
    assert repeat.should_notify is False


def test_repeated_healthy_cycles_after_recovery_emit_nothing(tmp_path):
    """11. repeated healthy cycles after recovery produce nothing."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))
    _deliver(
        state,
        incidents.observe_recovery(
            incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
            scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
            state_file=state,
        ),
    )

    for _ in range(10):
        decision = incidents.observe_recovery(
            incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
            scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
            state_file=state,
        )
        assert decision.should_notify is False
        assert decision.action == incidents.ACTION_SILENT


def test_later_independent_incident_can_open_again(tmp_path):
    """12. later independent incident can open again."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))
    _deliver(
        state,
        incidents.observe_recovery(
            incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
            scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
            state_file=state,
        ),
    )

    again = _pricing(state)
    assert again.action == incidents.ACTION_NOTIFY_OPEN
    assert again.occurrence_count == 1
    assert again.recovered_at is None


def test_restart_preserves_incident_state(tmp_path):
    """13. restart preserves state."""

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
    assert decision.action == restarted.ACTION_SUPPRESS_ONGOING
    assert decision.occurrence_count == 2
    assert decision.incident_id == before.incident_id
    assert decision.incident_key == before.incident_key


def test_concurrent_duplicate_open_attempts_produce_one_notification(tmp_path):
    """14. concurrent duplicate OPEN attempts produce one notification."""

    state = tmp_path / "incidents.json"
    # Drive the incident to the notification threshold so every concurrent
    # attempt is notification-eligible and only the reservation can stop it.
    for _ in range(6):
        _connectivity(state)

    workers = 12
    barrier = threading.Barrier(workers)
    results: list = []
    guard = threading.Lock()

    def run():
        barrier.wait()
        decision = _connectivity(state, when=NOW)
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
    reference = _connectivity(state, when=NOW)
    assert reference.should_notify is False


def test_concurrent_duplicate_recovered_attempts_produce_one_notification(tmp_path):
    """15. concurrent duplicate RECOVERED attempts produce one notification."""

    state = tmp_path / "incidents.json"
    for _ in range(6):
        _connectivity(state)
    _deliver(state, _connectivity(state))

    workers = 10
    barrier = threading.Barrier(workers)
    results: list = []
    guard = threading.Lock()

    def run():
        barrier.wait()
        decision = incidents.observe_recovery(
            incident_class=incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
            scope=incidents.SystemIncidentScope.KRAKEN_PUBLIC,
            now=NOW,
            state_file=state,
        )
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


# ===========================================================================
# CONNECTIVITY / AUTO RECOVERY
# ===========================================================================


def test_recovery_cycles_one_to_six_are_silent(tmp_path):
    """16-22. recovery cycles 1..6 emit no Telegram; the first alert is #7."""

    state = tmp_path / "incidents.json"

    for cycle in range(1, 7):
        decision = _connectivity(state)
        assert decision.action == incidents.ACTION_SILENT
        assert decision.should_notify is False
        assert decision.consecutive_recovery_failures == cycle
        assert decision.notification_state == incidents.NOTIFICATION_BELOW_THRESHOLD

    row = _row(state, PUBLIC_KEY)
    assert row is not None
    assert row["opened_notification_at"] is None
    assert row["occurrence_count"] == 6
    assert len(json.loads(state.read_text())["incidents"]) == 1


def test_seventh_failed_recovery_cycle_notifies_exactly_once(tmp_path):
    """23. cycle 7 -> exactly one SYSTEM FAILURE notification."""

    state = tmp_path / "incidents.json"
    for _ in range(6):
        _connectivity(state)

    seventh = _connectivity(state)
    assert seventh.action == incidents.ACTION_NOTIFY_OPEN
    assert seventh.should_notify is True
    assert seventh.consecutive_recovery_failures == 7
    _deliver(state, seventh)


def test_later_cycles_do_not_duplicate_failure_notification(tmp_path):
    """24. cycles 8..N -> no duplicate failure notification."""

    state = tmp_path / "incidents.json"
    for _ in range(6):
        _connectivity(state)
    _deliver(state, _connectivity(state))

    for _ in range(8, 20):
        decision = _connectivity(state)
        assert decision.should_notify is False
        assert decision.action == incidents.ACTION_SUPPRESS_ONGOING

    row = _row(state, PUBLIC_KEY)
    assert row is not None
    assert row["occurrence_count"] == 19
    assert row["suppressed_notification_count"] == 12


def test_automatic_recovery_continues_after_notification(tmp_path):
    """25. automatic recovery continues after cycle 7."""

    state = tmp_path / "incidents.json"
    for _ in range(6):
        _connectivity(state)
    _deliver(state, _connectivity(state))

    for cycle in range(8, 12):
        decision = _connectivity(state)
        assert decision.consecutive_recovery_failures == cycle
        assert decision.state != incidents.STATE_RECOVERED


def test_success_before_cycle_seven_is_silent_restoration(tmp_path):
    """26. success before cycle 7 -> silent healthy restoration."""

    state = tmp_path / "incidents.json"
    for _ in range(5):
        _connectivity(state)

    recovered = incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=incidents.SystemIncidentScope.KRAKEN_PUBLIC,
        state_file=state,
    )
    assert recovered.action == incidents.ACTION_SILENT
    assert recovered.reason == "SILENT_HEALTHY_RESTORATION"
    assert recovered.should_notify is False
    assert _row(state, PUBLIC_KEY)["state"] == incidents.STATE_RECOVERED


def test_success_after_failure_alert_emits_exactly_one_recovered(tmp_path):
    """27. success after failure alert -> exactly one SYSTEM RECOVERED."""

    state = tmp_path / "incidents.json"
    for _ in range(6):
        _connectivity(state)
    _deliver(state, _connectivity(state))

    recovered = incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=incidents.SystemIncidentScope.KRAKEN_PUBLIC,
        now=NOW + timedelta(minutes=30),
        state_file=state,
    )
    assert recovered.action == incidents.ACTION_NOTIFY_RECOVERY
    assert recovered.recovered_after_recovery_failures == 7
    _deliver(state, recovered)

    again = incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=incidents.SystemIncidentScope.KRAKEN_PUBLIC,
        now=NOW + timedelta(minutes=31),
        state_file=state,
    )
    assert again.should_notify is False
    assert again.action == incidents.ACTION_SILENT


def test_success_resets_consecutive_recovery_failures_to_zero(tmp_path):
    """28. success resets consecutive recovery failures to zero."""

    state = tmp_path / "incidents.json"
    for _ in range(6):
        _connectivity(state)
    _deliver(state, _connectivity(state))

    incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=incidents.SystemIncidentScope.KRAKEN_PUBLIC,
        state_file=state,
    )

    row = _row(state, PUBLIC_KEY)
    assert row is not None
    assert row["consecutive_recovery_failures"] == 0
    assert row["state"] == incidents.STATE_RECOVERED


def test_restart_at_failure_count_five_continues_at_six(tmp_path):
    """29. restart at failure count 5 -> next failure becomes 6."""

    state = tmp_path / "incidents.json"
    for _ in range(5):
        _connectivity(state)

    restarted = _fresh_module(state)
    sixth = restarted.observe_degradation(
        incident_class=restarted.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=restarted.SystemIncidentScope.KRAKEN_PUBLIC,
        reason=CONNECTIVITY_REASON,
        now=NOW + timedelta(minutes=1),
    )

    assert sixth.consecutive_recovery_failures == 6
    assert sixth.should_notify is False

    seventh = incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=incidents.SystemIncidentScope.KRAKEN_PUBLIC,
        reason=CONNECTIVITY_REASON,
        now=NOW + timedelta(minutes=2),
        state_file=state,
    )
    assert seventh.action == incidents.ACTION_NOTIFY_OPEN


def test_internal_transport_retries_do_not_increment_recovery_cycles(monkeypatch):
    """30. internal transport retries do not individually increment the counter."""

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


def test_actual_probe_not_cache_hit_must_prove_recovery(tmp_path):
    """31. actual health probe, not cache hit, is required to close an incident."""

    state = tmp_path / "incidents.json"
    for _ in range(6):
        _connectivity(state)
    _deliver(state, _connectivity(state))

    cached = incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=incidents.SystemIncidentScope.KRAKEN_PUBLIC,
        evidence="ttl cache hit",
        authoritative=False,
        state_file=state,
    )

    assert cached.action == incidents.ACTION_SILENT
    assert cached.reason == "EVIDENCE_NOT_AUTHORITATIVE"
    assert _row(state, PUBLIC_KEY)["state"] == incidents.STATE_OPEN

    fresh = incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=incidents.SystemIncidentScope.KRAKEN_PUBLIC,
        evidence="fresh Time probe succeeded",
        authoritative=True,
        state_file=state,
    )
    assert fresh.action == incidents.ACTION_NOTIFY_RECOVERY


def test_public_success_cannot_close_read_only_incident(tmp_path):
    """32. public success cannot close private/read-only incident."""

    state = tmp_path / "incidents.json"
    reason = "Kraken account state unavailable: ConnectError: connection refused"
    for _ in range(7):
        incidents.observe_degradation(
            incident_class=incidents.SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY,
            scope=incidents.SystemIncidentScope.KRAKEN_READ_ONLY,
            reason=reason,
            state_file=state,
        )

    read_only_key = "SYSTEM_HEALTH:KRAKEN:READ_ONLY_CONNECTIVITY"
    assert _row(state, read_only_key)["opened_notification_at"] is None

    incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=incidents.SystemIncidentScope.KRAKEN_PUBLIC,
        state_file=state,
    )

    assert _row(state, read_only_key)["state"] == incidents.STATE_OPEN


def test_private_success_cannot_falsely_close_public_incident(tmp_path):
    """33. private success cannot falsely close public incident."""

    state = tmp_path / "incidents.json"
    for _ in range(6):
        _connectivity(state)
    _deliver(state, _connectivity(state))

    read_only = incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY,
        scope=incidents.SystemIncidentScope.KRAKEN_READ_ONLY,
        state_file=state,
    )
    assert read_only.should_notify is False
    assert _row(state, PUBLIC_KEY)["state"] == incidents.STATE_OPEN


def test_low_level_recovery_may_recreate_http_client():
    """34. low-level recovery may recreate HTTP client safely."""

    public = transport.KrakenPublicTransport(requests_per_second=1000.0, burst=10)
    before = public._client
    assert public.reset_connection() is True
    assert public._client is not before
    public._client.close()


def test_recovery_probe_reconnects_only_on_connectivity_failure():
    """Low-level reconnect is attempted only for genuine reachability failures."""

    resets: list[str] = []

    probe = health.KrakenScopeProbe(
        max_attempts=2,
        sleeper=lambda _: None,
        random_source=lambda low, high: 0.0,
        connection_reset=lambda: resets.append("reset"),
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
    assert connectivity.success is False
    assert connectivity.connection_reset is True
    assert resets == ["reset"]


def test_recovery_logic_never_reaches_trading_or_order_apis():
    """35. recovery logic never reaches private trading/order APIs."""

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


def test_read_only_recovery_probe_asserts_read_only_before_any_call():
    """The only private surface recovery may touch is the read-only one."""

    calls: list[str] = []

    class FakeReadOnlyClient:
        enabled = True

        def assert_read_only(self):
            calls.append("assert_read_only")
            return SimpleNamespace(is_read_only=lambda: True)

        def get_balance(self):
            calls.append("get_balance")
            return {}

    probe = health.read_only_connectivity_probe(FakeReadOnlyClient())
    assert probe() == {}
    # The guard always runs first, so a future client gaining an order endpoint
    # could not be reached by the recovery path.
    assert calls == ["assert_read_only", "get_balance"]

    source = (ROOT / "app" / "services" / "kraken_health.py").read_text(encoding="utf-8")
    assert "assert_read_only()" in source


# ===========================================================================
# RATE LIMIT / AUTH
# ===========================================================================


def test_http_429_is_rate_limited_not_connectivity():
    """36. HTTP 429 is classified RATE_LIMITED, not connectivity unavailable."""

    assert (
        health.classify_failure_text("Kraken public HTTP 429 for Ticker")
        is health.KrakenFailureClass.RATE_LIMITED
    )
    assert (
        health.classify_failure_text("too many requests")
        is health.KrakenFailureClass.RATE_LIMITED
    )
    assert (
        health.classify_failure_text("Rate limit exceeded, retry after 30s")
        is health.KrakenFailureClass.RATE_LIMITED
    )


def test_retry_after_and_backoff_are_honoured_without_tight_loop():
    """37. Retry-After/backoff is honored where available."""

    delays: list[float] = []
    probe = health.KrakenScopeProbe(
        max_attempts=3,
        base_delay_seconds=0.5,
        max_delay_seconds=2.0,
        jitter_ratio=0.0,
        sleeper=delays.append,
        random_source=lambda low, high: 0.0,
    )

    result = probe.run(
        lambda: (_ for _ in ()).throw(RuntimeError("Kraken public HTTP 429")),
        scope=health.KrakenHealthScope.RATE_LIMIT,
    )

    assert result.success is False
    assert result.attempts == 3
    # Backoff increases and is bounded by the ceiling; never a tight loop.
    assert delays == [0.5, 1.0]
    assert all(delay <= 2.0 for delay in delays)


def test_backoff_is_jittered_and_ceiling_bounded():
    """Backoff applies jitter and never exceeds the configured ceiling."""

    delays: list[float] = []
    probe = health.KrakenScopeProbe(
        max_attempts=6,
        base_delay_seconds=1.0,
        max_delay_seconds=4.0,
        jitter_ratio=0.5,
        sleeper=delays.append,
        random_source=lambda low, high: high,
    )
    probe.run(
        lambda: (_ for _ in ()).throw(RuntimeError("connection refused")),
        scope=health.KrakenHealthScope.PUBLIC_CONNECTIVITY,
    )

    assert delays == [1.5, 3.0, 4.0, 4.0, 4.0]
    assert all(delay <= 4.0 for delay in delays)


def test_rate_limit_does_not_increment_connectivity_failure_count(tmp_path):
    """38. rate limit does not increment connectivity failure count."""

    state = tmp_path / "incidents.json"
    decision = incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.KRAKEN_RATE_LIMITED,
        scope=incidents.SystemIncidentScope.KRAKEN_RATE_LIMIT,
        reason="Kraken public HTTP 429 for Ticker",
        state_file=state,
    )

    assert decision.incident_class == "KRAKEN_RATE_LIMITED"
    assert decision.consecutive_recovery_failures == 0
    assert decision.action == incidents.ACTION_NOTIFY_OPEN
    # The connectivity incident was never created at all.
    assert _row(state, PUBLIC_KEY) is None
    assert (
        incidents.requires_owner_recovery_cycles(
            incidents.SystemIncidentClass.KRAKEN_RATE_LIMITED
        )
        is False
    )


def test_missing_credential_does_not_run_seven_connection_retries(tmp_path):
    """39. missing credential does not run seven connection retries."""

    state = tmp_path / "incidents.json"
    first = incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE,
        scope=incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH,
        reason="Kraken private credentials are not configured",
        state_file=state,
    )

    assert first.action == incidents.ACTION_NOTIFY_OPEN
    assert first.reason == "INCIDENT_OPEN"
    assert first.consecutive_recovery_failures == 0
    policy = incidents.policy_for(
        incidents.SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE
    )
    assert policy.notify_after_failures == 1
    assert policy.requires_recovery_cycles is False


def test_invalid_credential_is_a_separate_system_incident(tmp_path):
    """40. invalid/revoked credential is a separate SYSTEM auth/config incident."""

    klass, scope = incidents.classify_degradation_reason(
        "Kraken read-only key is invalid: EAPI:Invalid key"
    )
    assert klass is incidents.SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE
    assert scope is incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH

    state = tmp_path / "incidents.json"
    decision = incidents.observe_degradation(
        incident_class=klass, scope=scope, reason="EAPI:Invalid key", state_file=state
    )
    assert decision.action == incidents.ACTION_NOTIFY_OPEN
    assert decision.incident_key == "SYSTEM_HEALTH:KRAKEN:READ_ONLY_AUTH"


def test_persisted_incident_state_never_contains_secrets(tmp_path):
    """41 / 76. secrets never appear in persisted incident state."""

    state = tmp_path / "incidents.json"
    secret = "super-secret-bot-token-abcdef123456"
    decision = incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=incidents.SystemIncidentScope.KRAKEN_PUBLIC,
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


# ===========================================================================
# PRICING / POSITION
# ===========================================================================


def test_pricing_gap_is_held_asset_pricing_degraded():
    """42. "USD/stable-quote pricing unavailable..." is HELD_ASSET_PRICING_DEGRADED."""

    klass, scope = incidents.classify_degradation_reason(PRICING_REASON)
    assert klass is incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED
    assert scope is incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING

    # And it is emphatically NOT a connectivity incident.
    assert klass is not incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE
    assert klass is not incidents.SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY


def test_pricing_degradation_does_not_increment_connectivity_count(tmp_path):
    """43. pricing degradation does not increment connectivity failure count."""

    state = tmp_path / "incidents.json"
    for _ in range(10):
        decision = _pricing(state)

    assert decision.consecutive_recovery_failures == 0
    payload = json.loads(state.read_text())
    assert PUBLIC_KEY not in payload["incidents"]


def test_sixty_pricing_observations_remain_one_incident(tmp_path):
    """44. 60 pricing-gap observations remain one incident."""

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


def test_reordered_asset_list_remains_same_incident(tmp_path):
    """45. reordered asset list remains same incident."""

    state = tmp_path / "incidents.json"
    first = _pricing(state, reason=PRICING_REASON)
    _deliver(state, first)

    second = _pricing(state, reason=PRICING_REASON_REORDERED)
    assert second.incident_key == first.incident_key
    assert second.should_notify is False


def test_changed_asset_membership_updates_metadata_without_spam(tmp_path):
    """46. changed asset membership updates metadata without spam."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))

    added = _pricing(
        state,
        reason=(
            "USD/stable-quote pricing unavailable for held assets: "
            "ADA.S,ETH2.S,SEI.B,SUI.B,TAO.B,NEW.B"
        ),
        metadata={
            "unpriced_assets": ["ADA.S", "ETH2.S", "SEI.B", "SUI.B", "TAO.B", "NEW.B"]
        },
    )

    assert added.should_notify is False
    assert added.metadata["unpriced_assets"][-1] == "NEW.B"


def test_pricing_recovery_closes_pricing_incident_exactly_once(tmp_path):
    """47. pricing recovery closes pricing incident exactly once."""

    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))

    first = incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
        scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        state_file=state,
    )
    _deliver(state, first)
    second = incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
        scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        state_file=state,
    )

    assert first.action == incidents.ACTION_NOTIFY_RECOVERY
    assert second.should_notify is False


def test_position_verification_is_separate_from_connectivity(tmp_path):
    """48. POSITION_VERIFICATION_UNAVAILABLE is separate from connectivity."""

    klass, scope = incidents.classify_degradation_reason(
        "Managed lifecycle verification incomplete: SOLUSD:ABSENT"
    )
    assert klass is incidents.SystemIncidentClass.POSITION_VERIFICATION_UNAVAILABLE
    assert scope is incidents.SystemIncidentScope.KRAKEN_POSITION_VERIFICATION

    # Real transport evidence for private state *is* read-only connectivity.
    connect_klass, connect_scope = incidents.classify_degradation_reason(
        "Kraken account state unavailable: ConnectError: connection refused"
    )
    assert connect_klass is incidents.SystemIncidentClass.KRAKEN_READ_ONLY_CONNECTIVITY
    assert connect_scope is incidents.SystemIncidentScope.KRAKEN_READ_ONLY

    decision = incidents.observe_degradation(
        incident_class=klass,
        scope=scope,
        reason="incomplete position state",
        state_file=tmp_path / "position.json",
    )
    assert decision.consecutive_recovery_failures == 0


# ===========================================================================
# DELIVERY
# ===========================================================================


def test_failed_delivery_does_not_mark_notification_delivered(tmp_path):
    """49. failed Telegram delivery does not mark notification delivered."""

    state = tmp_path / "incidents.json"
    decision = _pricing(state)
    assert decision.action == incidents.ACTION_NOTIFY_OPEN

    # Delivery failed: the reservation is released, never confirmed.
    assert incidents.release_incident_notification(decision=decision, state_file=state)

    row = _row(state, PRICING_KEY)
    assert row is not None
    assert row["opened_notification_at"] is None
    assert row["notification_state"] != incidents.NOTIFICATION_OPEN_NOTIFIED


def test_failed_delivery_does_not_create_duplicate_incident(tmp_path):
    """50. failed Telegram delivery does not create duplicate incident."""

    state = tmp_path / "incidents.json"
    first = _pricing(state)
    incidents.release_incident_notification(decision=first, state_file=state)

    retry = _pricing(state)
    assert retry.incident_key == first.incident_key
    assert retry.incident_id == first.incident_id
    assert len(json.loads(state.read_text())["incidents"]) == 1


def test_bounded_delivery_retry_can_succeed(tmp_path):
    """51. bounded delivery retry can eventually mark notification successful."""

    state = tmp_path / "incidents.json"
    first = _pricing(state)
    incidents.release_incident_notification(decision=first, state_file=state)

    retry = _pricing(state)
    assert retry.action == incidents.ACTION_NOTIFY_OPEN
    assert _deliver(state, retry, message_id=4242) is True

    row = _row(state, PRICING_KEY)
    assert row is not None
    assert row["opened_notification_at"] is not None
    assert row["open_message_id"] == 4242
    assert row["notification_state"] == incidents.NOTIFICATION_OPEN_NOTIFIED


def test_incident_remains_open_if_delivery_failed(tmp_path):
    """52. incident remains open if delivery failed."""

    state = tmp_path / "incidents.json"
    decision = _pricing(state)
    incidents.release_incident_notification(decision=decision, state_file=state)

    assert _row(state, PRICING_KEY)["state"] == incidents.STATE_OPEN


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
# OBSERVABILITY FOR A FUTURE COCKPIT / OPERATIONS VIEW
# ===========================================================================


def test_incident_projection_is_secret_free_and_complete(tmp_path):
    """26. incident data contract supports a read-only Cockpit projection."""

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
        "suppressed_notification_count",
        "notification_delivered",
        "recovered_at",
        "outage_seconds",
        "latest_reason",
        "metadata",
    ):
        assert key in row

    assert row["occurrence_count"] == 2
    assert row["notification_delivered"] is True
    assert row["alert_family"] == "SYSTEM_HEALTH"


def test_open_incidents_can_be_filtered_for_operations_view(tmp_path):
    state = tmp_path / "incidents.json"
    _deliver(state, _pricing(state))
    incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
        scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        state_file=state,
    )

    assert incidents.read_incidents(state_file=state, include_recovered=False) == []
    assert len(incidents.read_incidents(state_file=state)) == 1


def test_incident_contract_fixture_matches_code_constants(tmp_path):
    """The frozen incident contract fixture must match the implementation.

    This keeps the Alert-v2 system-incident contract machine-checkable instead of
    drifting from `app.services.system_incidents`.
    """

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
    assert contract["identity"]["alert_family"] == incidents.SYSTEM_HEALTH_FAMILY
    assert contract["notification_budget"]["open"] == 1
    assert contract["notification_budget"]["recovered"] == 1
    assert contract["connectivity_recovery_rule"]["notify_after_consecutive_failures"] == (
        incidents.CONNECTIVITY_RECOVERY_FAILURES_BEFORE_NOTIFY
    )
    assert contract["connectivity_recovery_rule"]["notify_after_consecutive_failures"] == 7

    # Every documented scope must exist as a real scope constant.
    documented = {
        item.split("SYSTEM_HEALTH:", 1)[1] for item in contract["identity"]["examples"]
    }
    assert documented == {scope.value for scope in incidents.SystemIncidentScope}

    # Every persisted field must be produced by a freshly opened incident row.
    state = tmp_path / "_contract_probe.json"
    decision = incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.HELD_ASSET_PRICING_DEGRADED,
        scope=incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING,
        reason="USD/stable-quote pricing unavailable for held assets: ADA.S",
        state_file=state,
    )
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
    for _ in range(6):
        _connectivity(state)
    _deliver(state, _connectivity(state))

    recovered = incidents.observe_recovery(
        incident_class=incidents.SystemIncidentClass.KRAKEN_CONNECTIVITY_UNAVAILABLE,
        scope=incidents.SystemIncidentScope.KRAKEN_PUBLIC,
        now=NOW + timedelta(minutes=12),
        state_file=state,
    )

    from app.services.alert_v2_format import outage_seconds_between

    duration = outage_seconds_between(recovered.first_seen_at, recovered.recovered_at)
    assert duration == pytest.approx(12 * 60, abs=1)
