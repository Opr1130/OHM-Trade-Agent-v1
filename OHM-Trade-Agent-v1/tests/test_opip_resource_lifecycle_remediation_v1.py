"""ATDD - runtime containment and bounded caching.

Requirement -> acceptance criterion -> focused test -> implementation:

  R-1  A cycle workload must be bounded where the process actually lives, so it
       cannot outlive its authorization and orphan the cycle lock.
       AC-001 (in-container bound), AC-002 (release derivation intact)
  R-2  A stopped cycle must shut down in order and leave durable, honest
       evidence - and must never claim a completion it did not reach.
       AC-003 (termination evidence), AC-004 (survives fail-open boundaries)
  R-3  Transport caching must have a footprint bound, not only a freshness
       bound, and must report what it evicted.
       AC-006 (entry cap/LRU), AC-007 (byte cap), AC-008 (semantics preserved)
  R-4  Repairing containment must not widen any authority.
       AC-009
  R-5  The authorized bound must have exactly one authoritative source, and
       reading it must depend on the scheduled command, not on prose.
       AC-010 (comment-insensitive derivation)
  R-6  Non-critical work must not be able to occupy the whole bound and starve
       the next protection pass.
       AC-011 (cycle budget gate)
  R-7  An overdue or orphaned cycle must be detectable from existing
       read-only supervision.
       AC-012 (overdue / orphan assessment)

Run: python -m pytest tests/test_opip_resource_lifecycle_remediation_v1.py -q
"""

from __future__ import annotations

import re
import signal
from pathlib import Path

import pytest

from app.jobs import run_cycle as rc
from app.services import kraken_transport as kt
from app.services.kraken_transport import KrakenPublicTransport

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
CRON_ENTRY = APP_ROOT / "deploy" / "cron.d" / "ohm-unified-cycle"
DEPLOY = APP_ROOT / "deploy" / "remote" / "ohm-deploy"

#: The release controller's derivation expression, verbatim from
#: ``scheduler_hard_bound_seconds`` and from the learning diagnostics script.
DERIVATION = re.compile(r"timeout --signal=TERM --kill-after=[0-9]+s [0-9]+")
#: The in-container bound, deliberately written with the short flags.
IN_CONTAINER_BOUND = re.compile(r"timeout -s TERM -k ([0-9]+)s ([0-9]+)")


def _cron_text() -> str:
    return CRON_ENTRY.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# AC-001 / AC-002 - scheduler containment
# ---------------------------------------------------------------------------


def test_ac_001_the_container_side_process_is_bounded_not_only_the_launcher():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-001: the bound is enforced INSIDE the container, on the real process.

    A host-side ``timeout`` around ``docker compose exec`` signals the Compose
    CLIENT. The exec'd Python process is a child of the container's exec runtime,
    not of the client, so the client's death cannot bound it: it kept running,
    holding ``/app/data/.unified_cycle.lock``, and every following minute was
    skipped as lock contention. The bound therefore has to exist on the process
    the container actually owns.
    """
    text = _cron_text()
    matches = IN_CONTAINER_BOUND.findall(text)
    assert len(matches) == 1, "exactly one in-container bound may be declared"
    kill_after_seconds, bound_seconds = (int(value) for value in matches[0])
    assert bound_seconds > 0
    assert kill_after_seconds >= 1, "an unkillable escalation is not a bound"


def test_ac_001_the_container_bound_is_strictly_inside_the_release_bound():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-001: the inner bound must resolve before the outer one is reaped."""
    text = _cron_text()
    outer = DERIVATION.findall(text)
    assert len(outer) == 1
    outer_seconds = int(outer[0].split()[-1])
    inner_seconds = int(IN_CONTAINER_BOUND.findall(text)[0][1])
    assert inner_seconds < outer_seconds
    # A meaningful containment margin: the container must reach its own deadline
    # with time to shut down, not exactly when the launcher is already gone.
    assert outer_seconds - inner_seconds >= 30


