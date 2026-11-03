"""ATDD: owner-gated UI-ONLY release through the existing O'Pip controller."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

INCREMENT = "ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1"
ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
LIB = ROOT / "deploy/remote/lib/opip_ui_release.sh"
CONTROLLER = ROOT / "deploy/remote/ohm-deploy"
GATEWAY = ROOT / "deploy/remote/ohm-deploy-ssh"
OVERLAY = ROOT / "deploy/control-center/docker-compose.v2.yml"
README = ROOT / "deploy/control-center/README.md"
WORKFLOW = REPO / ".github/workflows/deploy-production.yml"
SHA = "a" * 40

FORBIDDEN_SERVICES = (
    "ohm-trade-agent",
    "opip-canonical-writer",
    "opip-stream-worker",
    "ohm-freqtrade-paper",
    "opip-irr-shadow",
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.acceptance
def test_forced_command_exposes_only_bounded_ui_release_actions():
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-001: gateway stays bounded."""
    gateway = _text(GATEWAY)
    assert "^deploy[[:space:]]+([0-9a-f]{40})$" in gateway
    assert "^deploy-ui[[:space:]]+([0-9a-f]{40})$" in gateway
    assert "^rollback-ui[[:space:]]+([0-9a-f]{40})$" in gateway
    assert '"$ORIGINAL" == "diagnose-learning"' in gateway
    assert "ohm-deploy ui-deploy" in gateway
    assert "ohm-deploy rollback-ui" in gateway
    # No arbitrary execution surface may appear alongside the allowlist.
    for forbidden in ("bash -c", "eval ", "sh -c", "exec bash", "${SHELL}"):
        assert forbidden not in gateway
    assert gateway.rstrip().endswith("exit 64")


@pytest.mark.acceptance
def test_controller_ui_hand_off_exits_before_any_core_path():
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-001: no core path is reachable."""
    controller = _text(CONTROLLER)
    assert 'case "$TARGET_SHA" in' in controller
    assert "ui-deploy|rollback-ui)" in controller
    assert 'OPIP_UI_STABLE_LIB="/usr/local/lib/opip/opip_ui_release.sh"' in controller
    assert "opip_ui_library_path" in controller
    hand_off = controller.index('if [[ -n "$UI_MODE" ]]; then')
    first_core_up = controller.index("docker compose up -d --remove-orphans")
    assert hand_off < first_core_up
    block_end = controller.index("wait_core_health() {", hand_off)
    assert block_end < first_core_up
    block = controller[hand_off:block_end]
    assert "opip_ui_run" in block
    assert "exit $?" in block
    # The UI mode only stages the exact commit; it never activates it and never
    # reaches a Compose mutation of its own.
    assert "source_prepare" in block
    code = [line for line in block.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert not [line for line in code if "source_activate_target" in line]
    assert not [line for line in code if "docker compose" in line]


@pytest.mark.acceptance
def test_owner_gate_requires_exact_main_sha_and_green_ci():
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-001: same owner gate as /deploy."""
    workflow = _text(WORKFLOW)
    assert "deploy_ui:" in workflow
    assert "github.event.comment.user.login == github.repository_owner" in workflow
    assert "github.event.comment.author_association == 'OWNER'" in workflow
    assert "startsWith(github.event.comment.body, '/deploy-ui ')" in workflow
    assert "startsWith(github.event.comment.body, '/rollback-ui ')" in workflow
    assert "environment: production" in workflow
    assert "--workflow pytest.yml" in workflow
    assert '"$UI_ACTION $TARGET_SHA"' in workflow
    # A UI release must never be able to trigger the learning deployment.
    assert "deploy-learning" not in workflow.split("deploy_ui:")[1]
    assert "deploy ui-deploy" not in workflow
    assert "docker compose" not in workflow.split("deploy_ui:")[1]


