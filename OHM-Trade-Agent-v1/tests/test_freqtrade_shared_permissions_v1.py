from __future__ import annotations

import os
import stat

import pytest

from app.services.freqtrade_signal_bridge import (
    cancel_admitted_signals,
    ensure_bridge_files,
)
from app.services.paper_trade_control import set_paper_trade_enabled
from app.services.registry_io import save_json_atomic


pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX file modes only")


def _mode(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_atomic_shared_file_mode_survives_replacement(tmp_path):
    path = tmp_path / "shared.json"
    save_json_atomic(path, {"value": 1}, mode=0o644)
    assert _mode(path) == 0o644

    path.chmod(0o600)
    save_json_atomic(path, {"value": 2}, mode=0o644)
    assert _mode(path) == 0o644


def test_freqtrade_bridge_files_are_created_world_readable(tmp_path):
    signals = tmp_path / "bridge" / "signals.json"
    usd = tmp_path / "bridge" / "pairlist_usd.json"
    usdt = tmp_path / "bridge" / "pairlist_usdt.json"

    ensure_bridge_files(
        signals_file=signals,
        pairlist_usd_file=usd,
        pairlist_usdt_file=usdt,
    )

    assert _mode(signals) == 0o644
    assert _mode(usd) == 0o644
    assert _mode(usdt) == 0o644


def test_bridge_rewrite_and_control_rewrite_restore_shared_mode(tmp_path):
    signals = tmp_path / "bridge" / "signals.json"
    save_json_atomic(
        signals,
        {
            "schema_version": 1,
            "signals": [
                {
                    "signal_id": "OHM:test",
                    "admission_status": "ADMITTED",
                }
            ],
        },
    )
    signals.chmod(0o600)

    changed = cancel_admitted_signals(
        cancelled_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        signals_file=signals,
    )
    assert changed == 1
    assert _mode(signals) == 0o644

    control = tmp_path / "control" / "control.json"
    set_paper_trade_enabled(False, updated_by="TEST", path=control)
    assert _mode(control) == 0o644
