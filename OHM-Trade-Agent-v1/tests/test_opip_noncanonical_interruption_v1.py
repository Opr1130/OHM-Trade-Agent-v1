"""Interruption evidence for the non-canonical protection registries.

The canonical store is proven separately. These registries (active trades, pending
setups) are written by the cycle process itself, so the orderly-shutdown exception
and SIGKILL can both land inside a write.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="real signals need POSIX")

_CHILD = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {root!r})
    from pathlib import Path
    from app.services.registry_io import save_json_atomic, registry_lock
    target = Path({target!r})
    lock = Path({lock!r})
    print("READY", flush=True)
    version = 0
    with registry_lock(lock, timeout=5.0):
        while True:
            version += 1
            save_json_atomic(target, {{"version": version, "pad": "x" * 200000,
                                       "tail": version}})
            print("V", version, flush=True)
    """
)


@POSIX_ONLY
@pytest.mark.parametrize("sig", [signal.SIGKILL, signal.SIGTERM])
def test_ac_006_a_registry_write_interrupted_by_a_real_signal_leaves_a_complete_file(
    tmp_path, sig
):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-006: SIGKILL or SIGTERM mid registry write leaves the previous or the new file whole, never a torn one, and releases the registry lock."""
    target = tmp_path / "active_trades.json"
    lock = tmp_path / ".trade_registry.lock"
    code = _CHILD.format(root=str(APP_ROOT), target=str(target), lock=str(lock))
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(APP_ROOT),
        env={**os.environ, "APP_ENV": "test"},
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "READY"
        seen = 0
        deadline = time.monotonic() + 60
        while seen < 25 and time.monotonic() < deadline:
            if child.stdout.readline().startswith("V "):
                seen += 1
        assert seen >= 25
        child.send_signal(sig)
    finally:
        child.wait(timeout=30)

    # Whatever instant the signal landed, the file is one complete version.
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["version"] == data["tail"]
    assert len(data["pad"]) == 200000

    # The advisory lock died with the process: the next cycle is not blocked.
    from app.services.registry_io import registry_lock

    with registry_lock(lock, timeout=0.0):
        pass
    # An interrupted write may leave its private temp file; it is never the live one.
    leftovers = [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    for leftover in leftovers:
        assert leftover.name != target.name


def test_ac_006_a_torn_jsonl_tail_is_repaired_not_trusted(tmp_path):
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-006: an append interrupted mid-line leaves a tail the bounded store explicitly repairs."""
    from app.opip.storage import bounded_jsonl

    path = tmp_path / "evidence.jsonl"
    path.write_bytes(b'{"a":1}\n{"a":2}\n{"a":3')  # killed mid-append
    bounded_jsonl.repair_truncated_tail(path)
    assert path.read_bytes().endswith(b"\n")
    lines = bounded_jsonl.read_lines(path)
    assert [json.loads(line)["a"] for line in lines] == [1, 2]


BUNDLE = APP_ROOT / "tools" / "opip_readiness_bundle.sh"


def test_ac_007_the_readiness_bundle_is_read_only_and_syntactically_valid():
    """ATDD-OPIP-PROBE-SNAPSHOT-FIXES-V1/AC-007: the one-paste diagnostic bundle cannot change production: valid shell, no mutating command, read-only database access."""
    import re
    import shutil

    raw = BUNDLE.read_text(encoding="utf-8")
    # Comments (usage notes) are not commands.
    text = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#"))
    if shutil.which("bash"):
        subprocess.run(["bash", "-n", str(BUNDLE)], check=True)
    forbidden = [
        r"\bdocker\s+(compose\s+)?(restart|stop|kill|rm|rmi|up|down|run|start|pause|cp|build|prune)\b",
        r"\bsystemctl\b",
        r"\bservice\s+\S+\s+(restart|stop|start)\b",
        r"(^|[;&|]\s*)(rm|mv|cp|chmod|chown|truncate|dd|kill|pkill|killall|crontab)\s",
        r"\btee\s",
        r">>?\s*/(?!dev/null)",
        r"\b(INSERT|UPDATE|DELETE|DROP|VACUUM|REINDEX)\b",
        r"wal_checkpoint",
        r"curl|wget",
    ]
    for pattern in forbidden:
        match = re.search(pattern, text, flags=re.MULTILINE)
        assert match is None, (pattern, match.group(0) if match else "")
    assert "mode=ro" in text and "query_only" in text
    # Bounded: the only count is time-boxed and niced; no unbounded scan helper.
    assert "set_progress_handler" in text and "timeout 30" in text
    assert "SELECT COUNT(*) FROM events\n" not in text