def test_ac_001_a_missing_timeout_binary_fails_closed_instead_of_running_unbounded():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-001: absent bound tooling must not degrade into an unbounded cycle.

    An unbound cycle IS the defect this entry exists to prevent, so falling
    through to a plain ``exec`` trades a visible outage for a silent one: the
    workload can outlive every bound, hold ``/app/data/.unified_cycle.lock`` and
    stop protection indefinitely. Both the host launcher and the container
    wrapper therefore refuse to start the workload, emit a terminal marker that
    claims neither SUCCESS nor DEGRADED, emit no COMPLETED_AT, and exit with a
    distinct code (98 host / 97 container).
    """
    text = _cron_text()
    assert text.count("command -v timeout") == 2, "both layers must guard"
    assert "exit 98" in text and "exit 97" in text
    # The guard must be fail-closed: no path may reach an unguarded exec.
    assert not re.search(r";\s*exec python -m app\.jobs\.run_cycle", text)
    assert re.search(
        r"OPIP_UNIFIED_CYCLE_STATUS=CONTAINMENT_UNAVAILABLE", text
    ), "the fail-closed path must name its own cause"


def test_ac_001_the_fail_closed_marker_cannot_be_read_as_a_completion():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-001: the release controller accepts only SUCCESS|DEGRADED.

    The fail-closed status is deliberately outside that set and carries no
    COMPLETED_AT, so a cycle that never ran can never be observed as a fresh
    completion - the same rule that keeps a bound-killed cycle honest.
    """
    text = _cron_text()
    statuses = set(re.findall(r"OPIP_UNIFIED_CYCLE_STATUS=([A-Z_]+)", text))
    assert statuses == {"CONTAINMENT_UNAVAILABLE"}
    assert "OPIP_UNIFIED_CYCLE_COMPLETED_AT=" not in text


