# Reproduction commands

The package is self-contained for Python tooling. The certified application tree
is **not** shipped; point the environment at an independently verified checkout
of `c196dd674678b3b1ee91c351ee97c1c9e2b3938d`.

```bash
# 0. pin the source (must be a real checkout at the exact SHA)
git -C "$OPIP_CERTIFIED_SRC_PARENT" rev-parse HEAD   # must print c196dd67...
export OPIP_CERTIFIED_SRC="$OPIP_CERTIFIED_SRC_PARENT"   # parent of OHM-Trade-Agent-v1
export PYTHONPATH="$PWD/scripts"
export PYTHONDONTWRITEBYTECODE=1

# 1. bytes: the shipped shell scripts must be LF-only
python - <<'PY'
from pathlib import Path
for p in ("scripts/v2/bootstrap-v2.sh", "scripts/v2/rollback-v2.sh"):
    raw = Path(p).read_bytes()
    assert b"\r\n" not in raw, p
    assert raw.startswith(b"#!/usr/bin/env bash\n"), p
print("LF-only: OK")
PY

# 2. shell syntax **on Linux** (this is the check the delivered ZIP failed)
bash -n scripts/v2/bootstrap-v2.sh
bash -n scripts/v2/rollback-v2.sh

# 3. python syntax
python -m compileall -q scripts tests

# 4. full suite (99 tests)
python -m pytest tests/scenarios -q

# 5. fast, fixture-free subset (35 tests, ~0.3 s)
python -m pytest tests/scenarios/test_r2_audit_fixes.py -q
```

Expected output of step 4:

```
99 passed
```

## Notes

* `tests/conftest.py` defaults `OPIP_CERTIFIED_SRC` to
  `C:\Users\ohmpr\opip-mission-07\src` on Windows; always override it when
  reviewing on another machine.
* The fixture bundle is built on demand (`make_fixture.py`) if absent.
* No test contacts the network, a database or a host.
