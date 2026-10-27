"""ATDD-RELEASE-TREE-DEPLOY-v1/AC-012 - core environment credential isolation.

The core container must never inherit a funded exchange credential. The deploy
controller therefore derives `.env.core` from the owner's `.env`, removing every
name in ``release_profiles.FUNDED_CREDENTIAL_KEYS``; the core service consumes
only that filtered file. The owner's `.env` is never modified, no value is ever
printed, and a missing or malformed source fails closed.

The filtered file is also a hard precondition of every Compose operation that
resolves the core service, so it is generated for the release installed BEFORE
activation (ahead of the first `docker compose` call), again after activation,
and before the rollback producer-quiescence proof; a deploy that fails before it
exists must still be able to roll back. Those ordering contracts, and the two
reported production failures they prevent, are pinned at the end of this module.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

APP = pathlib.Path(__file__).resolve().parents[1]
LIBRARY = APP / "deploy" / "remote" / "lib" / "opip_release_tree.sh"
DEPLOY = APP / "deploy" / "remote" / "ohm-deploy"
RECONCILE = APP / "deploy" / "remote" / "reconcile-scheduler.sh"
BOOTSTRAP = APP / "deploy" / "remote" / "bootstrap-release-tree-controller.sh"
COMPOSE = APP / "docker-compose.yml"
DOCKERIGNORE = APP / ".dockerignore"
BASH = shutil.which("bash")

pytestmark = [
    pytest.mark.acceptance,
    pytest.mark.skipif(BASH is None, reason="bash is required"),
]

ROTATED = "rotated-secret-value-9f2c"


def _library_keys() -> tuple[str, ...]:
    text = LIBRARY.read_text(encoding="utf-8")
    match = re.search(r'^OPIP_FUNDED_CREDENTIAL_KEYS="([^"]*)"$', text, re.MULTILINE)
    assert match, "funded credential name literal is missing from the provider"
    return tuple(match.group(1).split())


def _run(app_root: pathlib.Path, state: pathlib.Path):
    script = f"""
set -Eeuo pipefail
APP_ROOT={app_root.as_posix()}
STATE_DIR={state.as_posix()}
source {LIBRARY.as_posix()}
opip_write_core_env
"""
    return subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, check=False
    )


@pytest.fixture()
def sandbox(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    app_root = tmp_path / "app"
    app_root.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (app_root / ".env").write_text(
        "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\n"
        "OPIP_FEATURE_BUS_MODE=shadow\n"
        "KRAKEN_API_KEY=fake-key-value\n"
        "KRAKEN_API_SECRET=fake-secret-value\n"
        "KRKN_API_KEY=fake-legacy-value\n"
        "KRAKEN_RECONCILIATION_ENABLED=false\n"
        "OPIP_TRADEVISOR_WEBHOOK_TOKEN=token-value\n",
        encoding="utf-8",
    )
    return app_root, state


def test_funded_credential_names_match_the_python_authority() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: the controller filter and the
    release-profile authority list exactly the same funded credential names."""
    from app.services.release_profiles import FUNDED_CREDENTIAL_KEYS

    assert _library_keys() == tuple(FUNDED_CREDENTIAL_KEYS)


def test_generator_removes_funded_names_and_keeps_the_rest(sandbox) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: only funded names are removed."""
    app_root, state = sandbox
    source_before = (app_root / ".env").read_bytes()

    result = _run(app_root, state)
    assert result.returncode == 0, result.stderr

    dest = app_root / ".env.core"
    assert dest.is_file()
    text = dest.read_text(encoding="utf-8")
    for key in _library_keys():
        assert key not in text
    assert "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW" in text
    assert "KRAKEN_RECONCILIATION_ENABLED=false" in text
    assert "OPIP_TRADEVISOR_WEBHOOK_TOKEN=token-value" in text

    assert (app_root / ".env").read_bytes() == source_before
    assert oct(dest.stat().st_mode & 0o777) == "0o600"
    assert "fake-key-value" not in result.stdout + result.stderr
    assert "fake-secret-value" not in result.stdout + result.stderr
    assert f"excluded_count={len(_library_keys())}" in result.stdout


def test_generator_is_idempotent_and_follows_rotation(sandbox) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: regeneration is stable, and a rotated
    source produces a new filtered file."""
    app_root, state = sandbox
    dest = app_root / ".env.core"

    assert _run(app_root, state).returncode == 0
    first = dest.read_bytes()
    assert _run(app_root, state).returncode == 0
    assert dest.read_bytes() == first

    source = app_root / ".env"
    source.write_text(
        source.read_text(encoding="utf-8")
        .replace("fake-key-value", ROTATED)
        .replace(
            "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW",
            "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\nOPIP_MARKER=after-rotation",
        ),
        encoding="utf-8",
    )
    assert _run(app_root, state).returncode == 0
    rotated = dest.read_text(encoding="utf-8")
    assert ROTATED not in rotated
    assert "OPIP_MARKER=after-rotation" in rotated
    assert rotated != first.decode("utf-8")