def test_ac_002_release_derivation_still_resolves_exactly_one_bound():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-002: the release controller must keep deriving the HOST bound.

    ``scheduler_hard_bound_seconds`` pipes ``grep -m1 -oE`` into
    ``grep -oE '[0-9]+$'``. ``grep -m1`` stops after one matching *line*, not one
    match, so two matches on the same line yield two numbers and the function
    fails closed - which would fail every deploy. The in-container bound is
    therefore spelled with the short flags, and no comment may reproduce the long
    form either: a comment match would silently become the derived authority.
    """
    text = _cron_text()
    assert len(DERIVATION.findall(text)) == 1
    program_lines = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert len(DERIVATION.findall("\n".join(program_lines))) == 1


def test_ac_002_the_real_derivation_function_returns_the_release_bound(tmp_path):
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-002: execute the controller's own extractor against the real entry."""
    import shutil
    import subprocess

    staged = tmp_path / "ohm-unified-cycle"
    staged.write_text(_cron_text(), encoding="utf-8")

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available in this environment")
    deploy = DEPLOY.read_text(encoding="utf-8")
    start = deploy.index("scheduler_hard_bound_seconds() {")
    end = deploy.index("observe_unified_cycle() {", start)
    function = deploy[start:end].rstrip()
    script = (
        "export OPIP_DEPLOY_TEST_SEAMS=1\n"
        f"{function}\n"
        f"OPIP_RELEASE_SCHEDULER_ENTRY='{staged.as_posix()}' "
        "scheduler_hard_bound_seconds\n"
    )
    proc = subprocess.run([bash, "-c", script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == DERIVATION.findall(_cron_text())[0].split()[-1]


def test_ac_002_the_entry_is_still_one_canonical_scheduler():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-002: containment must not introduce a second scheduler."""
    jobs = [
        line
        for line in _cron_text().splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and "=" not in line.split(None, 1)[0]
    ]
    assert len(jobs) == 1
    assert jobs[0].startswith("* * * * * root")
    assert "flock -n /var/run/ohm-unified-cycle.lock" in jobs[0]


# ---------------------------------------------------------------------------
# AC-003 / AC-004 - orderly shutdown and honest terminal evidence
# ---------------------------------------------------------------------------


def _isolate_cycle(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "CYCLE_LOCK_FILE", tmp_path / ".unified_cycle.lock")
    monkeypatch.setattr(rc, "recover_interrupted_search", lambda: False)


def test_ac_003_a_shutdown_signal_terminates_with_durable_evidence(
    monkeypatch, tmp_path, capsys
):
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-003: a bounded cycle records that it was stopped, and releases its lock."""
    _isolate_cycle(monkeypatch, tmp_path)

    def body() -> bool:
        rc._handle_shutdown_signal(signal.SIGTERM, None)
        return True

    monkeypatch.setattr(rc, "_run_cycle_once", body)
    rc.main()
    out = capsys.readouterr().out

    assert "OPIP_UNIFIED_CYCLE_PHASE=SHUTDOWN_SIGNAL_SIGTERM" in out
    assert "OPIP_UNIFIED_CYCLE_STATUS=TERMINATED" in out
    assert "OPIP_UNIFIED_CYCLE_TERMINATED_AT=" in out
    assert "OPIP_UNIFIED_CYCLE_TERMINATED_REASON=SIGTERM" in out
    # The next minute's cycle must find the lock free: ``finally`` released it.
    with rc.registry_lock(rc.CYCLE_LOCK_FILE, timeout=0.0):
        pass


def test_ac_003_a_terminated_cycle_never_claims_a_completion(
    monkeypatch, tmp_path, capsys
):
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-003: TERMINATED must not fabricate SUCCESS/DEGRADED or COMPLETED_AT.

    The release controller pairs the newest ``COMPLETED_AT`` with the newest
    ``STATUS=(SUCCESS|DEGRADED)`` it can find. A terminated cycle that published
    COMPLETED_AT would bind a fresh timestamp to a stale SUCCESS and manufacture
    a healthy-completion receipt out of a cycle that was killed.
    """
    _isolate_cycle(monkeypatch, tmp_path)
    monkeypatch.setattr(
        rc, "_run_cycle_once", lambda: rc._handle_shutdown_signal(signal.SIGTERM, None)
    )
    rc.main()
    out = capsys.readouterr().out
    assert "OPIP_UNIFIED_CYCLE_COMPLETED_AT=" not in out
    assert "OPIP_UNIFIED_CYCLE_STATUS=SUCCESS" not in out
    assert "OPIP_UNIFIED_CYCLE_STATUS=DEGRADED" not in out


def test_ac_003_a_completed_cycle_still_emits_the_completion_pair(
    monkeypatch, tmp_path, capsys
):
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-003: the unchanged path keeps its exact release-facing contract."""
    _isolate_cycle(monkeypatch, tmp_path)
    monkeypatch.setattr(rc, "_run_cycle_once", lambda: True)
    rc.main()
    out = capsys.readouterr().out
    assert "OPIP_UNIFIED_CYCLE_STATUS=SUCCESS" in out
    assert "OPIP_UNIFIED_CYCLE_COMPLETED_AT=" in out
    assert "OPIP_UNIFIED_CYCLE_STATUS=TERMINATED" not in out


def test_ac_004_a_shutdown_cannot_be_swallowed_by_a_phase_fail_open_boundary(
    monkeypatch, tmp_path, capsys
):
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-004: every workload phase is wrapped in ``except Exception``.

    A shutdown request is not a phase failure. If it were an ``Exception``
    subclass, the first fail-open boundary would capture it and the cycle would
    carry on starting workloads instead of stopping.
    """
    assert issubclass(rc._CycleTerminated, BaseException)
    assert not issubclass(rc._CycleTerminated, Exception)

    _isolate_cycle(monkeypatch, tmp_path)
    swallowed: list[str] = []

    def body() -> bool:
        try:
            rc._handle_shutdown_signal(signal.SIGTERM, None)
        except Exception:  # the shape of every phase's fail-open boundary
            swallowed.append("swallowed")
        return True

    monkeypatch.setattr(rc, "_run_cycle_once", body)
    rc.main()
    out = capsys.readouterr().out
    assert swallowed == []
    assert "OPIP_UNIFIED_CYCLE_STATUS=TERMINATED" in out


def test_ac_004_handlers_are_restored_after_the_cycle(monkeypatch, tmp_path):
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-004: installing the handlers must not leak into the caller's process."""
    _isolate_cycle(monkeypatch, tmp_path)
    monkeypatch.setattr(rc, "_run_cycle_once", lambda: True)
    sigterm = getattr(signal, "SIGTERM", None)
    if sigterm is None:
        pytest.skip("SIGTERM is unavailable")
    before = signal.getsignal(sigterm)
    rc.main()
    assert signal.getsignal(sigterm) == before


def test_ac_004_lock_contention_is_still_a_skip_not_a_completion(
    monkeypatch, tmp_path, capsys
):
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-004: the unchanged contention path must not look like a completion."""
    _isolate_cycle(monkeypatch, tmp_path)

    class _BusyLock:
        def __enter__(self):
            raise TimeoutError("previous cycle still running")

        def __exit__(self, *exc):  # pragma: no cover - never reached
            return False

    monkeypatch.setattr(rc, "registry_lock", lambda *a, **k: _BusyLock())
    monkeypatch.setattr(rc, "_run_cycle_once", lambda: pytest.fail("must not run"))
    rc.main()
    out = capsys.readouterr().out
    assert "OPIP_UNIFIED_CYCLE_PHASE=LOCK_CONTENTION_SKIPPED" in out
    assert "OPIP_UNIFIED_CYCLE_COMPLETED_AT=" not in out
    assert "OPIP_UNIFIED_CYCLE_STATUS=" not in out


# ---------------------------------------------------------------------------
# AC-006 / AC-007 / AC-008 - bounded transport cache
# ---------------------------------------------------------------------------


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise kt.httpx.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses=None, payload=None):
        self._responses = list(responses or [])
        self._payload = payload
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if not self._responses:
            if self._payload is not None:
                return _Response({"error": [], "result": self._payload})
            return _Response({"error": [], "result": {"k": {"n": self.calls}}})
        return self._responses.pop(0)

    def close(self):
        pass


def _transport(**kwargs) -> KrakenPublicTransport:
    return KrakenPublicTransport(
        requests_per_second=1000, burst=10, max_retries=0, **kwargs
    )


def test_ac_006_the_entry_cap_bounds_the_cache_regardless_of_history():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-006: a TTL alone never bounded retention; the entry cap does.

    ``_store`` used to insert unconditionally and only a read of the *same* key
    after expiry removed anything, so a process retained one entry per distinct
    (endpoint, params) pair it had ever requested.
    """
    transport = _transport(max_cache_entries=4)
    transport._client = _Client()
    for index in range(20):
        transport.request("Ticker", {"pair": f"PAIR{index}"}, timeout_seconds=1)

    snapshot = transport.telemetry_snapshot()
    assert snapshot["cache_entries"] == 4
    assert snapshot["configured_cache_max_entries"] == 4
    assert snapshot["cache_evictions"] == 16
    assert snapshot[f"cache_evictions_{kt.EVICTION_SIZE}"] == 16


def test_ac_006_eviction_is_least_recently_used_and_a_hit_refreshes_recency():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-006: the cap must not destroy the repeated-read locality it exists for."""
    transport = _transport(max_cache_entries=2)
    transport._client = _Client()
    for pair in ("AAA", "BBB"):
        transport.request("Ticker", {"pair": pair}, timeout_seconds=1)
    # Re-read AAA (a hit), then insert a third distinct key.
    transport.request("Ticker", {"pair": "AAA"}, timeout_seconds=1)
    hits_before = transport.telemetry_snapshot()["cache_hits"]
    transport.request("Ticker", {"pair": "CCC"}, timeout_seconds=1)
    transport.request("Ticker", {"pair": "AAA"}, timeout_seconds=1)

    snapshot = transport.telemetry_snapshot()
    assert snapshot["cache_entries"] == 2
    assert snapshot["cache_hits"] == hits_before + 1, "the refreshed entry survived"
    assert snapshot[f"cache_evictions_{kt.EVICTION_SIZE}"] == 1


def test_ac_006_expired_entries_are_swept_before_live_ones(monkeypatch):
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-006: an expired entry can never be served, so it is evicted first."""
    clock = {"now": 1_000.0}
    monkeypatch.setattr(kt.time, "monotonic", lambda: clock["now"])
    transport = _transport(max_cache_entries=2)
    transport._client = _Client()
    transport._store("k:old", {"value": 1}, ttl=5.0)
    transport._store("k:new", {"value": 2}, ttl=600.0)
    clock["now"] += 10.0
    transport._store("k:fresh", {"value": 3}, ttl=600.0)

    snapshot = transport.telemetry_snapshot()
    assert "k:old" not in transport._cache
    assert snapshot[f"cache_evictions_{kt.EVICTION_TTL_SWEEP}"] == 1
    assert snapshot["cache_entries"] == 2


def test_ac_007_the_byte_cap_bounds_a_small_number_of_large_payloads():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-007: an entry cap alone does not bound memory - payloads vary in size."""
    transport = _transport(max_cache_entries=1000, max_cache_bytes=4096)
    transport._client = _Client(payload={"k": {"pad": "x" * 1000}})
    for index in range(20):
        transport.request(
            "OHLC", {"pair": f"PAIR{index}"}, timeout_seconds=1
        )

    snapshot = transport.telemetry_snapshot()
    assert snapshot["cache_bytes"] <= 4096
    assert snapshot["configured_cache_max_bytes"] == 4096
    assert snapshot["cache_entries"] < 20


def test_ac_007_replacing_a_key_does_not_double_count_its_footprint():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-007: refresh-in-place must keep the accounting exact."""
    transport = _transport(max_cache_entries=1000, max_cache_bytes=1_000_000)
    transport._store("k", {"payload": "a" * 100}, ttl=600.0)
    first = transport.telemetry_snapshot()["cache_bytes"]
    transport._store("k", {"payload": "a" * 100}, ttl=600.0)
    assert transport.telemetry_snapshot()["cache_bytes"] == first
    assert transport.telemetry_snapshot()["cache_entries"] == 1


def test_ac_008_cache_semantics_are_unchanged():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-008: bounded retention must not change cache correctness."""
    transport = _transport()
    transport._client = _Client()
    first = transport.request("Ticker", {"pair": "XBTUSD"}, timeout_seconds=1)
    first["mutated"] = True  # a caller mutating its copy must not poison the cache
    second = transport.request("Ticker", {"pair": "XBTUSD"}, timeout_seconds=1)

    assert "mutated" not in second
    assert transport.telemetry_snapshot()["cache_hits"] == 1
    assert transport._client.calls == 1


def test_ac_008_ttl_and_clear_still_behave(monkeypatch):
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-008: TTL expiry and explicit clearing keep their existing meaning."""
    clock = {"now": 500.0}
    monkeypatch.setattr(kt.time, "monotonic", lambda: clock["now"])
    transport = _transport()
    transport._client = _Client()
    transport.request("Ticker", {"pair": "XBTUSD"}, timeout_seconds=1)
    clock["now"] += transport.DEFAULT_TTLS["Ticker"] + 1
    transport.request("Ticker", {"pair": "XBTUSD"}, timeout_seconds=1)
    assert transport._client.calls == 2

    transport.clear_cache()
    snapshot = transport.telemetry_snapshot()
    assert snapshot["cache_entries"] == 0
    assert snapshot["cache_bytes"] == 0


def test_ac_008_defaults_are_bounded_and_overridable(monkeypatch):
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-008: the shipped defaults must be finite and the caps configurable."""
    default = _transport()
    assert default.max_cache_entries == kt.DEFAULT_CACHE_MAX_ENTRIES
    assert default.max_cache_bytes == kt.DEFAULT_CACHE_MAX_BYTES
    assert 0 < kt.DEFAULT_CACHE_MAX_BYTES <= 64 * 1024 * 1024

    monkeypatch.setenv("KRAKEN_CACHE_MAX_ENTRIES", "7")
    monkeypatch.setenv("KRAKEN_CACHE_MAX_BYTES", "8192")
    configured = KrakenPublicTransport(requests_per_second=1, burst=1, max_retries=0)
    assert configured.max_cache_entries == 7
    assert configured.max_cache_bytes == 8192


# ---------------------------------------------------------------------------
# AC-009 - no authority widening
# ---------------------------------------------------------------------------


def test_ac_009_containment_grants_no_trading_learning_or_paper_authority():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-009: this increment touches resource lifetime only."""
    cron = _cron_text()
    for forbidden in (
        "OPIP_PAPER_V2_MODE=on",
        "FUNDED_TRADING",
        "KRAKEN_API_KEY",
        "systemctl",
    ):
        assert forbidden not in cron

    transport_source = (APP_ROOT / "app" / "services" / "kraken_transport.py").read_text(
        encoding="utf-8"
    )
    for forbidden in ("/0/private/", "AddOrder", "CancelOrder", "FUNDED"):
        assert forbidden not in transport_source


# ---------------------------------------------------------------------------
# AC-010 - one authoritative bound, insensitive to prose
# ---------------------------------------------------------------------------

DIAGNOSE = APP_ROOT / "deploy" / "remote" / "diagnose-opip-learning.sh"


def test_ac_010_neither_reader_matches_the_entry_verbatim():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-010: a comment must never be able to supply or override the bound.

    Both readers used to run the derivation expression over the whole file, so
    a comment quoting the long-form flags became the authority: it either
    replaced the real bound or produced two matches and failed every deploy.
    Both readers must strip comments and cron.d assignments first.
    """
    for path in (DEPLOY, DIAGNOSE):
        source = path.read_text(encoding="utf-8")
        assert "/^[[:space:]]*#/ {next}" in source, path.name
        assert "/^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=/ {next}" in source, path.name
    # The controller also validates that the bound belongs to the cycle command.
    deploy = DEPLOY.read_text(encoding="utf-8")
    assert "python -m app\\.jobs\\.run_cycle" in deploy


def test_ac_010_the_controller_derives_the_real_bound_past_a_decoy_comment(tmp_path):
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-010: execute the real derivation against an entry with decoy prose.

    The fixture reproduces the shape a contributor is most likely to write: a
    documentation comment that quotes the long-form flags with a different
    number, above the one job line that actually carries the bound.
    """
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available in this environment")

    decoy = tmp_path / "decoy.cron"
    decoy.write_text(
        "# The launcher runs: timeout --signal=TERM --kill-after=30s 7200 cycle\n"
        "SHELL=/bin/bash\n"
        "* * * * * root flock -n /var/run/ohm-unified-cycle.lock -c "
        "'cd /opt/OHM-Trade-Agent-v1 && "
        "timeout --signal=TERM --kill-after=30s 3600 docker compose exec -T "
        "ohm-trade-agent python -m app.jobs.run_cycle'\n",
        encoding="utf-8",
    )
    comment_only = tmp_path / "comment_only.cron"
    comment_only.write_text(
        "# timeout --signal=TERM --kill-after=30s 3600\n"
        "* * * * * root docker compose exec -T ohm-trade-agent "
        "python -m app.jobs.run_cycle\n",
        encoding="utf-8",
    )

    deploy = DEPLOY.read_text(encoding="utf-8")
    start = deploy.index("scheduler_hard_bound_seconds() {")
    end = deploy.index("observe_unified_cycle() {", start)
    function = deploy[start:end].rstrip()
    script = (
        "set -Eeuo pipefail\n"
        "export OPIP_DEPLOY_TEST_SEAMS=1\n"
        f"{function}\n"
        f"echo \"$(OPIP_RELEASE_SCHEDULER_ENTRY='{decoy.as_posix()}' "
        "scheduler_hard_bound_seconds)\"\n"
        f"if OPIP_RELEASE_SCHEDULER_ENTRY='{comment_only.as_posix()}' "
        "scheduler_hard_bound_seconds; then echo UNEXPECTED; exit 9; else echo CLOSED; fi\n"
    )
    proc = subprocess.run([bash, "-c", script], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert "3600" in proc.stdout.split()
    assert "7200" not in proc.stdout
    assert "CLOSED" in proc.stdout



# ---------------------------------------------------------------------------
# AC-011 - non-critical work cannot starve protection
# ---------------------------------------------------------------------------


def _budget(seconds, clock):
    return rc._CycleBudget(seconds, clock=clock)


def test_ac_011_the_budget_gates_only_noncritical_phases():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-011: protection phases are never gated, whatever the elapsed time."""
    now = {"t": 0.0}
    budget = _budget(60, lambda: now["t"])
    now["t"] = 600.0
    assert budget.exhausted() is True
    for phase in rc.NONCRITICAL_PHASES:
        assert budget.allows(phase) is False, phase
    for phase in (
        "KRAKEN_RECONCILIATION",
        "ACTIVE_POSITION_PROTECTION",
        "QUALIFIED_ALERT_RETRY",
        "BROAD_DISCOVERY",
        "ENTRY_WATCH_RECHECK",
        "PENDING_SETUP_MONITOR",
        "EARLY_WATCH",
        "PAPER_MONITOR",
        "EXTERNAL_ORDER_REVIEW",
    ):
        assert budget.allows(phase) is True, phase


def test_ac_011_a_zero_budget_disables_the_gate_and_bad_values_never_open_it(monkeypatch):
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-011: the gate's own fallback must never be 'unlimited'."""
    now = {"t": 0.0}
    budget = _budget(0, lambda: now["t"])
    now["t"] = 1_000_000.0
    for phase in rc.NONCRITICAL_PHASES:
        assert budget.allows(phase) is True
    assert budget.exhausted() is False

    monkeypatch.setenv(rc._CYCLE_BUDGET_ENV, "not-a-number")
    assert rc._cycle_budget_seconds() == rc.DEFAULT_CYCLE_BUDGET_SECONDS
    monkeypatch.setenv(rc._CYCLE_BUDGET_ENV, "")
    assert rc._cycle_budget_seconds() == rc.DEFAULT_CYCLE_BUDGET_SECONDS
    monkeypatch.setenv(rc._CYCLE_BUDGET_ENV, "-5")
    assert rc._cycle_budget_seconds() == 0


def test_ac_011_the_default_budget_expires_well_inside_the_container_bound():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-011: the gate must be able to act before the hard bound does."""
    text = _cron_text()
    inner = int(IN_CONTAINER_BOUND.findall(text)[0][1])
    assert 0 < rc.DEFAULT_CYCLE_BUDGET_SECONDS < inner


def test_ac_011_every_noncritical_phase_is_actually_gated_in_the_cycle_body():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-011: the gate must be wired, not merely defined."""
    import inspect

    body = inspect.getsource(rc._run_cycle_once)
    for phase in rc.NONCRITICAL_PHASES:
        assert f'budget.allows("{phase}")' in body, phase
        assert f'budget.record_skip("{phase}")' in body, phase
    # A skipped phase is a DEGRADED cycle, never a clean SUCCESS.
    assert "return not budget.skipped" in body


# ---------------------------------------------------------------------------
# AC-012 - overdue / orphan is detectable from existing read-only supervision
# ---------------------------------------------------------------------------


def test_ac_012_the_assessment_reports_overdue_and_orphan_from_existing_state():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-012: the two failure modes must be visible in the diagnostics receipt."""
    source = DIAGNOSE.read_text(encoding="utf-8")
    for field in (
        "unified_cycle_lock_state=",
        "unified_cycle_holder_elapsed_seconds=",
        "unified_cycle_holder_overdue=",
        "unified_cycle_in_flight=",
        "unified_cycle_orphan_suspected=",
    ):
        assert field in source, field
    # It reuses the existing lock probe and owner lookup; it starts nothing,
    # signals nothing, writes nothing and takes no lock of its own.
    start = source.index("# Overdue and orphan assessment (read-only).")
    end = source.index('echo "OPIP_UNIFIED_CYCLE_END"', start)
    assessment = source[start:end]
    for forbidden in (
        "docker",
        "systemctl",
        "crontab",
        "run_cycle",
        "rm ",
        "kill ",
        "pkill",
        "flock",
        "touch ",
    ):
        assert forbidden not in assessment, forbidden
    assert "UNKNOWN" in assessment, "an absent input must not become a guess"


def test_ac_012_the_probe_verdict_is_available_to_the_assessment():
    """ATDD-OPIP-RESOURCE-LIFECYCLE-REMEDIATION-v1/AC-012: the earlier read-only probe must hand its verdict forward."""
    source = DIAGNOSE.read_text(encoding="utf-8")
    assert 'UNIFIED_CYCLE_HOST_LOCK_STATE="IDLE"' in source
    assert 'UNIFIED_CYCLE_HOST_LOCK_STATE="HELD"' in source
    assert 'UNIFIED_CYCLE_HOST_LOCK_STATE="ABSENT"' in source

