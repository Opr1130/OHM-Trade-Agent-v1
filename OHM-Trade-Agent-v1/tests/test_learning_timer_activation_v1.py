"""Regression contract for first-time learning timer activation."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "learning" / "run-gated-learning-deploy.sh"


def test_first_activation_is_one_shot_gated_before_timer_enable():
    source = SCRIPT.read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    activation = source.index("running one-shot activation gates")
    sync_1 = source.index("systemctl start opip-learning-sync.service", activation)
    exact_release = source.index('production_sha" != "$TARGET_SHA"', sync_1)
    capture = source.index("systemctl start opip-learning-capture.service", exact_release)
    capture_gate = source.index('capture_disposition" != "CONSUMED_EMPTY"', capture)
    outcomes = source.index("systemctl start opip-learning-outcomes.service", capture_gate)
    outcomes_gate = source.index('outcomes_disposition" != "CONSUMED_OK"', outcomes)
    sync_2 = source.index("systemctl start opip-learning-sync.service", sync_1 + 1)
    orphan_gate = source.index("orphan learning job containers remain", sync_2)
    enable = source.index('systemctl enable --now "${TIMERS[@]}"', orphan_gate)

    assert activation < sync_1 < exact_release < capture < capture_gate
    assert capture_gate < outcomes < outcomes_gate < sync_2 < orphan_gate < enable
    assert 'retired" != "1"' in source
    assert 'capture_release" != "CURRENT"' in source
    assert 'outcomes_release" != "CURRENT"' in source
    assert "CONSUMED_EMPTY" in source
    assert "CONSUMED_OK" in source


def test_activation_failure_cannot_enable_timers_early():
    source = SCRIPT.read_text(encoding="utf-8")
    enable = source.index('systemctl enable --now "${TIMERS[@]}"')

    for failure in (
        "sync did not prove exact schema-4 production release",
        "capture one-shot was not governed consumed-empty/current",
        "outcomes one-shot was not governed consumed/current",
        "orphan learning job containers remain",
    ):
        pos = source.index(failure)
        assert pos < enable
        assert "exit 75" in source[pos : pos + 500]


def test_activation_remains_measurement_only_without_trading_credentials():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "measurement_only=true" in source
    assert "trade_authority_changed=false" in source
    assert "policy_change_authorized=false" in source
    assert "KRAKEN" not in source
    assert "TELEGRAM" not in source
