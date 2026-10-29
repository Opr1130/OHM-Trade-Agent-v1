"""ATDD-RELEASE-TREE-DEPLOY-v1/AC-016: Tradevisor activation uses .env.core.

Model Docker Compose's real env_file behaviour: an already-running core does
not consume edits to its source file until the container is recreated. No
Docker daemon, production host, or real credential is touched by these tests.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest


APP = pathlib.Path(__file__).resolve().parents[1]
ACTIVATION = APP / "deploy" / "tradevisor" / "activate-tradevisor-advisory.sh"
PROVIDER = APP / "deploy" / "remote" / "lib" / "opip_release_tree.sh"
BASH = shutil.which("bash")

pytestmark = [pytest.mark.acceptance, pytest.mark.skipif(BASH is None, reason="bash required")]


def _functions() -> str:
    source = ACTIVATION.read_text(encoding="utf-8")
    helpers = source[source.index("env_count() {"):source.index("valid_urlsafe() {")]
    transition = source[
        source.index("# --- Route state transitions"):
        source.index("# --- Rollback")
    ]
    rollback = source[
        source.index("restore_pre_activation_posture() {"):
        source.index("\ndie() {", source.index("restore_pre_activation_posture() {"))
    ]
    return "\n".join((helpers, transition, rollback))


def _run(
    tmp_path: pathlib.Path,
    mode: str,
    *,
    broken_provider: bool = False,
    stuck_runtime: bool = False,
):
    app = tmp_path / "app"
    library = app / "deploy" / "remote" / "lib"
    library.mkdir(parents=True)
    (library / "opip_release_tree.sh").write_text(
        "opip_write_core_env() { return 1; }\n"
        if broken_provider else PROVIDER.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (app / ".env").write_text(
        "OPIP_TRADEVISOR_ENABLED=false\n"
        "OPIP_TRADEVISOR_WEBHOOK_TOKEN=test-only-token\n"
        "KRAKEN_API_KEY=fake-funded-credential\n",
        encoding="utf-8",
    )
    (app / ".env.core").write_text(
        "OPIP_TRADEVISOR_ENABLED=false\n", encoding="utf-8"
    )
    runtime = tmp_path / "container-env"
    runtime.write_text("false\n", encoding="utf-8")
    if mode == "explicit_rollback":
        (app / ".env.core").write_text(
            "OPIP_TRADEVISOR_ENABLED=true\n", encoding="utf-8"
        )
        runtime.write_text("true\n", encoding="utf-8")
    functions = tmp_path / "activation-functions.sh"
    functions.write_text(_functions(), encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    script = f"""
set -Eeuo pipefail
APP_ROOT={app.as_posix()}
STATE_DIR={state.as_posix()}
ENV_FILE=$APP_ROOT/.env
ENABLE_KEY=OPIP_TRADEVISOR_ENABLED
CORE_SERVICE=ohm-trade-agent
EDGE_SERVICE=ohm-dashboard-edge
ENABLED_FLIPPED=0
EDGE_RECREATED=0
PRIOR_ENABLED=false
ROLLBACK_ONLY=1
RUNTIME_FILE={runtime.as_posix()}
STUCK_RUNTIME={int(stuck_runtime)}
COMPOSE_LOG={tmp_path.as_posix()}/compose.log
step() {{ :; }}
pass() {{ :; }}
warn() {{ printf 'WARN  %s\\n' "$*" >&2; }}
wait_core_health() {{ return 0; }}
wait_dashboard() {{ return 0; }}
wait_writer_health() {{ return 0; }}
probe_route() {{
  if [[ "$(cat "$RUNTIME_FILE")" == "false" ]]; then printf '404'; else printf '422'; fi
}}
compose() {{
  case "$1" in
    up)
      printf '%s\\n' "$*" >> "$COMPOSE_LOG"
      # Existing containers retain their environment unless recreated.
      if [[ " $* " == *" --force-recreate "* && "$STUCK_RUNTIME" == 0 ]]; then
        awk -F= '$1 == "OPIP_TRADEVISOR_ENABLED" {{ print $2 }}' \
          "$APP_ROOT/.env.core" > "$RUNTIME_FILE"
      fi
      ;;
    exec)
      [[ "$*" == *"Settings().opip_tradevisor_enabled"* ]] || return 1
      cat "$RUNTIME_FILE"
      ;;
    *) return 1 ;;
  esac
}}
source {functions.as_posix()}
die() {{
  printf 'FAIL  %s\\n' "$*" >&2
  if [[ "$ENABLED_FLIPPED" == 1 ]]; then
    restore_pre_activation_posture || exit 3
  fi
  exit 1
}}
case {mode} in
  enable) enable_ingress ;;
  rollback) enable_ingress && restore_pre_activation_posture ;;
  explicit_rollback) verify_route_is_dark && restore_pre_activation_posture ;;
esac
"""
    result = subprocess.run([BASH, "-c", script], capture_output=True, text=True)
    return result, app, runtime, tmp_path / "compose.log"


def test_ac_016_enable_refreshes_filtered_env_and_recreates_running_core(tmp_path):
    result, app, runtime, log = _run(tmp_path, "enable")
    assert result.returncode == 0, result.stderr
    assert "OPIP_TRADEVISOR_ENABLED=true" in (app / ".env").read_text()
    filtered = (app / ".env.core").read_text()
    assert "OPIP_TRADEVISOR_ENABLED=true" in filtered
    assert "KRAKEN_API_KEY" not in filtered
    assert runtime.read_text().strip() == "true"
    assert "--force-recreate ohm-trade-agent" in log.read_text()


def test_ac_016_rollback_refreshes_env_and_recreates_core_disabled(tmp_path):
    result, app, runtime, log = _run(tmp_path, "rollback")
    assert result.returncode == 0, result.stderr
    assert "OPIP_TRADEVISOR_ENABLED=false" in (app / ".env").read_text()
    assert "OPIP_TRADEVISOR_ENABLED=false" in (app / ".env.core").read_text()
    assert runtime.read_text().strip() == "false"
    assert log.read_text().count("--force-recreate ohm-trade-agent") == 2
    assert "ROLLBACK=RESTORED" in result.stdout


def test_ac_016_generator_failure_refuses_activation_and_restores_owner_flag(tmp_path):
    result, app, runtime, _ = _run(tmp_path, "enable", broken_provider=True)
    assert result.returncode != 0
    assert "OPIP_TRADEVISOR_ENABLED=false" in (app / ".env").read_text()
    assert runtime.read_text().strip() == "false"
    assert "FAIL" in result.stderr


def test_ac_016_runtime_mismatch_refuses_activation_and_rolls_back(tmp_path):
    result, app, runtime, _ = _run(tmp_path, "enable", stuck_runtime=True)
    assert result.returncode != 0
    assert "running core did not accept" in result.stderr
    assert "OPIP_TRADEVISOR_ENABLED=false" in (app / ".env").read_text()
    assert "OPIP_TRADEVISOR_ENABLED=false" in (app / ".env.core").read_text()
    assert runtime.read_text().strip() == "false"


def test_ac_016_explicit_rollback_repairs_stale_core_even_if_owner_env_is_false(tmp_path):
    result, app, runtime, log = _run(tmp_path, "explicit_rollback")
    assert result.returncode == 0, result.stderr
    assert "OPIP_TRADEVISOR_ENABLED=false" in (app / ".env.core").read_text()
    assert runtime.read_text().strip() == "false"
    assert "--force-recreate ohm-trade-agent" in log.read_text()
    assert "ROLLBACK=RESTORED" in result.stdout