def test_missing_or_symlinked_source_fails_closed(tmp_path: pathlib.Path) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: no source, or a symlinked source,
    refuses and never leaves a filtered file behind."""
    app_root = tmp_path / "app"
    app_root.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    dest = app_root / ".env.core"

    missing = _run(app_root, state)
    assert missing.returncode != 0
    assert "missing, unreadable or a symlink" in missing.stderr
    assert not dest.exists()

    real = tmp_path / "real.env"
    real.write_text(
        "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\nKRAKEN_API_KEY=x\n", encoding="utf-8"
    )
    (app_root / ".env").symlink_to(real)
    linked = _run(app_root, state)
    assert linked.returncode != 0
    assert not dest.exists()


def test_core_compose_consumes_only_the_filtered_environment() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: the core service reads the filtered
    file, and that file can never enter the image build context."""
    compose = COMPOSE.read_text(encoding="utf-8")
    core = compose[compose.index("  ohm-trade-agent:"):compose.index("  opip-canonical-writer:")]
    assert "env_file:" in core
    assert "- .env.core" in core
    assert "- .env\n" not in core
    dockerignore = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    assert ".env.*" in dockerignore


# ---------------------------------------------------------------------------
# AC-012 deploy-order regression: the filtered core environment is a
# precondition of every Compose operation that resolves the core service, on the
# success path AND on the rollback path.
#
# Reported production failures reproduced here:
#   * 3ed771fb: `docker compose up -d ohm-trade-agent` refused with
#     "env file .../.env.core not found" after the canonical writer had already
#     been recreated, because the filtered file was generated far later than the
#     Compose sequence that needs it.
#   * rollback: `docker compose exec -T ohm-trade-agent` (the producer-quiescence
#     proof) failed for the same reason, the process state became UNKNOWN and a
#     rollback that could have completed aborted as
#     OPIP_ROLLBACK_ABORTED=PRODUCER_QUIESCENCE_UNPROVEN.
# ---------------------------------------------------------------------------

#: Compose resolves a service's `env_file` only when it materializes that
#: service (`up`, `exec`, `config`), and the shipped Compose refuses the whole
#: operation when the file is absent. This seam reproduces that exactly: the
#: core-resolving operation fails with Compose's own message while `.env.core` is
#: missing and succeeds once the controller produced it.
_COMPOSE_SEAM = """
docker() {
  if [[ "${1:-}" == "compose" ]]; then
    if [[ ! -f "$APP_ROOT/.env.core" ]]; then
      echo "env file $APP_ROOT/.env.core not found: stat $APP_ROOT/.env.core: no such file or directory" >&2
      return 1
    fi
    printf '%s\\n' '{"feature_snapshot_cursor": null, "feasibility_evidence_cursor": null}'
  fi
  return 0
}
"""


