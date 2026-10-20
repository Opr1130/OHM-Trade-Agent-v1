"""R2 deep-audit regressions.

Each test maps to an independently reproduced defect from the read-only audit /
owner verdict. These are behavioural or byte-level assertions; none of them can
pass vacuously (R2 explicitly removed the `or True` escape).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import opip_bundle as bundle
import opip_layout as layout
import opip_receipts as receipts
import opip_rollback_guard as guard

R1 = Path(__file__).resolve().parents[2]
BOOTSTRAP = R1 / "scripts" / "v2" / "bootstrap-v2.sh"
ROLLBACK = R1 / "scripts" / "v2" / "rollback-v2.sh"


def _fn(text: str, name: str) -> str:
    start = text.index(f"{name}() {{")
    depth = 0
    for offset, char in enumerate(text[start:]):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:start + offset + 1]
    raise AssertionError(f"function {name} is not brace-balanced")


@pytest.fixture(scope="module")
def bootstrap_text() -> str:
    return BOOTSTRAP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rollback_text() -> str:
    return ROLLBACK.read_text(encoding="utf-8")


# --- D02: one host layout -------------------------------------------------
def test_layout_contract_is_single_root():
    result = layout.validate_layout()
    assert result["verdict"] == "PASS", result["problems"]
    assert layout.APP_ROOT == "/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"
    assert layout.ENV_FILE.startswith(layout.APP_ROOT + "/")
    assert layout.LIVE_DB.startswith(layout.DATA_ROOT + "/")
    assert not layout.ENV_FILE.startswith(layout.RELEASE_SOURCE_ROOT)


def test_bootstrap_uses_exactly_one_env_and_compose_root(bootstrap_text):
    assert 'ENV_FILE="$APP_ROOT/.env"' in bootstrap_text
    assert '"$SRC/.env"' not in bootstrap_text, "a second .env in the release is forbidden"
    # R3: byte-for-byte the controller's invocation - bare `docker compose` in APP_ROOT.
    assert '( cd "$APP_ROOT" && docker compose "$@" )' in bootstrap_text
    assert 'docker compose -f' not in bootstrap_text, "no overlay may be layered on the core stack"
    assert "COMPOSE_FILE" not in bootstrap_text


def test_bootstrap_resolves_the_release_without_guessing(bootstrap_text):
    resolve = _fn(bootstrap_text, "resolve_src")
    assert "OHM-Trade-Agent-v1/docker-compose.yml" in resolve
    assert resolve.count("die ") == 1, "exactly one fail-closed branch"


# --- D01: writer ordering -------------------------------------------------
def test_execute_never_starts_writer_or_scheduler(bootstrap_text):
    execute = _fn(bootstrap_text, "mode_execute")
    assert "up -d --no-deps opip-canonical-writer" not in execute
    # A presence check is allowed; an invocation is not.
    assert 'run "scheduler reconciliation"' not in execute
    assert 'bash "$APP_ROOT/deploy/remote/reconcile-scheduler.sh"' not in execute
    assert '"writer_started": False' in execute
    assert '"scheduler_started": False' in execute


def test_writer_activation_requires_a_bound_recovery_receipt(bootstrap_text):
    activate = _fn(bootstrap_text, "mode_activate_writer")
    assert "opip_rollback_guard.py" in activate and "writer-gate" in activate
    assert '"$RECOVERY_RECEIPT"' in activate
    assert "already running" in activate
    assert 'running" == "1"' in activate


def test_scheduler_activation_requires_writer_and_live_acceptance(bootstrap_text):
    activate = _fn(bootstrap_text, "mode_activate_scheduler")
    assert '"$WRITER_RECEIPT"' in activate
    assert '"$LIVE_ACCEPTANCE"' in activate
    assert "https_ok" in activate and "telegram_ok" in activate
    assert "scheduler stays OFF" in activate


# --- orphaned-body regression (found during this correction) --------------
def test_no_orphaned_function_bodies(bootstrap_text):
    depth = 0
    offenders = []
    for number, line in enumerate(bootstrap_text.splitlines(), start=1):
        stripped = line.strip()
        if depth == 0 and (stripped.startswith("local ") or stripped in {"verify_identity", "verify_source"}):
            offenders.append(f"{number}: {stripped}")
        depth += line.count("{") - line.count("}")
        depth = max(depth, 0)
    assert not offenders, f"statement outside any function: {offenders}"
    assert depth == 0, "unbalanced braces"



# --- D08: distributable bytes must be LF-only ----------------------------
@pytest.mark.parametrize("script", [BOOTSTRAP, ROLLBACK], ids=["bootstrap", "rollback"])
def test_host_scripts_are_lf_only_on_disk(script):
    raw = Path(script).read_bytes()
    assert b"\r\n" not in raw, f"{script.name} contains CRLF; Linux bash -n would fail"
    assert raw.startswith(b"#!/usr/bin/env bash\n")


def test_gitattributes_pins_shell_line_endings():
    text = (R1 / ".gitattributes").read_text(encoding="utf-8")
    assert "*.sh text eol=lf" in text


# --- D10: no vacuous assertions -----------------------------------------
def test_no_vacuous_assertions_in_any_test():
    offenders = []
    for path in sorted((R1 / "tests").rglob("test_*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"assert\b.*\bor\s+(True|1\s*==\s*1)\s*$", line):
                offenders.append(f"{path.name}:{number}")
    assert not offenders, f"vacuous assertions: {offenders}"


# --- D05: containment and symlink ancestors -----------------------------
def test_safe_destination_rejects_escapes(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    for bad in ("../escape.txt", "/etc/passwd", "a/../../escape.txt", "C:/windows/x"):
        with pytest.raises(SystemExit):
            bundle._safe_destination(root, bad)


def test_safe_destination_rejects_symlinked_ancestor(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation not permitted in this environment")
    with pytest.raises(SystemExit):
        bundle._safe_destination(root, "linked/file.txt")


# --- D04: preflight every conflict before the first copy ----------------
def _staging(tmp_path: Path) -> Path:
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "a_first.txt").write_text("new-a\n", encoding="utf-8")
    (staging / "z_conflict.txt").write_text("new-z\n", encoding="utf-8")
    files = {p.relative_to(staging).as_posix(): bundle.sha256_file(p) for p in staging.rglob("*") if p.is_file()}
    (staging / "staging-receipt.json").write_text(json.dumps({
        "staged_at_utc": "2026-10-19T00:00:00Z",
        "bundle_manifest_sha256": "0" * 64,
        "files": files,
        "canonical_staged": False,
        "canonical_recovered": False,
    }), encoding="utf-8")
    return staging


def test_promote_refuses_without_writing_when_a_later_conflict_exists(tmp_path):
    staging = _staging(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (target / "z_conflict.txt").write_text("existing-z\n", encoding="utf-8")
    result = bundle.promote(staging, target, authorize=True)
    assert result["verdict"] == "REFUSED"
    assert result["writes_performed"] == 0
    assert result["promoted"] == 0
    assert not (target / "a_first.txt").exists(), "a conflicting preflight must copy nothing"
    assert (target / "z_conflict.txt").read_text(encoding="utf-8") == "existing-z\n"


def test_promote_force_still_preflights_everything(tmp_path):
    staging = _staging(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (target / "z_conflict.txt").write_text("existing-z\n", encoding="utf-8")
    result = bundle.promote(staging, target, authorize=True, force=True)
    assert result["verdict"] == "PASS", result.get("preflight_problems")
    assert result["promoted"] == 2
    assert (target / "a_first.txt").read_text(encoding="utf-8") == "new-a\n"
    assert (target / "z_conflict.txt").read_text(encoding="utf-8") == "new-z\n"


def test_promote_refuses_a_staged_file_changed_after_staging(tmp_path):
    staging = _staging(tmp_path)
    (staging / "a_first.txt").write_text("tampered\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    result = bundle.promote(staging, target, authorize=True)
    assert result["verdict"] == "REFUSED"
    assert result["writes_performed"] == 0
    assert any("changed after staging" in problem for problem in result["preflight_problems"])



# --- D06: receipt binding, expiry and replay ----------------------------
def _prepared(tmp_path: Path, ttl: int = 60) -> Path:
    path = tmp_path / "BOOTSTRAP_PREPARED.json"
    receipts.build_prepared(
        path=path, host="610307364", sha=layout.CERTIFIED_SHA, image_id="sha256:abc",
        config_digest="c" * 64, preflight_digest="p" * 64,
        preflight={"ready": True, "blocking_incidents": [], "fresh_host_only": False},
        ttl_minutes=ttl,
    )
    return path


def test_authorization_is_bound_to_the_prepared_receipt(tmp_path):
    prepared = _prepared(tmp_path)
    out = tmp_path / "BOOTSTRAP_AUTHORIZED.json"
    receipts.bind_authorization(prepared_path=prepared, path=out, confirm_sha=layout.CERTIFIED_SHA,
                                confirm_host="610307364", acknowledge_fresh_host=False)
    observed = {"host": "610307364", "certified_sha": layout.CERTIFIED_SHA,
                "image_id": "sha256:abc", "config_digest": "c" * 64}
    assert receipts.verify_for_execute(prepared_path=prepared, authorized_path=out,
                                       observed=observed)["verdict"] == "PASS"
    # A second prepare invalidates the earlier authorization.
    _prepared(tmp_path)
    gate = receipts.verify_for_execute(prepared_path=prepared, authorized_path=out, observed=observed)
    assert gate["verdict"] == "FAIL"
    assert any("not bound" in problem for problem in gate["problems"])


def test_blocking_incident_receipt_cannot_be_authorized(tmp_path):
    path = tmp_path / "prepared.json"
    receipts.build_prepared(
        path=path, host="610307364", sha=layout.CERTIFIED_SHA, image_id="sha256:abc",
        config_digest="c" * 64, preflight_digest="p" * 64,
        preflight={"ready": True, "blocking_incidents": ["INCUMBENT_V1_STILL_RUNNING"],
                   "fresh_host_only": False},
    )
    with pytest.raises(SystemExit, match="blocking protection incident"):
        receipts.bind_authorization(prepared_path=path, path=tmp_path / "auth.json",
                                    confirm_sha=layout.CERTIFIED_SHA, confirm_host="610307364",
                                    acknowledge_fresh_host=True)

def test_expired_prepared_receipt_cannot_be_authorized(tmp_path):
    prepared = _prepared(tmp_path, ttl=-1)
    with pytest.raises(SystemExit, match="expired"):
        receipts.bind_authorization(prepared_path=prepared, path=tmp_path / "auth.json",
                                    confirm_sha=layout.CERTIFIED_SHA, confirm_host="610307364",
                                    acknowledge_fresh_host=False)


def test_authorization_cannot_be_replayed(tmp_path):
    prepared = _prepared(tmp_path)
    out = tmp_path / "auth.json"
    receipts.bind_authorization(prepared_path=prepared, path=out, confirm_sha=layout.CERTIFIED_SHA,
                                confirm_host="610307364", acknowledge_fresh_host=False)
    observed = {"host": "610307364", "certified_sha": layout.CERTIFIED_SHA,
                "image_id": "sha256:abc", "config_digest": "c" * 64}
    receipts.consume(out)
    gate = receipts.verify_for_execute(prepared_path=prepared, authorized_path=out, observed=observed)
    assert gate["verdict"] == "FAIL"
    assert any("consumed" in problem for problem in gate["problems"])


def test_host_drift_is_refused_at_execute(tmp_path):
    prepared = _prepared(tmp_path)
    out = tmp_path / "auth.json"
    receipts.bind_authorization(prepared_path=prepared, path=out, confirm_sha=layout.CERTIFIED_SHA,
                                confirm_host="610307364", acknowledge_fresh_host=False)
    gate = receipts.verify_for_execute(
        prepared_path=prepared, authorized_path=out,
        observed={"host": "610307364", "certified_sha": layout.CERTIFIED_SHA,
                  "image_id": "sha256:drifted", "config_digest": "c" * 64})
    assert gate["verdict"] == "FAIL"
    assert any("image_id drifted" in problem for problem in gate["problems"])


# --- D03/D07: WAL-safe snapshot and full history comparison -------------
def _db(path: Path, *, epoch: int = 2, seq: int = 3, events: int = 2, wal: bool = False) -> Path:
    conn = sqlite3.connect(path)
    if wal:
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE meta (id INTEGER PRIMARY KEY, history_epoch INTEGER, next_local_sequence INTEGER)")
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO meta VALUES (1, ?, ?)", (epoch, seq))
    for _ in range(events):
        conn.execute("INSERT INTO events DEFAULT VALUES")
    conn.commit()
    conn.close()
    return path


def test_snapshot_is_wal_safe_and_verified(tmp_path):
    live = _db(tmp_path / "live.sqlite3", epoch=4, seq=9, events=7, wal=True)
    result = guard.snapshot_database(live, tmp_path / "snapshot.sqlite3")
    assert result["integrity_check"] == "ok"
    assert result["foreign_key_violations"] == 0
    assert result["event_count"] == 7 and result["history_epoch"] == 4
    assert result["sha256"] and len(result["sha256"]) == 64
    assert (tmp_path / "snapshot.sqlite3").is_file()


def test_snapshot_refuses_to_overwrite_an_existing_file(tmp_path):
    live = _db(tmp_path / "live.sqlite3")
    out = tmp_path / "snapshot.sqlite3"
    out.write_text("keep\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="exists"):
        guard.snapshot_database(live, out)
    assert out.read_text(encoding="utf-8") == "keep\n"


def test_compare_refuses_equal_epoch_with_newer_sequence_or_count():
    plan = {"live_at_prepare": {"history_epoch": 2, "next_local_sequence": 3, "event_count": 2}}
    live = {"history_epoch": 2, "next_local_sequence": 4, "event_count": 2,
            "integrity_check": "ok", "foreign_key_violations": 0}
    result = guard.compare_history(plan=plan, live=live)
    assert result["verdict"] == "REFUSED"
    assert any("next_local_sequence" in problem for problem in result["problems"])
    assert guard.compare_history(plan=plan, live=dict(live, next_local_sequence=3, event_count=3))["verdict"] == "REFUSED"
    assert guard.compare_history(plan=plan, live=dict(live, next_local_sequence=3))["verdict"] == "PASS"


# --- writer gate: receipt must be bound to THIS database ----------------
def _gate_receipt(tmp_path: Path, db: Path, **overrides) -> Path:
    payload = {
        "receipt_schema": 1, "operation": "canonical-native-restore", "completed": True,
        "verdict": "PASS", "restored_at_utc": datetime.now(timezone.utc).isoformat(),
        "host": "host", "destination_db": str(db.resolve()), "integrity_check": "ok",
        "foreign_key_violations": 0,
        "restored": {"history_epoch": 2, "next_local_sequence": 3, "event_count": 2},
    }
    payload.update(overrides)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_writer_gate_accepts_a_bound_pass_receipt(tmp_path):
    db = _db(tmp_path / "live.sqlite3")
    assert guard.writer_gate(receipt_path=_gate_receipt(tmp_path, db), db=db)["verdict"] == "PASS"


def test_writer_gate_refuses_missing_or_failed_receipt(tmp_path):
    db = _db(tmp_path / "live.sqlite3")
    missing = tmp_path / "nope.json"
    assert guard.writer_gate(receipt_path=missing, db=db)["verdict"] == "REFUSED"
    failed = _gate_receipt(tmp_path, db, completed=False, verdict="FAIL")
    assert guard.writer_gate(receipt_path=failed, db=db)["verdict"] == "REFUSED"


def test_writer_gate_refuses_a_receipt_for_another_destination(tmp_path):
    db = _db(tmp_path / "live.sqlite3")
    receipt = _gate_receipt(tmp_path, db, destination_db=str((tmp_path / "other.sqlite3").resolve()))
    gate = guard.writer_gate(receipt_path=receipt, db=db)
    assert gate["verdict"] == "REFUSED"
    assert any("destination" in problem for problem in gate["problems"])


def test_writer_gate_refuses_a_stale_receipt(tmp_path):
    db = _db(tmp_path / "live.sqlite3")
    old = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    receipt = _gate_receipt(tmp_path, db, restored_at_utc=old)
    gate = guard.writer_gate(receipt_path=receipt, db=db, max_age_hours=24)
    assert gate["verdict"] == "REFUSED"
    assert any("stale" in problem for problem in gate["problems"])


# --- set -e landmines and prepare/execute digest stability --------------
@pytest.mark.parametrize("script", [BOOTSTRAP, ROLLBACK], ids=["bootstrap", "rollback"])
def test_no_errexit_conditional_die(script):
    text = Path(script).read_text(encoding="utf-8")
    offenders = [f"{n}: {line.strip()}" for n, line in enumerate(text.splitlines(), start=1)
                 if re.search(r"\]\]\s*&&\s*(die|exit)\b", line)]
    assert not offenders, f"`]] && die` exits under set -e: {offenders}"


def test_prepare_and_execute_share_one_config_digest_basis(bootstrap_text):
    digest_fn = _fn(bootstrap_text, "config_digest_value")
    assert 'base="$SRC/docker-compose.yml"' in digest_fn
    assert 'base="$APP_ROOT/docker-compose.yml"' in digest_fn
    execute = _fn(bootstrap_text, "mode_execute")
    assert "config_digest_value" in execute
    prepare = _fn(bootstrap_text, "mode_prepare")
    assert "verify_source 0" in prepare, "prepare rebuilds the manifest instead of failing on a stale one"



def test_install_tolerates_a_precreated_data_dir(bootstrap_text):
    install = _fn(bootstrap_text, "install_release")
    assert "! -name data" in install, "a pre-created data/ must not look like foreign content"
    execute = _fn(bootstrap_text, "mode_execute")
    assert execute.index("install_release") < execute.index('"$CANONICAL_DIR"'), \
        "the release must be installed before runtime data directories are created"

def test_writer_gate_refuses_history_drift_after_restore(tmp_path):
    db = _db(tmp_path / "live.sqlite3", epoch=3, seq=1, events=5)
    gate = guard.writer_gate(receipt_path=_gate_receipt(tmp_path, db), db=db)
    assert gate["verdict"] == "REFUSED"
    assert any("history_epoch" in problem for problem in gate["problems"])


# --- D11 / D09: readiness scope and runbook consistency -----------------
def test_prebootstrap_has_no_backup_gate():
    readiness = (R1 / "scripts" / "Invoke-V2Readiness.ps1").read_text(encoding="utf-8")
    start = readiness.index("if ($Phase -eq 'PreBootstrap')")
    end = readiness.index("Add-R1Check $receipt 'PHASE_SCOPE' 'PASS' 'no /opt", start)
    pre = readiness[start:end]
    assert "BACKUP_SEED_INFO" in pre
    assert "'SKIP'" in pre
    assert "backups/*.manifest.json" in pre
    assert "-ge 1" not in pre, "a pre-bootstrap backup count must not gate the phase"
    assert "Invoke-PhaseProbe 'BACKUPS'" not in pre


def test_runbooks_use_the_guarded_entrypoints():
    migration = (R1 / "04-REVISED-MIGRATION-PROCEDURE.md").read_text(encoding="utf-8")
    assert "CanonicalRecovered" not in migration
    assert "RecoveryReceipt" in migration
    rollback_doc = (R1 / "05-REVISED-ROLLBACK-PROCEDURE.md").read_text(encoding="utf-8")
    assert "CutoverEpoch" not in rollback_doc
    assert "Invoke-V2Rollback.ps1" in rollback_doc



def test_compare_refuses_a_corrupt_live_database():
    plan = {"live_at_prepare": {"history_epoch": 2, "next_local_sequence": 3, "event_count": 2}}
    live = {"history_epoch": 2, "next_local_sequence": 3, "event_count": 2,
            "integrity_check": "*** in database main ***", "foreign_key_violations": 0}
    assert guard.compare_history(plan=plan, live=live)["verdict"] == "REFUSED"


@pytest.mark.skipif(os.name == "nt", reason="POSIX: executes an executable docker shim")
def test_image_binding_resolves_build_services_and_refuses_drift(tmp_path):
    """AC-007: build-only services resolve to project images and drift is refused."""
    import opip_image

    config = {
        "name": "proj",
        "services": {
            "ohm-trade-agent": {"build": "."},
            "opip-canonical-writer": {"build": "."},
            "ohm-dashboard-edge": {"image": "nginx:1.27-alpine"},
        },
    }
    images = opip_image.compose_images(config)
    assert images["ohm-trade-agent"] == "proj-ohm-trade-agent"
    assert images["opip-canonical-writer"] == "proj-opip-canonical-writer"
    assert images["ohm-dashboard-edge"] == "nginx:1.27-alpine"

    shim = tmp_path / "docker"
    shim.write_text("#!/bin/sh\nprintf 'sha256:certified\\n'\n", encoding="utf-8")
    shim.chmod(0o755)
    good = opip_image.verify_service(config, service="opip-canonical-writer",
                                     certified_image_id="sha256:certified", docker=str(shim))
    assert good["verdict"] == "PASS", good

    shim.write_text("#!/bin/sh\nprintf 'sha256:stale\\n'\n", encoding="utf-8")
    shim.chmod(0o755)
    stale = opip_image.verify_service(config, service="opip-canonical-writer",
                                      certified_image_id="sha256:certified", docker=str(shim))
    assert stale["verdict"] == "REFUSED"
    assert "not the certified" in " ".join(stale["problems"])

    missing = opip_image.verify_service(config, service="not-a-service",
                                        certified_image_id="sha256:certified", docker=str(shim))
    assert missing["verdict"] == "REFUSED"


def test_identity_evidence_requires_matching_fresh_host_document(tmp_path):
    """AC-008: a bare proof string is not evidence; host/gen/freshness bind."""
    import json as json_mod
    from datetime import datetime, timedelta, timezone

    import opip_bundle

    expected = {"droplet_id": "610307364", "ip": "161.35.106.207"}
    evidence = tmp_path / "identity.json"

    def write(**overrides):
        document = {
            "droplet_id": "610307364",
            "ip": "161.35.106.207",
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_generation": "gen-0001",
        }
        document.update(overrides)
        evidence.write_text(json_mod.dumps(document), encoding="utf-8")

    write()
    loaded = opip_bundle.load_identity_evidence(evidence, expected=expected)
    assert len(loaded["sha256"]) == 64
    assert loaded["source_generation"] == "gen-0001"

    with pytest.raises(SystemExit):
        opip_bundle.load_identity_evidence(None, expected=expected)

    write(droplet_id="999")
    with pytest.raises(SystemExit):
        opip_bundle.load_identity_evidence(evidence, expected=expected)

    write(captured_at_utc=(datetime.now(timezone.utc) - timedelta(hours=48)).isoformat())
    with pytest.raises(SystemExit):
        opip_bundle.load_identity_evidence(evidence, expected=expected, max_age_hours=24)

    write(source_generation="")
    with pytest.raises(SystemExit):
        opip_bundle.load_identity_evidence(evidence, expected=expected)