@pytest.mark.acceptance
def test_overlay_pins_only_the_verified_candidate_tree():
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-002: exact-SHA artifacts only."""
    overlay = _text(OVERLAY)
    assert "OPIP_DEPLOYED_SHA:?" in overlay
    assert "${OPIP_UI_TREE:?exact release tree required}/deploy/nginx/dashboard-sidecar.conf:/etc/nginx/nginx.conf:ro" in overlay
    assert "${OPIP_UI_TREE:?exact release tree required}/deploy/control-center/nginx:/etc/nginx/opip-control-center:ro" in overlay
    assert "deploy/control-center/Dockerfile.edge" in overlay
    # A relative mount would resolve against the LIVE (older) release.
    assert "./deploy/" not in overlay
    assert "ports:" not in overlay
    assert "networks:" not in overlay
    assert "mem_limit:" not in overlay
    for forbidden in ("KRAKEN_", "FUNDED_TRADING", "OPIP_F11", "environ" + "ment:"):
        assert forbidden not in overlay
    lib = _text(LIB)
    assert 'OPIP_UI_STAGED_APP="' in _text(CONTROLLER)
    assert "OPIP_STAGING_DIR/$TARGET_SHA/tree/OHM-Trade-Agent-v1" in _text(CONTROLLER)
    assert 'nginx -t' in lib
    assert '-v "$opip_ui_cert_dir:/etc/letsencrypt:ro"' in lib



def _bash_ready() -> bool:
    """True when a usable bash exists (CI: yes; restricted Windows shells: no)."""
    bash = shutil.which("bash")
    if not bash:
        return False
    try:
        probe = subprocess.run([bash, "-c", "exit 0"], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


DOCKER_SHIM = """#!/usr/bin/env bash
log="${FAKE_DOCKER_LOG:?}"
printf '%s\\n' "$*" >> "$log"
cmd="${1:-}"; shift || true
case "$cmd" in
  build) exit "${FAKE_BUILD_RC:-0}" ;;
  image)
    [[ "${1:-}" == "inspect" ]] || exit 0
    if [[ "$*" == *"--format {{.Id}}"* ]]; then printf 'sha256:fakeimage\\n'; fi
    exit 0 ;;
  run)
    if [[ "$*" == *"nginx -t"* ]]; then printf 'configuration file test is successful\\n'; exit "${FAKE_NGINX_RC:-0}"; fi
    if [[ "$*" == *"ls /usr/share"* ]]; then printf 'index-abc123.js\\n'; exit 0; fi
    exit 0 ;;
  inspect)
    if [[ "$*" == *"HostConfig.Memory"* ]]; then
      printf '67108864|67108864|128|true\\n'
    else
      printf 'nginx:1.27-alpine\\n'
    fi
    exit 0 ;;
  port) printf '443/tcp -> 0.0.0.0:443\\n8443/tcp -> 0.0.0.0:8443\\n'; exit 0 ;;
  compose)
    if [[ "$*" == *"config --format json"* ]]; then
      "${FAKE_PYTHON:?}" - "$FAKE_TREE" "$FAKE_SHA" <<'PY'
import json, sys
tree, sha = sys.argv[1], sys.argv[2]
edge = {
    "image": "opip-dashboard-edge-v2:" + sha,
    "read_only": True,
    "volumes": [
        {"type": "bind", "source": tree + "/deploy/nginx/dashboard-sidecar.conf",
         "target": "/etc/nginx/nginx.conf"},
        {"type": "bind", "source": tree + "/deploy/control-center/nginx",
         "target": "/etc/nginx/opip-control-center"},
        {"type": "bind", "source": "/etc/letsencrypt", "target": "/etc/letsencrypt"},
    ],
    "ports": [{"published": "8443"}, {"published": "443"}],
}
print(json.dumps({"services": {"ohm-dashboard-edge": edge}}))
PY
      exit $?
    fi
    if [[ "$*" == *"ps -q"* ]]; then printf 'deadbeefcafe\\n'; exit 0; fi
    exit "${FAKE_UP_RC:-0}" ;;
esac
exit 0
"""

CURL_SHIM = """#!/usr/bin/env bash
wants_code=0
url=""
method="GET"
prev=""
for arg in "$@"; do
  case "$arg" in
    '%{http_code}') wants_code=1 ;;
    https://*) url="$arg" ;;
  esac
  if [[ "$prev" == "--request" ]]; then method="$arg"; fi
  prev="$arg"
