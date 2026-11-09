"""ATDD - Early Watch observability and notification-mode control.

Requirement -> acceptance criterion -> focused test -> implementation:

  R-1  Per-phase CPU, RSS, cgroup memory and duration must be measurable, and an
       unmeasurable value must be reported as UNKNOWN rather than fabricated.
       AC-001, AC-002
  R-2  Measurement must be opt-in and must never be able to break a workload.
       AC-003
  R-3  Automatic EARLY_MOVER delivery must be controllable by an explicit,
       fail-closed release configuration - and only that family.
       AC-004, AC-005
  R-4  A suppressed automatic notification must stay auditable.
       AC-006

No test here sends anything: every delivery boundary is a double.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import early_watch_mode as mode
from app.services import phase_resources as resources

APP_ROOT = Path(__file__).resolve().parents[1]
MOVEMENT = APP_ROOT / "app" / "services" / "movement_discovery_v2.py"


# ---------------------------------------------------------------------------
# AC-001 - the four measurements exist and are bounded
# ---------------------------------------------------------------------------


def test_ac_001_a_sample_reports_duration_cpu_rss_and_cgroup_memory():
    sample = resources.PhaseResourceSample(
        phase="EARLY_WATCH",
        duration_ms=1234,
        cpu_ms=987,
        resident_bytes=64 * 1024 * 1024,
        cgroup_current_bytes=128 * 1024 * 1024,
        cgroup_peak_bytes=256 * 1024 * 1024,
    )
    line = sample.evidence_line()
    assert line.startswith("OPIP_PHASE_RESOURCE=EARLY_WATCH ")
    for field in (
        "duration_ms=1234",
        "cpu_ms=987",
        "rss_bytes=67108864",
        "cgroup_memory_current_bytes=134217728",
        "cgroup_memory_peak_bytes=268435456",
    ):
        assert field in line, field
    # One line, no payload, no symbol, no free-form text.
    assert "\n" not in line
    assert " " in line


def test_ac_001_an_unmeasurable_value_is_reported_as_unknown():
    sample = resources.PhaseResourceSample(
        phase="EARLY_WATCH",
        duration_ms=5,
        cpu_ms=1,
        resident_bytes=None,
        cgroup_current_bytes=None,
        cgroup_peak_bytes=None,
    )
    fields = sample.as_fields()
    assert fields["rss_bytes"] == "UNKNOWN"
    assert fields["cgroup_memory_current_bytes"] == "UNKNOWN"
    assert fields["cgroup_memory_peak_bytes"] == "UNKNOWN"
    # Measured values stay measured.
    assert fields["duration_ms"] == "5"


# ---------------------------------------------------------------------------
# AC-002 - the probe is real on this platform and honest where it is not
# ---------------------------------------------------------------------------


def test_ac_002_resident_bytes_is_measured_or_absent_never_invented():
    value = resources.resident_bytes()
    assert value is None or value > 0


def test_ac_002_cgroup_paths_match_the_documented_layout_or_are_absent():
    paths = resources.cgroup_memory_paths()
    if paths is None:
        pytest.skip("cgroup v2 is not available on this platform")
    current, peak = paths
    assert current.name == "memory.current"
    assert peak.name == "memory.peak"
    assert current.parent == peak.parent


# ---------------------------------------------------------------------------
# AC-003 - opt-in, fail-soft
# ---------------------------------------------------------------------------


def test_ac_003_measurement_is_off_unless_explicitly_enabled():
    assert resources.telemetry_enabled({}) is False
    assert resources.telemetry_enabled({"OPIP_PHASE_RESOURCE_TELEMETRY": ""}) is False
    assert resources.telemetry_enabled({"OPIP_PHASE_RESOURCE_TELEMETRY": "0"}) is False
    assert resources.telemetry_enabled({"OPIP_PHASE_RESOURCE_TELEMETRY": "1"}) is True


def test_ac_003_a_disabled_phase_emits_nothing():
    emitted: list[str] = []
    with resources.measure_phase("EARLY_WATCH", emit=emitted.append, env={}):
        pass
    assert emitted == []


def test_ac_003_an_enabled_phase_emits_exactly_one_line():
    emitted: list[str] = []
    with resources.measure_phase(
        "EARLY_WATCH",
        emit=lambda line, **_: emitted.append(line),
        env={"OPIP_PHASE_RESOURCE_TELEMETRY": "1"},
    ):
        pass
    assert len(emitted) == 1
    assert emitted[0].startswith("OPIP_PHASE_RESOURCE=EARLY_WATCH ")


def test_ac_003_a_failing_phase_still_emits_and_the_error_propagates():
    emitted: list[str] = []
    with pytest.raises(RuntimeError):
        with resources.measure_phase(
            "EARLY_WATCH",
            emit=lambda line, **_: emitted.append(line),
            env={"OPIP_PHASE_RESOURCE_TELEMETRY": "1"},
        ):
            raise RuntimeError("phase failed")
    assert len(emitted) == 1, "an overrun that ends in an error is still attributable"


def test_ac_003_a_broken_emitter_cannot_break_the_workload():
    def explode(_line, **__):
        raise OSError("log sink gone")

    with resources.measure_phase(
        "EARLY_WATCH",
        emit=explode,
        env={"OPIP_PHASE_RESOURCE_TELEMETRY": "1"},
    ):
        pass


# ---------------------------------------------------------------------------
# AC-004 - the notification mode is explicit and fails closed
# ---------------------------------------------------------------------------


def test_ac_004_the_default_mode_is_todays_behaviour():
    assert mode.resolve_early_watch_mode({}) == mode.MODE_ACTIVE
    assert mode.automatic_notifications_enabled({}) is True


def test_ac_004_evidence_only_is_honoured_and_is_the_only_alternative():
    assert (
        mode.resolve_early_watch_mode({mode.MODE_ENV: "evidence_only"})
        == mode.MODE_EVIDENCE_ONLY
    )
    assert mode.automatic_notifications_enabled(
        {mode.MODE_ENV: "EVIDENCE_ONLY"}
    ) is False
    for unknown in ("", "off", "quiet", "evidence-only", "1"):
        assert (
            mode.resolve_early_watch_mode({mode.MODE_ENV: unknown})
            == mode.MODE_ACTIVE
        ), unknown


def test_ac_004_the_reason_codes_stay_separable():
    """A mode suppression must not be confused with a policy/eligibility one."""
    assert mode.MODE_ACTIVE in mode.ALLOWED_MODES
    assert mode.MODE_EVIDENCE_ONLY in mode.ALLOWED_MODES
    source = MOVEMENT.read_text(encoding="utf-8")
    assert '"NOTIFICATION_POLICY"' in source
    assert '"ALERT_NOT_ELIGIBLE"' in source
    assert "EVIDENCE_ONLY_SUPPRESSION_REASON" in source
    assert 'EVIDENCE_ONLY_SUPPRESSION_REASON = "EARLY_WATCH_EVIDENCE_ONLY"' in source


# ---------------------------------------------------------------------------
# AC-005 - only the automatic EARLY_MOVER family is affected
# ---------------------------------------------------------------------------


def test_ac_005_the_switch_is_only_consulted_at_the_early_mover_boundary():
    """AC-005: critical safety, qualified entry and COMMAND_WATCH are separate."""
    source = MOVEMENT.read_text(encoding="utf-8")
    start = source.index("def send_early_mover_update(")
    end = len(source)
    boundary = source[start:end]
    assert "automatic_notifications_enabled()" in boundary
    # The suppression sits after the eligibility and policy gates, so their
    # reasons keep their meaning.
    assert boundary.index("ALERT_NOT_ELIGIBLE") < boundary.index(
        "automatic_notifications_enabled()"
    )
    assert boundary.index("NOTIFICATION_POLICY") < boundary.index(
        "automatic_notifications_enabled()"
    )
    # Families outside Early Watch never consult the mode.
    for path in (
        APP_ROOT / "app" / "services" / "chief_alert_notifier.py",
        APP_ROOT / "app" / "services" / "qualified_alert_outbox.py",
        APP_ROOT / "app" / "services" / "telegram_command_center.py",
        APP_ROOT / "app" / "services" / "emergency_alert_notifier.py",
    ):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "OPIP_EARLY_WATCH_MODE" not in text, path.name
        assert "early_watch_mode" not in text, path.name


# ---------------------------------------------------------------------------
# AC-006 - a suppressed automatic notification stays auditable
# ---------------------------------------------------------------------------


def test_ac_006_suppression_evidence_names_the_identity_and_mode():
    line = mode.suppression_evidence_line(
        "EARLY_MOVER:SOLUSD", mode=mode.MODE_EVIDENCE_ONLY
    )
    assert line == "OPIP_EARLY_WATCH_SUPPRESSED=EARLY_MOVER:SOLUSD mode=evidence_only"
    assert "\n" not in line
    # Withheld under the configured mode, the marker names that mode.
    resolved = mode.suppression_evidence_line(
        "EARLY_MOVER:SOLUSD", environ={mode.MODE_ENV: "evidence_only"}
    )
    assert resolved.endswith("mode=evidence_only")
