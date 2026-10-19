"""R1 operational scenarios: fresh host, prerequisites, overlay, rollback,
remote evidence, TLS, Telegram tracking and uniqueness.

Maps to mandated scenarios 1, 2, 3, 4, 10, 11, 12, 13, 14 and 15.
"""

from __future__ import annotations

import os
from pathlib import Path

import opip_recovery as recovery
import opip_signals as signals

R1 = Path(__file__).resolve().parents[2]
BASE_COMPOSE = Path(os.environ.get("OPIP_CERTIFIED_SRC", r"C:\Users\ohmpr\opip-mission-07\src")) / \
    "OHM-Trade-Agent-v1" / "docker-compose.yml"


def _host(tmp_path: Path, *, env: bool = True, head: bool = True) -> Path:
    app = tmp_path / "app"
    (app / ".git").mkdir(parents=True)
    (app / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    if head:
        (app / ".git" / "HEAD").write_text("c196dd674678b3b1ee91c351ee97c1c9e2b3938d\n", encoding="utf-8")
    if env:
        (app / ".env").write_text("WEBHOOK_SECRET=placeholder-value\n", encoding="utf-8")
    return app


def test_fresh_host_without_container_is_classified_for_bootstrap(tmp_path):
    """Scenario 1: a brand-new server must use bootstrap, never the controller."""
    app = _host(tmp_path)
    result = recovery.classify_fresh_host(app_root=app, state_dir=tmp_path / "state", container_present="no")
    assert result["status"] == "FRESH_READY"
    assert result["bootstrap_allowed"] is True
    assert result["controller_allowed"] is False
    assert result["preflight_required"] is True


def test_fresh_host_with_incumbent_container_is_not_bootstrap(tmp_path):
    app = _host(tmp_path)
    result = recovery.classify_fresh_host(app_root=app, state_dir=tmp_path / "state", container_present="yes")
    assert result["status"] == "INCUMBENT_PRESENT"
    assert result["bootstrap_allowed"] is False
    assert result["controller_allowed"] is True


def test_unproven_container_presence_is_not_assumed(tmp_path):
    app = _host(tmp_path)
    result = recovery.classify_fresh_host(app_root=app, state_dir=tmp_path / "state")
    assert result["status"] == "UNKNOWN"
    assert result["bootstrap_allowed"] is False


def test_missing_git_checkout_or_env_blocks_bootstrap(tmp_path):
    """Scenario 2."""
    no_git = tmp_path / "no-git"
    no_git.mkdir()
    (no_git / ".env").write_text("x=1\n", encoding="utf-8")
    assert recovery.classify_fresh_host(app_root=no_git, state_dir=tmp_path / "s1",
                                        container_present="no")["status"] == "MISSING_GIT_CHECKOUT"

    app = _host(tmp_path / "host2", env=False)
    assert recovery.classify_fresh_host(app_root=app, state_dir=tmp_path / "s2",
                                        container_present="no")["status"] == "MISSING_ENV"


def test_legacy_paper_service_selection_is_refused():
    """Scenario 3."""
    bad = recovery.classify_paper_topology(["ohm-trade-agent", "ohm-freqtrade-paper-usdt"])
    assert bad["verdict"] == "REFUSED"
    assert bad["paper_services"] == ["ohm-freqtrade-paper-usdt"]
    assert recovery.classify_paper_topology(["ohm-trade-agent", "opip-canonical-writer"])["verdict"] == "PASS"


def test_overlay_pair_guards(tmp_path):
    """Scenario 4: missing overlay and wrong overlay values both fail closed."""
    overlay = R1 / "drafts" / "docker-compose.v2.yml"
    good = recovery.verify_overlay_pair(BASE_COMPOSE, overlay)
    assert good["verdict"] == "PASS", [c for c in good["checks"] if c["status"] == "FAIL"]
    assert recovery.verify_overlay_pair(BASE_COMPOSE, tmp_path / "absent.yml")["verdict"] == "FAIL"

    wrong = tmp_path / "wrong.yml"
    wrong.write_text(overlay.read_text(encoding="utf-8").replace("mem_limit: 128m", "mem_limit: 256m"),
                     encoding="utf-8")
    assert recovery.verify_overlay_pair(BASE_COMPOSE, wrong)["verdict"] == "FAIL"
def test_rollback_plan_is_durable_and_independent_of_transient_override(tmp_path):
    """Scenario 10: rollback after a successful deploy."""
    result = recovery.plan_rollback(durable_dir=tmp_path / "durable",
                                    previous_sha="f" * 40, current_epoch=2, rollback_epoch=2)
    assert result["verdict"] == "PASS"
    overlay = Path(result["durable_overlay"])
    assert overlay.is_file()
    assert result["depends_on_transient_override"] is False
    assert "safe-baseline-rollback.override.yml" not in str(overlay)
    assert overlay.read_text(encoding="utf-8").count("OPIP_PAPER_V2_MODE") == 1
    assert any("opip-cutover-evidence" in step for step in result["steps"])


def test_rollback_refuses_newer_history_and_bad_profile(tmp_path):
    """Scenarios 9 and 11."""
    newer = recovery.plan_rollback(durable_dir=tmp_path / "d1", previous_sha="a" * 40,
                                   current_epoch=5, rollback_epoch=2)
    assert newer["verdict"] == "REFUSED"
    assert any("newer" in reason for reason in newer["refusals"])

    malformed = recovery.plan_rollback(durable_dir=tmp_path / "d2", previous_sha="not-a-sha")
    assert malformed["verdict"] == "REFUSED"


def test_ssh_evidence_requires_approved_account_and_exit_zero():
    """Scenario 12."""
    good = [{"user": "opipadmin", "exit_code": 0, "command": "hostname", "evidence": {"hostname": "OPip-Trade-AI"}}]
    assert recovery.evaluate_ssh_evidence(good)["verdict"] == "PASS"

    wrong_user = recovery.evaluate_ssh_evidence(
        [{"user": "root", "exit_code": 0, "command": "hostname", "evidence": {"hostname": "x"}}])
    assert wrong_user["verdict"] == "FAIL"
    assert any("wrong ssh account" in p for p in wrong_user["problems"])

    failed_cmd = recovery.evaluate_ssh_evidence(
        [{"user": "opipadmin", "exit_code": 255, "command": "docker ps", "evidence": None}])
    assert failed_cmd["verdict"] == "FAIL"


def test_tls_prerequisites_block_https_activation():
    """Scenario 13."""
    missing = recovery.verify_tls_prerequisites({})
    assert missing["verdict"] == "REFUSED"
    assert len(missing["problems"]) >= 5

    ready = recovery.verify_tls_prerequisites({
        "dns_resolves_to_host": True, "port_80_reachable_from_internet": True,
        "certificate_present": True, "private_key_permissions_ok": True,
        "firewall_8443_restricted": True, "renewal_timer_active": True,
    })
    assert ready["verdict"] == "PASS"


def test_telegram_transport_success_without_tracked_receipt_fails():
    """Scenario 14: send succeeded, but no delivery evidence exists."""
    result = signals.evaluate_telegram_evidence(transport_probe={"ok": True, "message_id": 55}, events=[])
    assert result["verdict"] == "FAIL"
    assert result["transport_only"] is True
    assert result["actionable_signal_delivered"] is False

    events = [{"alert_id": "A-1", "status": "DELIVERED", "message_id": 55}]
    tracked = signals.evaluate_telegram_evidence(transport_probe={"ok": True, "message_id": 55}, events=events)
    assert tracked["verdict"] == "PASS"
    assert tracked["actionable_signal_delivered"] is True

    assert signals.evaluate_dedupe(identity="A-1", id_before=55, id_after=55)["verdict"] == "PASS"
    assert signals.evaluate_dedupe(identity="A-1", id_before=55, id_after=56)["verdict"] == "FAIL"
    assert signals.evaluate_dedupe(identity="A-1", id_before=55, id_after=56,
                                   expected_duplicate=True)["verdict"] == "PASS"
    untracked_row = signals.evaluate_telegram_evidence(
        transport_probe={"ok": True, "message_id": 55},
        events=[{"alert_id": "A-1", "status": "DELIVERED", "message_id": None}])
    assert untracked_row["verdict"] == "FAIL"


def test_pipeline_state_never_infers_signals_from_api_health():
    healthy_empty = signals.classify_pipeline_state(
        api_healthy=True, scheduler_ran=True, market_data_acquired=True, candidates_evaluated=True,
        qualified_actionable=0, delivered=0)
    assert healthy_empty["state"] == "SIGNAL_PIPELINE_HEALTHY_NO_ACTIONABLE_SIGNAL"

    api_only = signals.classify_pipeline_state(
        api_healthy=True, scheduler_ran=False, market_data_acquired=False, candidates_evaluated=False,
        qualified_actionable=0, delivered=0)
    assert api_only["state"] == "SIGNAL_PIPELINE_FAILED"
    assert api_only["stages"]["A_api_healthy"] is True


def test_two_writers_or_schedulers_are_refused():
    """Scenario 15."""
    good = {"canonical_writer_containers": 1, "core_containers": 1, "cycle_cron_lines": 1, "telegram_pollers": 1}
    assert recovery.verify_uniqueness(good)["verdict"] == "PASS"
    assert recovery.verify_uniqueness({**good, "canonical_writer_containers": 2})["verdict"] == "REFUSED"
    assert recovery.verify_uniqueness({**good, "cycle_cron_lines": 2})["verdict"] == "REFUSED"
    assert recovery.verify_uniqueness({**good, "telegram_pollers": 2})["verdict"] == "REFUSED"