done
rest="${url#*://}"
if [[ "$rest" == */* ]]; then path="/${rest#*/}"; else path="/"; fi
status=404
body=""
if [[ "$path" == "/dashboard/v2" ]]; then
  status=308
elif [[ "$path" == "/dashboard/v2/" ]]; then
  if [[ "${FAKE_POST_FAIL:-0}" == "1" ]]; then status=500; else status=200; body='<div id="root"></div>'; fi
elif [[ "$path" == /dashboard/v2/assets/* ]]; then
  status=200
elif [[ "$path" == "/dashboard/" ]]; then
  status=308
elif [[ "$path" == "/dashboard" ]]; then
  status=200; body="O'Pip Intelligence Cockpit"
elif [[ "$path" == /api/opip/* ]]; then
  if [[ "$method" == "DELETE" ]]; then status=403; else status=401; fi
elif [[ "$path" == "/" || "$path" == "/webhooks/tradevisor/v1" ]]; then
  status="${FAKE_TRADEVISOR_STATUS:-000}"
fi
if [[ "$wants_code" == "1" ]]; then printf '%s' "$status"; else printf '%s' "$body"; fi
exit 0
"""

DF_SHIM = """#!/usr/bin/env bash
printf 'Filesystem 1024-blocks Used Available Capacity Mounted\\n/dev/fake 100000000 1 90000000 1%% /\\n'
"""


def _write_exec(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _install_tree(base: Path) -> Path:
    """Candidate tree carrying the real reviewed artifacts, nothing invented."""
    app_root = base / "candidate"
    for relative in (
        "deploy/control-center/nginx/v2.conf",
        "deploy/control-center/Dockerfile.edge",
        "deploy/nginx/dashboard-sidecar.conf",
        "control-center-v2/package-lock.json",
    ):
        target = app_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    (app_root / "deploy/control-center").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(OVERLAY, app_root / "deploy/control-center/docker-compose.v2.yml")
    (app_root / "docker-compose.yml").write_text(
        "services:\n  ohm-trade-agent:\n    image: ohm-trade-agent:latest\n",
        encoding="utf-8",
    )
    return app_root


def _harness(tmp_path: Path, **overrides: object) -> dict[str, object]:
    staging = tmp_path / "staging"
    base = staging / SHA
    base.mkdir(parents=True, exist_ok=True)
    candidate = _install_tree(base / "tree")
    (base / "provenance.env").write_text(
        f"tree={'b' * 40}\narchive_digest={'c' * 64}\nmanifest_digest={'d' * 64}\n",
        encoding="utf-8",
    )
    app = tmp_path / "app"
    app.mkdir()
    (app / "docker-compose.yml").write_text(
        "services:\n  ohm-trade-agent:\n    image: ohm-trade-agent:latest\n",
        encoding="utf-8",
    )
    (app / ".env.core").write_text("OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\n", encoding="utf-8")
    certs = tmp_path / "certs"
    live = certs / "live" / "161-35-106-207.sslip.io"
    live.mkdir(parents=True)
    (live / "fullchain.pem").write_text("cert\n", encoding="utf-8")
    (live / "privkey.pem").write_text("key\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exec(bin_dir / "docker", DOCKER_SHIM)
    _write_exec(bin_dir / "curl", CURL_SHIM)
    _write_exec(bin_dir / "df", DF_SHIM)
    state = tmp_path / "ui"
    log = tmp_path / "docker.log"
    python = __import__("sys").executable
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        "STATE_DIR": str(tmp_path / "state"),
        "APP_ROOT": str(app),
        "TARGET_SHA": SHA,
        "PYTHON_BIN": python,
        "OPIP_STAGING_DIR": str(staging),
        "OPIP_UI_STATE_DIR": str(state),
        "OPIP_UI_STAGED_APP": str(candidate),
        "OPIP_UI_CERT_DIR": str(certs),
        "OPIP_UI_MIN_FREE_KB": "1",
        "OPIP_UI_MIN_AVAIL_KB": "1",
        "OPIP_UI_WAIT_TRIES": "1",
        "OPIP_UI_HTTP_TIMEOUT": "1",
        "OPIP_UI_ATTEMPT": "t0001",
        "FAKE_DOCKER_LOG": str(log),
        "FAKE_PYTHON": python,
        "FAKE_TREE": str(candidate),
        "FAKE_SHA": SHA,
    }
    for key, value in overrides.items():
        env[key] = str(value)
    return {"env": env, "log": log, "state": state, "candidate": candidate, "app": app}


def _run(harness: dict[str, object], mode: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            shutil.which("bash") or "bash",
            "-c",
            'set -euo pipefail; source "$1"; opip_ui_run "$2"',
            "bash",
            str(LIB),
            mode,
        ],
        capture_output=True,
        text=True,
        timeout=300,
        env={**os.environ, **harness["env"]},
    )


def _log_lines(harness: dict[str, object]) -> list[str]:
    log = harness["log"]
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _receipts(harness: dict[str, object]) -> str:
    receipts = sorted((harness["state"] / "receipts").glob("*.env"))
    assert receipts, "no receipt was written"
    return receipts[-1].read_text(encoding="utf-8")



@pytest.mark.acceptance
def test_failed_preflight_blocks_cutover_without_mutation(tmp_path):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-003: fail closed, no mutation."""
    if not _bash_ready():
        pytest.skip("a usable bash is not available in this environment")
    harness = _harness(tmp_path, FAKE_NGINX_RC="1")
    result = _run(harness, "ui-deploy")
    assert result.returncode == 65, result.stderr
    assert "OPIP_UI_RESULT=BLOCKED" in result.stdout
    assert "OPIP_UI_LIVE_RELEASE=UNCHANGED" in result.stdout
    lines = _log_lines(harness)
    assert not [line for line in lines if line.startswith("build")]
    assert not [line for line in lines if " up -d " in f" {line} "]
    receipt = _receipts(harness)
    assert "result=BLOCKED" in receipt
    assert "NGINX_SYNTAX_FAILED" in receipt
    assert "rollback=NOT_REQUIRED" in receipt


@pytest.mark.acceptance
def test_successful_cutover_recreates_only_the_edge_and_records_a_receipt(tmp_path):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-005: edge-only, receipted."""
    if not _bash_ready():
        pytest.skip("a usable bash is not available in this environment")
    harness = _harness(tmp_path)
    result = _run(harness, "ui-deploy")
    assert result.returncode == 0, result.stderr
    assert "OPIP_UI_RESULT=DEPLOYED" in result.stdout
    up_lines = [line for line in _log_lines(harness) if " up " in f" {line} "]
    assert len(up_lines) == 1, up_lines
    for line in up_lines:
        assert "--no-deps" in line
        assert "--no-build" in line
        assert "ohm-dashboard-edge" in line
        assert "--remove-orphans" not in line
        for service in FORBIDDEN_SERVICES:
            assert service not in line
    build_lines = [line for line in _log_lines(harness) if line.startswith("build")]
    assert build_lines and str(harness["candidate"]) in build_lines[0]
    receipt = _receipts(harness)
    assert f"sha={SHA}" in receipt
    assert "result=DEPLOYED" in receipt
    assert "rollback=NONE" in receipt
    assert (harness["state"] / "active.env").is_file()
    assert (harness["state"] / "previous-edge.env").is_file()


@pytest.mark.acceptance
def test_post_cutover_failure_restores_the_previous_edge(tmp_path):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-004: automatic restore."""
    if not _bash_ready():
        pytest.skip("a usable bash is not available in this environment")
    harness = _harness(tmp_path, FAKE_POST_FAIL="1")
    result = _run(harness, "ui-deploy")
    assert result.returncode == 70, result.stderr
    assert "OPIP_UI_RESULT=DEPLOY_FAILED" in result.stdout
    assert "OPIP_UI_ROLLBACK=SUCCESS" in result.stdout
    up_lines = [line for line in _log_lines(harness) if " up " in f" {line} "]
    assert len(up_lines) == 2, up_lines
    assert "docker-compose.v2.yml" in up_lines[0]
    assert "docker-compose.v2.yml" not in up_lines[1]
    for line in up_lines:
        assert "--no-deps" in line
        assert "--remove-orphans" not in line
        for service in FORBIDDEN_SERVICES:
            assert service not in line
    receipt = _receipts(harness)
    assert "result=DEPLOY_FAILED" in receipt
    assert "rollback=SUCCESS" in receipt
    assert "V2_INDEX_STATUS:500" in receipt
    assert not (harness["state"] / "active.env").exists()


@pytest.mark.acceptance
def test_rollback_refuses_a_sha_that_is_not_the_active_release(tmp_path):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-004: bounded rollback target."""
    if not _bash_ready():
        pytest.skip("a usable bash is not available in this environment")
    harness = _harness(tmp_path)
    state = harness["state"]
    state.mkdir(parents=True, exist_ok=True)
    (state / "active.env").write_text(f"schema=1\nsha={'e' * 40}\n", encoding="utf-8")
    (state / "previous-edge.env").write_text("schema=1\nimage=nginx:1.27-alpine\n", encoding="utf-8")
    result = _run(harness, "rollback-ui")
    assert result.returncode == 65, result.stderr
    assert "OPIP_UI_RESULT=ROLLBACK_REFUSED" in result.stdout
    assert "ACTIVE_UI_SHA_MISMATCH" in result.stdout
    assert not [line for line in _log_lines(harness) if " up " in f" {line} "]
    receipt = _receipts(harness)
    assert "result=ROLLBACK_REFUSED" in receipt


@pytest.mark.acceptance
def test_legacy_surface_isolation_and_receipts_are_preserved():
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-005: legacy stays intact."""
    lib = _text(LIB)
    readme = _text(README)
    assert "Intelligence Cockpit" in lib
    assert "LEGACY_DASHBOARD_REDIRECT_CHANGED" in lib
    assert "OPIP_UI_TRADEVISOR_VHOST" in lib
    assert "/webhooks/tradevisor/v1" in lib
    assert "API_METHOD_NOT_DENIED" in lib
    assert "67108864|67108864|128|true" in lib
    assert "EDGE_PORTS_CHANGED" in lib
    assert "receipts" in lib and "history.log" in lib
    assert "never creates a new port" in readme.lower()
    assert "/deploy-ui" in readme and "/rollback-ui" in readme
    # The prohibitions are documented in comments; no executable line may carry
    # them.
    code = [line for line in lib.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert not [line for line in code if "--remove-orphans" in line]
    assert not [line for line in code if "docker compose -f deploy/control-center" in line]

