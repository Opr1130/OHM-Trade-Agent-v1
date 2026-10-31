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


def test_worker_has_its_own_bounded_memory_cgroup(compose, worker):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-001: own 96 MB cgroup, not the core's."""
    core = compose["services"][CORE]
    assert worker["mem_limit"] == "96m"
    assert worker["memswap_limit"] == "96m"
    assert worker["mem_limit"] != core["mem_limit"]
    assert core["mem_limit"] == "384m"
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
    assert "--profile irr-shadow run --rm --no-deps -T opip-irr-shadow" in text
    assert "docker compose exec" not in text
    schedule_line = next(
        line for line in text.splitlines() if line.startswith("* * * * * root")
    )
    # The wall-clock bound lives in the service command, inside its own cgroup.
    assert "timeout" not in schedule_line


def test_scheduler_authorization_is_explicit_and_absent_by_default(reconcile_text):
    """ATDD-IRR-ACTIVATION-INFRA-v1/AC-004: opt-in install, fail-closed token."""
    assert (
        'IRR_SHADOW_AUTH_FILE="/var/lib/ohm-deploy/irr-shadow-scheduler.enabled"'
        in reconcile_text
    )
    assert 'IRR_SHADOW_SRC="$APP_ROOT/deploy/cron.d/opip-irr-shadow"' in reconcile_text
    assert 'IRR_SHADOW_DST="/etc/cron.d/opip-irr-shadow"' in reconcile_text
    # Default posture is absent, and only an exact token authorizes installation.
    assert 'irr_shadow_state="ABSENT"' in reconcile_text
    assert 'installed) irr_shadow_state="INSTALLED" ;;' in reconcile_text
    assert 'absent) irr_shadow_state="ABSENT" ;;' in reconcile_text
    assert "IRR shadow scheduler authorization value is invalid" in reconcile_text
    assert "IRR shadow scheduler authorization is not a readable regular file" in (
        reconcile_text
    )
    # The artifact is required only when it is authorized.
    assert 'required_artifacts+=("$IRR_SHADOW_SRC")' in reconcile_text


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
    rollback_removal = reconcile_text.index('rm -f "$IRR_SHADOW_DST"')

    # The snapshot precedes the rollback handler, the handler is armed before the
    # install, and the handler both restores a previous entry and removes a
    # first-time one.
    assert snapshot < rollback_definition < trap_armed < install
    assert rollback_definition < restore
    assert restore < rollback_removal
    # Unauthorized reconciliation removes any stale entry.
    unauthorized_block = "\n".join(
        (
            'if [[ "$irr_shadow_state" == "INSTALLED" ]]; then',
            '  install -o root -g root -m 0644 "$IRR_SHADOW_SRC" "$IRR_SHADOW_DST"',
            "else",
            '  rm -f "$IRR_SHADOW_DST"',
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