def _script(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace(f"__{key.upper()}__", value)
    return template


def _run_script(script: str, tmp: pathlib.Path, name: str, timeout: int = 90):
    path = tmp / name
    path.write_text(script, encoding="utf-8", newline="\n")
    return subprocess.run(
        [BASH, str(path)], capture_output=True, text=True, timeout=timeout
    )


def _deploy_text() -> str:
    return DEPLOY.read_text(encoding="utf-8")


def _function_block(name: str, next_name: str) -> str:
    text = _deploy_text()
    start = text.index(f"{name}() {{")
    end = text.index(f"{next_name}() {{", start)
    return text[start:end].rstrip()


def _rollback_block() -> str:
    text = _deploy_text()
    start = text.index("rollback() {")
    end = text.index("\ntrap rollback ERR", start)
    return text[start:end]


def _installed_tree_ensure_block() -> str:
    """The real controller text that generates `.env.core` for the release that
    is installed BEFORE activation (the gate that precedes the first Compose
    call)."""
    text = _deploy_text()
    start = text.index(
        "# The filtered core environment must exist BEFORE ANY Compose operation"
    )
    call = text.index("opip_write_core_env || {", start)
    end = text.index("\n}\n", call) + 3
    return text[start:end]


def _ordered(name: str, text: str) -> int:
    assert name in text, name
    return text.index(name)


def test_controller_generates_before_every_compose_operation_and_on_both_failure_paths() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: `.env.core` exists before ANY Compose
    operation that resolves the core service -- the pre-activation evidence read,
    the build/recreate, and the rollback quiescence proof."""
    deploy = _deploy_text()
    installed_ensure = _ordered(
        "# The filtered core environment must exist BEFORE ANY Compose operation",
        deploy,
    )
    baseline_read = _ordered(
        'RELEASE_EVIDENCE_BASELINE="$(capture_release_evidence_baseline)"', deploy
    )
    rollback_ensure = _ordered(
        "filtered core environment could not be generated before producer quiescence",
        deploy,
    )
    quiescence_guard = _ordered("if ! quiesce_evidence_producers; then", deploy)
    restored_ensure = _ordered(
        "rollback: filtered core environment could not be regenerated", deploy
    )
    activation_ensure = _ordered(
        'refusing deploy: the filtered core environment could not be generated"',
        deploy,
    )
    build = _ordered(
        'docker compose build --build-arg "OPIP_RELEASE_SHA=$TARGET_SHA"', deploy
    )
    writer_up = _ordered(
        "docker compose up -d --remove-orphans opip-canonical-writer", deploy
    )
    core_up = _ordered("docker compose up -d --remove-orphans ohm-trade-agent", deploy)

    # The release installed right now: generation precedes the evidence-baseline
    # `docker compose exec -T ohm-trade-agent`, which is the first Compose call.
    assert installed_ensure < baseline_read
    # Rollback: generation precedes the quiescence proof, and the regeneration
    # for the restored release still happens after SAFE_BASELINE is applied.
    assert rollback_ensure < quiescence_guard < restored_ensure
    # Candidate release: generation precedes the build/recreate that resolves it.
    assert baseline_read < activation_ensure < build < writer_up < core_up
    assert (
        "refusing deploy: the filtered core environment could not be generated"
        " for the installed release" in deploy
    )


def test_positions_fails_safe_when_the_credential_is_absent(monkeypatch) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: with the credential isolated, the
    operator view degrades to a bounded message and never invents data."""
    from app.exchanges.kraken_private import KrakenPrivateClient
    from app.services import telegram_command_center

    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    assert KrakenPrivateClient().enabled is False

    message = telegram_command_center.positions_report()
    assert message == "⚠️ POSITIONS — Kraken private account data is temporarily unavailable."
    assert "fake-key-value" not in message
    assert telegram_command_center.orders_report().startswith("⚠️ ORDERS")


def test_core_env_exists_before_the_first_compose_call_that_resolves_the_core_service(
    tmp_path: pathlib.Path,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: the real controller ordering produces
    the filtered file before the first Compose call, so the core service resolves."""
    app_root = tmp_path / "app"
    app_root.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (app_root / ".env").write_text(
        "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\n"
        "KRAKEN_API_KEY=fake-key-value\n"
        "OPIP_MARKER=kept\n",
        encoding="utf-8",
    )
    script = _script(
        """
set -Eeuo pipefail
APP_ROOT=__APP_ROOT__
STATE_DIR=__STATE__
source __LIBRARY__
__SEAM__
__BASELINE__
__ENSURE__
capture_release_evidence_baseline
printf '%s\\n' BASELINE_READ=PASS
""",
        app_root=app_root.as_posix(),
        state=state.as_posix(),
        library=LIBRARY.as_posix(),
        seam=_COMPOSE_SEAM,
        baseline=_function_block(
            "capture_release_evidence_baseline", "snapshot_scheduler_state"
        ),
        ensure=_installed_tree_ensure_block(),
    )
    result = _run_script(script, tmp_path, "installed-tree-order.sh")

    assert result.returncode == 0, result.stderr
    assert "BASELINE_READ=PASS" in result.stdout
    assert "not found" not in result.stderr
    produced = app_root / ".env.core"
    assert produced.is_file()
    assert "KRAKEN_API_KEY" not in produced.read_text(encoding="utf-8")


def test_the_pre_incident_order_fails_exactly_as_reported(tmp_path: pathlib.Path) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: reproduces the reported failure -- with
    the generation gate absent (the deployed 3ed771fb sequence) the very first
    Compose call that resolves the core service refuses because the filtered file
    does not exist."""
    app_root = tmp_path / "app"
    app_root.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (app_root / ".env").write_text(
        "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\nKRAKEN_API_KEY=fake-key-value\n",
        encoding="utf-8",
    )
    script = _script(
        """
set -Eeuo pipefail
APP_ROOT=__APP_ROOT__
STATE_DIR=__STATE__
source __LIBRARY__
__SEAM__
__BASELINE__
capture_release_evidence_baseline
""",
        app_root=app_root.as_posix(),
        state=state.as_posix(),
        library=LIBRARY.as_posix(),
        seam=_COMPOSE_SEAM,
        baseline=_function_block(
            "capture_release_evidence_baseline", "snapshot_scheduler_state"
        ),
    )
    result = _run_script(script, tmp_path, "installed-tree-order-prefix.sh")

    assert result.returncode != 0
    assert "env file" in result.stderr
    assert ".env.core not found" in result.stderr
    assert not (app_root / ".env.core").exists()


_ROLLBACK_HARNESS = """
set -uo pipefail
LOG=__LOG__
: >"$LOG"
PREVIOUS_SHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
APP_ROOT=__APP_ROOT__
STATE_DIR=__STATE__
LAST_GOOD_FILE=__STATE__/last-good-sha
SAFE_BASELINE_OVERRIDE=__STATE__/safe-baseline-rollback.override.yml
RELEASE_RUNTIME_VERIFIER_ATTEMPTED=""
source __LIBRARY__
__OVERRIDE__
stop_paper_stack() { echo "stop_paper_stack" >>"$LOG"; }
preserve_scheduler_snapshot_on_abort() { echo "preserve_scheduler_snapshot_on_abort" >>"$LOG"; }
write_safe_baseline_override() { echo "write_safe_baseline_override" >>"$LOG"; }
wait_core_health() { echo "wait_core_health" >>"$LOG"; return 0; }
wait_writer_health() { echo "wait_writer_health" >>"$LOG"; return 0; }
writer_health_diagnostics() { echo "writer_health_diagnostics" >>"$LOG"; return 0; }
validate_safe_baseline_modes() { echo "validate_safe_baseline_modes" >>"$LOG"; return 0; }
restore_scheduler_state() { echo "restore_scheduler_state" >>"$LOG"; }
release_evidence_producer_locks() { echo "release_evidence_producer_locks" >>"$LOG"; }
start_paper_stack() { echo "start_paper_stack" >>"$LOG"; return 0; }
wait_paper_health() { echo "wait_paper_health" >>"$LOG"; return 0; }
cleanup_snapshot() { echo "cleanup_snapshot" >>"$LOG"; }
emit_capture_producer_disposition_evidence() { echo "disposition" >>"$LOG"; return 0; }
git() { printf '%s\\n' "$PREVIOUS_SHA"; }
GIT=(git)
docker() {
  echo "docker $*" >>"$LOG"
  if [[ " $* " == *" config " && " $* " == *" --services "* ]]; then
    printf '%s\\n' opip-canonical-writer
  fi
  return 0
}
__QUIESCENCE__
__ROLLBACK__
false
rollback
"""


def _rollback_scenario(
    tmp_path: pathlib.Path,
    *,
    core_env_present: bool,
    generation_available: bool,
    generation_skipped: bool = False,
) -> tuple[subprocess.CompletedProcess, str]:
    """Run the REAL rollback control flow with every external effect stubbed.

    ``generation_skipped`` reproduces a deploy that failed BEFORE the filtered
    core environment was generated (no generation gate at all, as in the deployed
    3ed771fb controller); ``generation_available=False`` reproduces a generation
    gate that is present but cannot run.
    """
    app_root = tmp_path / "app"
    app_root.mkdir(exist_ok=True)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    if generation_available:
        (app_root / ".env").write_text(
            "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\nKRAKEN_API_KEY=fake-key-value\n",
            encoding="utf-8",
        )
    if core_env_present:
        (app_root / ".env.core").write_text(
            "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\n", encoding="utf-8"
        )
    override = (
        'opip_write_core_env() { echo "opip_write_core_env skipped" >>"$LOG"; return 1; }'
        if generation_skipped
        else ""
    )
    quiescence = """
quiesce_evidence_producers() {
  if [[ ! -f "$APP_ROOT/.env.core" ]]; then
    echo "OPIP_EVIDENCE_PRODUCER_PROCESS_STATE=UNKNOWN" >&2
    echo "OPIP_EVIDENCE_PRODUCER_QUIESCENCE=FAILED" >&2
    echo "OPIP_EVIDENCE_PRODUCER_QUIESCENCE_REASON=PRODUCER_PROCESS_STATE_UNKNOWN" >&2
    echo "quiesce core_env=absent" >>"$LOG"
    return 1
  fi
  echo "quiesce core_env=present" >>"$LOG"
  printf '%s\\n' OPIP_EVIDENCE_PRODUCER_QUIESCENCE=QUIESCED
  return 0
}
"""
    script = _script(
        _ROLLBACK_HARNESS,
        log=log.as_posix(),
        app_root=app_root.as_posix(),
        state=state.as_posix(),
        library=LIBRARY.as_posix(),
        override=override,
        quiescence=quiescence,
        rollback=_rollback_block(),
    )
    result = _run_script(script, tmp_path, "rollback.sh")
    return result, log.read_text(encoding="utf-8")


def test_rollback_generates_the_core_env_before_the_producer_quiescence_proof(
    tmp_path: pathlib.Path,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: when the deploy failed before the
    filtered file existed, rollback still reaches a provable quiescence instead
    of aborting as PRODUCER_QUIESCENCE_UNPROVEN."""
    result, log = _rollback_scenario(
        tmp_path, core_env_present=False, generation_available=True
    )

    assert "quiesce core_env=present" in log
    assert "OPIP_ROLLBACK_ABORTED" not in result.stderr
    assert "OPIP_EVIDENCE_PRODUCER_QUIESCENCE=FAILED" not in result.stderr
    assert "OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS" in result.stdout
    assert log.index("quiesce core_env=present") < log.index(
        "write_safe_baseline_override"
    )


def test_rollback_without_the_generation_gate_reproduces_the_reported_abort(
    tmp_path: pathlib.Path,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: reproduces the reported rollback -- with
    no generation gate (the deployed 3ed771fb controller) the quiescence Compose
    proof cannot resolve the core service, so the rollback aborts and the
    SAFE_BASELINE restore is never applied."""
    result, log = _rollback_scenario(
        tmp_path,
        core_env_present=False,
        generation_available=True,
        generation_skipped=True,
    )

    assert "quiesce core_env=absent" in log
    assert "OPIP_ROLLBACK_ABORTED=PRODUCER_QUIESCENCE_UNPROVEN" in result.stderr
    assert "OPIP_SAFE_BASELINE_ROLLBACK=UNPROVEN" in result.stderr
    assert (
        "OPIP_EVIDENCE_PRODUCER_QUIESCENCE_REASON=PRODUCER_PROCESS_STATE_UNKNOWN"
        in result.stderr
    )
    assert "preserve_scheduler_snapshot_on_abort" in log
    assert "write_safe_baseline_override" not in log
    assert "OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS" not in result.stdout


def test_rollback_stays_fail_closed_when_the_core_env_cannot_be_generated(
    tmp_path: pathlib.Path,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: the generation gate never weakens the
    fail-closed abort -- an unprovable quiescence still aborts the rollback."""
    result, log = _rollback_scenario(
        tmp_path, core_env_present=False, generation_available=False
    )

    assert (
        "WARN  rollback: filtered core environment could not be generated before"
        " producer quiescence" in result.stderr
    )
    assert "quiesce core_env=absent" in log
    assert "OPIP_ROLLBACK_ABORTED=PRODUCER_QUIESCENCE_UNPROVEN" in result.stderr
    assert "OPIP_SAFE_BASELINE_ROLLBACK=UNPROVEN" in result.stderr
    assert "write_safe_baseline_override" not in log


def test_runtime_core_env_is_not_part_of_the_release_manifest(
    tmp_path: pathlib.Path,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: the file the controller must create is
    runtime-only, so it can never invalidate the installed release -- and an
    unrecorded or modified source file still fails closed."""
    release = tmp_path / "release"
    (release / "app").mkdir(parents=True)
    (release / "data").mkdir()
    (release / "app" / "x.py").write_text("X = 1\n", encoding="utf-8")
    (release / ".env").write_text(
        "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\nKRAKEN_API_KEY=not-a-real-key\n",
        encoding="utf-8",
    )
    (release / "data" / "canonical.sqlite3").write_bytes(b"db")
    state = tmp_path / "state"
    state.mkdir()
    manifest = tmp_path / "manifest.sha256"
    script = _script(
        """
set -Eeuo pipefail
APP_ROOT=__APP_ROOT__
STATE_DIR=__STATE__
LAST_GOOD_FILE=__STATE__/last-good-sha
source __LIBRARY__
rt_list_tree "$APP_ROOT" > __MANIFEST__
grep -q '  app/x.py$' __MANIFEST__
! grep -q ' \\.env' __MANIFEST__
! grep -q 'data/' __MANIFEST__
rt_verify_tree "$APP_ROOT" __MANIFEST__
opip_write_core_env
[ -f "$APP_ROOT/.env.core" ]
# The generated runtime file must not make the installed release unverifiable.
rt_verify_tree "$APP_ROOT" __MANIFEST__
printf '%s\\n' 'rogue = True' > "$APP_ROOT/app/rogue.py"
if rt_verify_tree "$APP_ROOT" __MANIFEST__ 2>/dev/null; then exit 90; fi
rm -f "$APP_ROOT/app/rogue.py"
printf '%s\\n' 'X = 2' > "$APP_ROOT/app/x.py"
if rt_verify_tree "$APP_ROOT" __MANIFEST__ 2>/dev/null; then exit 91; fi
printf '%s\\n' MANIFEST=OK
""",
        app_root=release.as_posix(),
        state=state.as_posix(),
        library=LIBRARY.as_posix(),
        manifest=manifest.as_posix(),
    )
    result = _run_script(script, tmp_path, "manifest.sh")

    assert result.returncode == 0, result.stderr
    assert "MANIFEST=OK" in result.stdout


def test_controller_refuses_before_activation_when_the_library_cannot_generate() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: a source provider without the generator
    refuses at start-up with the only supported remedy, instead of failing after
    the canonical writer was already recreated."""
    deploy = _deploy_text()
    guard = _ordered("does not provide opip_write_core_env", deploy)
    assert guard < _ordered("\nopip_source_init\n", deploy)
    assert guard < _ordered("trap rollback ERR", deploy)
    assert guard < _ordered("opip_write_core_env || {", deploy)
    assert "bootstrap-release-tree-controller.sh --apply" in deploy[guard : guard + 900]


def test_scheduler_reconciliation_keeps_the_source_provider_in_lockstep() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: the library that provides the generator
    is installed from the deployed release, on the same path as the controller, so
    it can never be one release behind it."""
    text = RECONCILE.read_text(encoding="utf-8")
    assert 'LIBRARY_SRC="$APP_ROOT/deploy/remote/lib/opip_release_tree.sh"' in text
    assert 'LIBRARY_DST="/usr/local/lib/opip/opip_release_tree.sh"' in text
    gate = _ordered('bash -n "$LIBRARY_SRC"', text)
    install = _ordered(
        'install -o root -g root -m 0644 "$LIBRARY_SRC" "$LIBRARY_STAGED"', text
    )
    assert gate < install
    # Installed as the LAST mutation, so no ERR rollback can leave a new library
    # beside a restored old controller.
    assert install < _ordered("\ntrap - ERR\n", text)


def test_bootstrap_preserves_a_proven_activation_record() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: recovering from a deploy that activated a
    release and then failed must not relabel the live tree with the last-good SHA --
    the bootstrap preserves the manifest-verified activation record and only falls
    back to recording the running release when that record cannot be proven."""
    text = BOOTSTRAP.read_text(encoding="utf-8")
    guard = _ordered('LIVE_RECORD="$(rt_state_get "$OPIP_CURRENT_RELEASE" sha || true)"', text)
    verify = _ordered('rt_verify_tree "$APP_ROOT" "$STATE_DIR/releases/$LIVE_RECORD/manifest.sha256"', text)
    preserved = _ordered("baseline=PRESERVED", text)
    fallback = _ordered('rt_write_state "$OPIP_CURRENT_RELEASE" "$RUNNING_SHA"', text)
    assert guard < verify < preserved < fallback
    # The fallback still materializes the rollback vehicle, and nothing about the
    # installation changes: no container, no deployment, no gateway reachability.
    assert "rt_snapshot_current" in text
    assert "docker compose" not in text


@pytest.mark.parametrize(
    "script",
    [DEPLOY, LIBRARY, RECONCILE, BOOTSTRAP],
    ids=["controller", "library", "reconcile", "bootstrap"],
)
def test_the_edited_remote_operators_parse(script: pathlib.Path) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: every edited production shell file
    parses before it can be installed."""
    result = subprocess.run([BASH, "-n", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr



