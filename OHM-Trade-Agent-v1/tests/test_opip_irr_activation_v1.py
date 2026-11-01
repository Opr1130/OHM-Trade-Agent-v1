"""ATDD: isolated IRR activation infrastructure (compose worker + scheduler).

Focused coverage for the activation-infrastructure increment:

* the worker has its own bounded memory cgroup and never shares the core's;
* it receives no funded, exchange, notification or core-authority credential;
* it mounts only the dedicated IRR state subtree;
* the cron entry invokes the isolated worker, never `compose exec` in the core;
* scheduler installation is explicitly authorized, snapshotted, rolled back and
  removed, and is absent by default;
* `OPIP_IRR_MODE=off` remains the default posture.

These are structural assertions over the reviewed artifacts, matching the
existing `tests/test_production_scheduler_v1.py` convention. The shell script's
own syntax is validated by CI (`bash -n`) on Linux.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from app.services import independent_research_review as irr


pytestmark = pytest.mark.acceptance

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
CRON = ROOT / "deploy/cron.d/opip-irr-shadow"
RECONCILE = ROOT / "deploy/remote/reconcile-scheduler.sh"

WORKER = "opip-irr-shadow"
CORE = "ohm-trade-agent"
AUTH_LIB = ROOT / "deploy" / "remote" / "lib" / "opip_scheduler_authorization.sh"
RUNNER_SRC = ROOT / "deploy" / "bin" / "opip-irr-shadow-run"
DEPLOY = ROOT / "deploy" / "remote" / "ohm-deploy"
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash unavailable")

#: Credentials the advisory worker must never receive.
FORBIDDEN_CREDENTIALS = (
    "KRAKEN_API_KEY",
    "KRAKEN_API_SECRET",
    "KRAKEN_PRIVATE_KEY",
    "KRKN_API_KEY",
    "LIVE_TRADING_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "OPENAI_API_KEY",
    "DIGITALOCEAN_INFERENCE_KEY",
    "DIGITALOCEAN_TOKEN",
    "OPIP_TRADEVISOR_WEBHOOK_TOKEN",
    "OPIP_TRADEVISOR_INTERNAL_VERIFICATION_VALUE",
)

#: Core authority switches the advisory worker must not inherit.
FORBIDDEN_AUTHORITY_SWITCHES = (
    "OPIP_RELEASE_PROFILE",
    "OPIP_FEATURE_BUS_MODE",
    "OPIP_CANONICAL_WRITER_MODE",
    "OPIP_TARGET_SPINE_MODE",
    "OPIP_PAPER_V2_MODE",
    "OPIP_COMMITTEE_MODE",
)


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def worker(compose) -> dict:
    return compose["services"][WORKER]


@pytest.fixture(scope="module")
def cron_text() -> str:
    return CRON.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def reconcile_text() -> str:
    return RECONCILE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def auth_lib_text() -> str:
    return AUTH_LIB.read_text(encoding="utf-8")


def test_worker_has_its_own_bounded_memory_cgroup(compose, worker):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-001: own 96 MB cgroup, not the core's."""
    core = compose["services"][CORE]
    assert worker["mem_limit"] == "96m"
    assert worker["memswap_limit"] == "96m"
    assert worker["mem_limit"] != core["mem_limit"]
    assert core["mem_limit"] == "640m"
    assert worker["restart"] == "no"
    assert worker["profiles"] == ["irr-shadow"]
    assert worker["read_only"] is True
    assert worker["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in worker["security_opt"]
    assert int(worker["pids_limit"]) <= 128
    # An advisory OOM must be the first victim, never the core or protection.
    assert int(worker["oom_score_adj"]) >= 300
    assert worker["tmpfs"] == ["/tmp"]
    assert worker["stop_grace_period"] == "20s"


def test_worker_command_is_the_bounded_drain_job(worker, cron_text):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-001: bounded job, run in its own container."""
    command = worker["command"]
    assert command[0] == "timeout"
    assert "--signal=TERM" in command
    assert "--kill-after=5s" in command
    assert "150" in command
    assert command[-3:] == ["python", "-m", "app.jobs.drain_irr_shadow"]
    assert "compose exec" not in cron_text
    assert CORE not in cron_text


def test_worker_receives_no_funded_or_core_authority_credentials(worker):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-002: credential and authority isolation."""
    assert "env_file" not in worker
    keys = set(worker["environment"])
    assert not (keys & set(FORBIDDEN_CREDENTIALS))
    assert not (keys & set(FORBIDDEN_AUTHORITY_SWITCHES))
    # Exactly one secret, plus the bounded OPIP_IRR_* controls.
    secrets = {
        key
        for key in keys
        if key.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_KEY"))
        and key != "DEEPSEEK_API_KEY"
    }
    assert secrets == set()
    assert "DEEPSEEK_API_KEY" in keys
    assert worker["environment"]["DEEPSEEK_API_KEY"] == "${DEEPSEEK_API_KEY:-}"
    # The endpoint and model are pinned literals: a stale .env cannot widen them.
    assert worker["environment"]["OPIP_IRR_BASE_URL"] == "https://api.deepseek.com"
    assert worker["environment"]["OPIP_IRR_MODEL"] == "deepseek-flash"


def test_worker_mounts_only_the_irr_state_subtree(worker):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-002: no alert/order/canonical registry."""
    assert worker["volumes"] == ["./data/irr:/app/data/irr"]
    joined = " ".join(worker["volumes"])
    for forbidden in (
        "/app/data:",
        "freqtrade",
        "kraken",
        "canonical",
        "paper",
        "alert_state",
        "/app/data/opip",
    ):
        assert forbidden not in joined
    assert irr.IRR_STATE_DIR == Path("/app/data/irr")
    for path in (
        irr.QUEUE_FILE,
        irr.QUEUE_CHECKPOINT_FILE,
        irr.EVIDENCE_FILE,
        irr.SPEND_LEDGER_FILE,
        irr.IDEMPOTENCE_FILE,
    ):
        assert path.parent == irr.IRR_STATE_DIR


def test_cron_entry_runs_the_isolated_worker():
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-003: isolated invocation, bounded and locked."""
    text = CRON.read_text(encoding="utf-8")
    assert "* * * * * root" in text
    assert "flock -n /var/run/opip-irr-shadow.lock" in text
    # The schedule delegates to the verified runner, which carries the isolated
    # invocation; the entry itself names no container and cannot build.
    assert "/usr/local/bin/opip-irr-shadow-run" in text
    assert "docker compose exec" not in text
    schedule_line = next(
        line for line in text.splitlines() if line.startswith("* * * * * root")
    )
    assert "docker compose" not in schedule_line
    # The wall-clock bound lives in the service command, inside its own cgroup.
    assert "timeout" not in schedule_line


def test_scheduler_authorization_is_explicit_and_absent_by_default(
    reconcile_text, auth_lib_text
):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-004: opt-in install, fail-closed token."""
    assert (
        'IRR_SHADOW_AUTH_FILE="/var/lib/ohm-deploy/irr-shadow-scheduler.enabled"'
        in reconcile_text
    )
    assert 'IRR_SHADOW_SRC="$APP_ROOT/deploy/cron.d/opip-irr-shadow"' in reconcile_text
    assert 'IRR_SHADOW_DST="/etc/cron.d/opip-irr-shadow"' in reconcile_text
    # Default posture is absent; only the marker contract can authorize install.
    assert 'irr_shadow_state="ABSENT"' in reconcile_text
    assert 'INSTALLED) irr_shadow_state="INSTALLED" ;;' in reconcile_text
    assert 'ABSENT) irr_shadow_state="ABSENT" ;;' in reconcile_text
    assert "opip_scheduler_authorization_state" in reconcile_text
    # The exact-token contract lives in the sourced library.
    assert "installed | installed$'\\n')" in auth_lib_text
    assert "absent | absent$'\\n')" in auth_lib_text
    assert (
        "must be exactly 'installed' or 'absent' (optional final newline)"
        in auth_lib_text
    )
    # The artifacts are required only when the entry is authorized.
    assert (
        'required_artifacts+=("$IRR_SHADOW_SRC" "$IRR_RUNNER_SRC")' in reconcile_text
    )


def test_scheduler_snapshots_installs_rolls_back_and_removes(reconcile_text):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-004: snapshot, rollback, removal ordering."""
    rollback_definition = reconcile_text.index("rollback() {")
    trap_armed = reconcile_text.index("trap rollback ERR")
    snapshot = reconcile_text.index(
        'cp -a "$IRR_SHADOW_DST" "$tmpdir/irr-shadow.before"'
    )
    install = reconcile_text.index(
        'install -o root -g root -m 0644 "$IRR_SHADOW_SRC" "$IRR_SHADOW_DST"'
    )
    restore = reconcile_text.index(
        'cp -a "$tmpdir/irr-shadow.before" "$IRR_SHADOW_DST"'
    )
    # The first removal *inside* the rollback handler, not an earlier refusal path.
    rollback_removal = reconcile_text.index(
        'rm -f "$IRR_SHADOW_DST"', rollback_definition
    )

    # The snapshot precedes the rollback handler, the handler is armed before the
    # install, and the handler both restores a previous entry and removes a
    # first-time one.
    assert snapshot < rollback_definition < trap_armed < install
    assert rollback_definition < restore
    assert restore < rollback_removal
    # Unauthorized reconciliation removes the entry, the runner and the pin.
    unauthorized_block = "\n".join(
        (
            "else",
            '  rm -f "$IRR_SHADOW_DST"',
            '  rm -f "$IRR_RUNNER_DST"',
            '  rm -f "$IRR_WORKER_PIN"',
            "fi",
        )
    )
    assert unauthorized_block in reconcile_text
    # The entry must never be re-pointed at the core container.
    assert "IRR shadow scheduler entry must not exec inside the core container" in (
        reconcile_text
    )
    assert "IRR shadow scheduler entry exists without authorization" in reconcile_text


def test_irr_remains_disabled_by_default(monkeypatch, tmp_path, worker, reconcile_text):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-005: default OFF everywhere."""
    monkeypatch.delenv("OPIP_IRR_MODE", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(irr, "QUEUE_FILE", tmp_path / "queue.jsonl")
    monkeypatch.setattr(irr, "QUEUE_LOCK", tmp_path / ".queue.lock")
    monkeypatch.setattr(irr, "QUEUE_CHECKPOINT_FILE", tmp_path / "checkpoint.json")
    monkeypatch.setattr(irr, "EVIDENCE_FILE", tmp_path / "evidence.jsonl")
    monkeypatch.setattr(irr, "EVIDENCE_LOCK", tmp_path / ".evidence.lock")
    monkeypatch.setattr(irr, "SPEND_LEDGER_FILE", tmp_path / "spend.jsonl")
    monkeypatch.setattr(irr, "SPEND_LEDGER_LOCK", tmp_path / ".spend.lock")

    assert irr.irr_mode() == "off"
    assert irr.drain_queue()["status"] == irr.STATUS_DISABLED
    assert not irr.QUEUE_FILE.exists()
    assert not irr.SPEND_LEDGER_FILE.exists()
    # Compose keeps the operator's value, defaulting to off; the scheduler needs
    # an explicit authorization marker before it installs anything.
    assert worker["environment"]["OPIP_IRR_MODE"] == "${OPIP_IRR_MODE:-off}"
    assert 'irr_shadow_state="ABSENT"' in reconcile_text


# ---------------------------------------------------------------------------
# AC-007: prepared image, verified before installation, never built by a tick
# ---------------------------------------------------------------------------


def test_worker_image_is_prepared_by_the_deploy_from_the_release_sha(worker):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-007: deterministic alias + deploy build."""
    # A stable alias is what the pin is recorded against; `build: .` keeps the
    # isolation definition single-sourced in Compose.
    assert worker["image"] == "opip-irr-shadow:worker"
    assert worker["build"] == "."

    deploy = DEPLOY.read_text(encoding="utf-8")
    # Prepared on the success path from the approved target SHA...
    assert (
        'build --build-arg "OPIP_RELEASE_SHA=$TARGET_SHA" \\\n'
        "  opip-canonical-writer ohm-trade-agent opip-irr-shadow"
    ) in deploy
    # ...and on both rollback paths from the restored previous SHA, so a rollback
    # re-provisions the worker from the code it restores - but only when the
    # restored release declares the service (AC-009).
    assert (
        'build --build-arg "OPIP_RELEASE_SHA=$PREVIOUS_SHA" \\\n'
        '      opip-canonical-writer ohm-trade-agent "${irr_rollback_build_targets[@]}"'
    ) in deploy
    assert (
        'build --build-arg "OPIP_RELEASE_SHA=$PREVIOUS_SHA" ohm-trade-agent \\\n'
        '      "${irr_rollback_build_targets[@]}"'
    ) in deploy
    # The reconciliation receives the exact approved SHA it must verify against.
    assert 'OPIP_RELEASE_SHA="$TARGET_SHA" bash "$SCHEDULER_RECONCILE"' in deploy


def test_reconcile_verifies_the_image_before_installing_the_entry(reconcile_text):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-007: unverified image means no entry."""
    assert 'IRR_WORKER_ALIAS="opip-irr-shadow:worker"' in reconcile_text
    assert 'IRR_WORKER_PIN="/var/lib/ohm-deploy/irr-worker-image.env"' in reconcile_text
    assert (
        "docker image inspect --format '{{.Id}}' \"$IRR_WORKER_ALIAS\"" in reconcile_text
    )
    assert (
        '{{index .Config.Labels "org.opencontainers.image.revision"}}'
        in reconcile_text
    )
    assert (
        "IRR shadow worker image revision mismatch: expected=$irr_release_sha"
        in reconcile_text
    )
    assert (
        "IRR shadow worker image is not prepared: $IRR_WORKER_ALIAS is absent"
        in reconcile_text
    )
    assert (
        "IRR shadow worker image cannot be verified: the approved release SHA is "
        "not provable" in reconcile_text
    )
    # Verification happens before the snapshot/install block.
    verify_at = reconcile_text.index("OPIP_IRR_WORKER_IMAGE_VERIFIED")
    snapshot_at = reconcile_text.index(
        'cp -a "$IRR_SHADOW_DST" "$tmpdir/irr-shadow.before"'
    )
    install_at = reconcile_text.index(
        'install -o root -g root -m 0755 "$IRR_RUNNER_SRC" "$IRR_RUNNER_DST"'
    )
    assert verify_at < snapshot_at < install_at
    # The pin is root-owned, mode 0600, and part of the rollback set.
    assert 'chmod 0600 "$pin_staged"' in reconcile_text
    assert 'chown root:root "$pin_staged"' in reconcile_text
    assert "had_irr_pin=1" in reconcile_text
    assert 'cp -a "$tmpdir/irr-pin.before" "$IRR_WORKER_PIN"' in reconcile_text
    assert "had_irr_runner=1" in reconcile_text
    assert 'cp -a "$tmpdir/irr-runner.before" "$IRR_RUNNER_DST"' in reconcile_text


def test_scheduled_entry_delegates_to_the_verified_runner(cron_text, reconcile_text):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-007: a tick can never build an image."""
    schedule_line = next(
        line for line in cron_text.splitlines() if line.startswith("* * * * * root")
    )
    assert "/usr/local/bin/opip-irr-shadow-run" in schedule_line
    assert "docker compose" not in schedule_line
    assert "build" not in schedule_line

    runner = RUNNER_SRC.read_text(encoding="utf-8")
    assert "--build" not in runner
    # The pin, the alias and the label must all agree before anything runs.
    for code in (
        "PIN_UNREADABLE",
        "PIN_NOT_ROOT_OWNED",
        "PIN_MODE_UNSAFE",
        "PIN_ID_INVALID",
        "PIN_REVISION_INVALID",
        "IMAGE_PIN_MISMATCH",
        "IMAGE_LABEL_MISMATCH",
        "IMAGE_UNAVAILABLE",
    ):
        assert code in runner, code
    assert 'alias_id" == "$pinned_id' in runner
    assert 'alias_revision" == "$pinned_revision' in runner
    assert runner.rstrip().endswith('-T "$SERVICE"')


# ---------------------------------------------------------------------------
# AC-008: authorization marker ownership, mode and exact token
# ---------------------------------------------------------------------------


def _marker(tmp_path: Path, content: bytes, mode: int = 0o644) -> Path:
    path = tmp_path / "marker"
    path.write_bytes(content)
    os.chmod(path, mode)
    return path


def _authorization_state(path: Path, allowed_uid: int | None = None):
    env = dict(os.environ)
    env["OPIP_DEPLOY_TEST_SEAMS"] = "1"
    if allowed_uid is not None:
        env["OPIP_SCHEDULER_AUTH_ALLOWED_UID"] = str(allowed_uid)
    return subprocess.run(
        [
            BASH,
            "-c",
            'source "$1"; opip_scheduler_authorization_state "$2"',
            "_",
            str(AUTH_LIB),
            str(path),
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


@needs_bash
@pytest.mark.parametrize(
    "content", [b"installed", b"installed\n", b"absent", b"absent\n"]
)
def test_marker_accepts_only_the_two_exact_tokens(tmp_path, content):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-008: exact token, optional final newline."""
    path = _marker(tmp_path, content, mode=0o600)
    result = _authorization_state(path, allowed_uid=os.getuid())
    assert result.returncode == 0, result.stderr
    expected = "INSTALLED" if content.startswith(b"installed") else "ABSENT"
    assert result.stdout.strip() == expected


@needs_bash
@pytest.mark.parametrize(
    "content",
    [
        b"installed ",
        b" installed",
        b"installed\r\n",
        b"installed\n\n",
        b"INSTALLED\n",
        b"yes\n",
        b"",
        b"installed-installed\n",
    ],
)
def test_marker_rejects_malformed_tokens(tmp_path, content):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-008: malformed marker content fails closed."""
    path = _marker(tmp_path, content, mode=0o600)
    result = _authorization_state(path, allowed_uid=os.getuid())
    assert result.returncode == 65
    assert result.stdout.strip() == ""


@needs_bash
@pytest.mark.parametrize("mode", [0o666, 0o664, 0o662, 0o777, 0o755, 0o700])
def test_marker_rejects_unsafe_permissions(tmp_path, mode):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-008: unsafe marker modes are refused."""
    path = _marker(tmp_path, b"installed\n", mode=mode)
    result = _authorization_state(path, allowed_uid=os.getuid())
    assert result.returncode == 65
    assert result.stdout.strip() == ""


@needs_bash
def test_marker_accepts_safe_permissions(tmp_path):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-008: 0600/0644 remain acceptable."""
    for mode in (0o600, 0o644, 0o400):
        path = _marker(tmp_path, b"installed\n", mode=mode)
        result = _authorization_state(path, allowed_uid=os.getuid())
        assert result.returncode == 0, (mode, result.stderr)
        path.unlink()


@needs_bash
def test_marker_rejects_symlinks_directories_and_foreign_owners(tmp_path):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-008: type and ownership are enforced."""
    target = _marker(tmp_path, b"installed\n", mode=0o600)
    link = tmp_path / "link"
    link.symlink_to(target)
    assert _authorization_state(link, allowed_uid=os.getuid()).returncode == 65

    directory = tmp_path / "dir"
    directory.mkdir()
    assert _authorization_state(directory, allowed_uid=os.getuid()).returncode == 65

    if os.geteuid() != 0:
        # A marker the caller does not own (root, by contract) is refused.
        assert _authorization_state(target, allowed_uid=0).returncode == 65


@needs_bash
def test_marker_absence_means_not_installed(tmp_path):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-008: no marker is the default posture."""
    missing = tmp_path / "missing"
    result = _authorization_state(missing, allowed_uid=os.getuid())
    assert result.returncode == 0
    assert result.stdout.strip() == "ABSENT"


def test_reconcile_delegates_the_marker_contract_to_the_library(reconcile_text):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-008: one hardened parser, no loose parse."""
    assert 'source "$IRR_AUTH_LIB"' in reconcile_text
    assert "opip_scheduler_authorization_state" in reconcile_text
    # The previous permissive CR/LF stripping must be gone.
    assert "tr -d '\\r\\n'" not in reconcile_text
    # A missing library fails closed before any mutation.
    assert "scheduler authorization library missing" in reconcile_text
    assert "does not define $irr_contract" in reconcile_text

    assert 'install -o root -g root -m 0755 "$IRR_RUNNER_SRC" "$IRR_RUNNER_DST"' in (
        reconcile_text
    )





# ---------------------------------------------------------------------------
# AC-009: a rollback restores a release that may predate the IRR worker
# ---------------------------------------------------------------------------

ROLLBACK_DETECTION_START = "# >>> opip-irr-rollback-detection"
ROLLBACK_DETECTION_END = "# <<< opip-irr-rollback-detection"


def _sentinel_block(text: str, start: str, end: str) -> str:
    """Return the shipped shell between two sentinel comments."""
    begin = text.index(start) + len(start)
    return text[begin : text.index(end, begin)]


def _rollback_section(deploy_text: str) -> str:
    begin = deploy_text.index("rollback() {")
    return deploy_text[begin : deploy_text.index("trap rollback ERR", begin)]


def test_rollback_only_requests_the_irr_service_the_restored_release_declares():
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-009: c60bbbd1, which has no IRR service."""
    deploy = DEPLOY.read_text(encoding="utf-8")
    rollback_body = _rollback_section(deploy)
    assert ROLLBACK_DETECTION_START in rollback_body
    assert ROLLBACK_DETECTION_END in rollback_body
    # No rollback build may list the worker unconditionally: both build calls
    # consume the detected targets instead.
    assert "ohm-trade-agent opip-irr-shadow" not in rollback_body
    assert rollback_body.count('"${irr_rollback_build_targets[@]}"') == 2
    # Both rollback builds still rebuild from the restored release SHA.
    assert rollback_body.count(
        'build --build-arg "OPIP_RELEASE_SHA=$PREVIOUS_SHA"'
    ) == 2
    # A restored release without the worker removes the activation it inherited
    # from the abandoned candidate release: entry, runner and pin together.
    assert "OPIP_ROLLBACK_IRR_ACTIVATION=REMOVED" in rollback_body
    for artifact in (
        "/etc/cron.d/opip-irr-shadow",
        "/usr/local/bin/opip-irr-shadow-run",
        "/var/lib/ohm-deploy/irr-worker-image.env",
    ):
        assert artifact in rollback_body, artifact
    # The success path is unchanged: the target release always declares it.
    assert (
        'build --build-arg "OPIP_RELEASE_SHA=$TARGET_SHA" \\\n'
        "  opip-canonical-writer ohm-trade-agent opip-irr-shadow"
    ) in deploy


@needs_bash
@pytest.mark.parametrize(
    ("services", "expected"),
    [
        # A release that predates the worker declares neither service pairing
        # beyond the core and the writer.
        ("ohm-trade-agent\nopip-canonical-writer\n", []),
        (
            "ohm-trade-agent\nopip-canonical-writer\nopip-irr-shadow\n",
            ["opip-irr-shadow"],
        ),
        # An unresolvable Compose model is treated as "not declared", so the
        # rollback can never request a service that may not exist.
        ("", []),
    ],
)
def test_rollback_service_detection_executes_against_the_restored_model(
    tmp_path, services, expected
):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-009: the shipped detection block runs."""
    block = _sentinel_block(
        DEPLOY.read_text(encoding="utf-8"),
        ROLLBACK_DETECTION_START,
        ROLLBACK_DETECTION_END,
    )
    services_file = tmp_path / "services"
    services_file.write_text(services, encoding="utf-8")
    script = "\n".join(
        [
            'docker() { cat "$OPIP_TEST_SERVICES"; }',
            "SAFE_BASELINE_OVERRIDE=/dev/null",
            block,
            'printf "TARGETS=%s\\n" "${irr_rollback_build_targets[*]}"',
        ]
    )
    env = dict(os.environ)
    env["OPIP_TEST_SERVICES"] = str(services_file)
    result = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, env=env, check=False
    )
    assert result.returncode == 0, result.stderr
    targets = next(
        line.split("=", 1)[1]
        for line in result.stdout.splitlines()
        if line.startswith("TARGETS=")
    )
    assert targets.split() == expected
    marker = (
        "DECLARED_BY_RESTORED_RELEASE" if expected else "ABSENT_FROM_RESTORED_RELEASE"
    )
    assert marker in result.stdout


# ---------------------------------------------------------------------------
# AC-010: a failed image verification cannot drop uncaptured scheduler state
# ---------------------------------------------------------------------------

PREFLIGHT_CAPTURE = "opip_irr_capture_activation_state"
PREFLIGHT_RESTORE = "opip_irr_restore_activation_state"


def test_failed_image_verification_restores_the_captured_scheduler_state(
    reconcile_text,
):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-010: capture first, restore on failure."""
    capture_at = reconcile_text.index(f'{PREFLIGHT_CAPTURE} "$irr_preflight_dir"')
    verify_at = reconcile_text.index("docker image inspect --format '{{.Id}}'")
    # The capture precedes the very first verification.
    assert capture_at < verify_at
    # Nothing between capture and verification may delete the entry: a failure
    # used to `rm -f` a state whose previous value had never been recorded.
    assert 'rm -f "$IRR_SHADOW_DST"' not in reconcile_text[capture_at:verify_at]
    # Every failure path restores instead, and the reconciliation still fails
    # closed with the operator-visible marker.
    assert reconcile_text.count(f'{PREFLIGHT_RESTORE} "$irr_preflight_dir"') == 1
    helper_at = reconcile_text.index("irr_preflight_fail() {")
    gate_at = reconcile_text.index(
        'if [[ "$irr_shadow_state" == "INSTALLED" ]]; then', helper_at
    )
    fail_helper = reconcile_text[helper_at:gate_at]
    assert f"{PREFLIGHT_RESTORE} \"$irr_preflight_dir\"" in fail_helper
    assert "OPIP_IRR_IMAGE_PREFLIGHT=FAILED_STATE_PRESERVED" in fail_helper
    assert "exit 65" in fail_helper
    assert reconcile_text.count("    irr_preflight_fail \"") == 3
    # The failure messages the operator reads are unchanged.
    for message in (
        "IRR shadow worker image cannot be verified: the approved release SHA is not provable",
        "IRR shadow worker image is not prepared: $IRR_WORKER_ALIAS is absent",
        "IRR shadow worker image revision mismatch: expected=$irr_release_sha",
    ):
        assert message in reconcile_text, message
    # The capture directory is never leaked, on success or on the ERR rollback.
    assert reconcile_text.count('rm -rf "$irr_preflight_dir"') >= 3


@needs_bash
def test_activation_state_survives_a_failed_preflight_byte_for_byte(tmp_path):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-010: capture/restore keeps the state."""
    entry, runner, pin = (tmp_path / name for name in ("entry", "runner", "pin"))
    entry.write_text("* * * * * root /usr/local/bin/opip-irr-shadow-run\n", "utf-8")
    runner.write_text("#!/usr/bin/env bash\nexit 0\n", "utf-8")
    os.chmod(runner, 0o755)
    pin.write_text("image_id=sha256:aaa\nrevision=bbb\nalias=opip-irr-shadow:worker\n", "utf-8")
    capture = tmp_path / "capture"
    script = "\n".join(
        [
            "set -Eeuo pipefail",
            'source "$1"',
            'opip_irr_capture_activation_state "$2" "$3" "$4" "$5"',
            # Anything after the capture may destroy the state (that is what the
            # old preflight did); the restore must put it back exactly.
            'rm -f "$3" "$4" "$5"',
            'opip_irr_restore_activation_state "$2" "$3" "$4" "$5"',
        ]
    )
    result = subprocess.run(
        [BASH, "-c", script, "_", str(AUTH_LIB), str(capture), str(entry), str(runner), str(pin)],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert entry.read_text("utf-8") == "* * * * * root /usr/local/bin/opip-irr-shadow-run\n"
    assert runner.read_text("utf-8").endswith("exit 0\n")
    assert (runner.stat().st_mode & 0o111) == 0o111
    assert pin.read_text("utf-8").startswith("image_id=sha256:aaa\n")


@needs_bash
def test_state_absent_at_capture_stays_absent_after_a_failed_preflight(tmp_path):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-010: a first install is not half-created."""
    entry, runner, pin = (tmp_path / name for name in ("entry", "runner", "pin"))
    capture = tmp_path / "capture"
    script = "\n".join(
        [
            "set -Eeuo pipefail",
            'source "$1"',
            'opip_irr_capture_activation_state "$2" "$3" "$4" "$5"',
            # A partial install must not survive a failed verification.
            'printf x > "$3"',
            'printf x > "$4"',
            'printf x > "$5"',
            'opip_irr_restore_activation_state "$2" "$3" "$4" "$5"',
        ]
    )
    result = subprocess.run(
        [BASH, "-c", script, "_", str(AUTH_LIB), str(capture), str(entry), str(runner), str(pin)],
        capture_output=True,
        text=True,
        env=dict(os.environ),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert not entry.exists()
    assert not runner.exists()
    assert not pin.exists()
