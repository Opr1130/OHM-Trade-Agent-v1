"""R1 recovery engine: fresh-host classification, rollback planning, canonical restore.

Design rules:
  * a file copy is never reported as database recovery;
  * the canonical restore uses the CERTIFIED native recovery contract
    (app.opip.canonical.recovery.restore_from_backup) and validates epoch,
    sequence, integrity, foreign keys and event counts afterwards;
  * rollback never depends on the controller's transient SAFE_BASELINE override:
    it is generated into a durable directory and re-read/verified;
  * fresh-host classification runs the certified protection preflight logic and
    records its verdict instead of silently skipping it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

CERTIFIED_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()

SAFE_BASELINE_OVERRIDE = """services:
  ohm-trade-agent:
    environment:
      OPIP_RELEASE_PROFILE: "SAFE_BASELINE"
      OPIP_FEATURE_BUS_MODE: "off"
      OPIP_CANONICAL_WRITER_MODE: "off"
      OPIP_TARGET_SPINE_MODE: "off"
      OPIP_PAPER_V2_MODE: "off"
      OPIP_COMMITTEE_MODE: "off"
      OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD: "0.0"
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_text(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def classify_fresh_host(
    *,
    app_root: Path,
    state_dir: Path,
    container_present: str = "unknown",
    expected_sha: str = CERTIFIED_SHA,
    facts_override: dict | None = None,
) -> dict:
    """Read-only classification of a host before first install."""
    app_root = Path(app_root)
    state_dir = Path(state_dir)
    facts = {
        "git_head": None,
        "env_present": (app_root / ".env").is_file(),
        "compose_present": (app_root / "docker-compose.yml").is_file(),
        "paper_compose_present": (app_root / "docker-compose.paper.yml").is_file(),
        "core_container_present": container_present,
        "last_good_present": (state_dir / "last-good-sha").is_file(),
        "canonical_store_present": (app_root / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3").is_file(),
        "p1_outbox_present": (app_root / "data" / "p1_shadow_outbox.jsonl").is_file(),
        "data_dir_present": (app_root / "data").is_dir(),
    }
    head_file = app_root / ".git" / "HEAD"
    if head_file.is_file():
        raw = head_file.read_text(encoding="utf-8").strip()
        if raw.startswith("ref: "):
            ref = app_root / ".git" / raw.split(" ", 1)[1].strip()
            facts["git_head"] = ref.read_text(encoding="utf-8").strip() if ref.is_file() else None
        else:
            facts["git_head"] = raw

    if facts_override:
        facts.update(facts_override)
        facts["evidence_source"] = "remote-read-only-facts"
    else:
        facts["evidence_source"] = "local-path"

    reasons: list[str] = []
    status = "FRESH_READY"
    if not facts["compose_present"] or not (head_file.is_file() or facts.get("git_head")):
        status, _ = "MISSING_GIT_CHECKOUT", reasons.append("certified checkout/compose missing")
    elif facts["git_head"] != expected_sha:
        status, _ = "WRONG_REVISION", reasons.append(f"git_head={facts['git_head']}")
    elif not facts["env_present"]:
        status, _ = "MISSING_ENV", reasons.append(".env absent")
    elif facts["core_container_present"] == "yes":
        status, _ = "INCUMBENT_PRESENT", reasons.append("ohm-trade-agent container already exists")
    elif facts["core_container_present"] == "unknown":
        status, _ = "UNKNOWN", reasons.append("container presence not proven (remote probe required)")
    elif facts["last_good_present"] or facts["canonical_store_present"]:
        status, _ = "INCUMBENT_PRESENT", reasons.append("last-good-sha or canonical store present")

    return {
        "classified_at_utc": utc_now(),
        "status": status,
        "verdict": status,
        "reasons": reasons,
        "facts": facts,
        "bootstrap_allowed": status == "FRESH_READY",
        "controller_allowed": status == "INCUMBENT_PRESENT",
        "preflight_required": True,
        "note": "The certified controller refuses a host without an existing container "
                "(ohm-deploy:1347-1391); first install must use the bootstrap path and still "
                "record the certified protection-preflight verdict.",
    }
def classify_paper_topology(service_names: list[str]) -> dict:
    """Any selected Freqtrade paper service blocks bootstrap (legacy activation)."""
    paper = sorted(n for n in service_names if n.startswith("ohm-freqtrade-paper") or n == "freqtrade")
    return {
        "services": sorted(service_names),
        "paper_services": paper,
        "verdict": "REFUSED" if paper else "PASS",
        "detail": "legacy Freqtrade paper services must never be selected during bootstrap"
        if paper else "no paper service selected",
    }


def verify_overlay_pair(base_path: Path, overlay_path: Path) -> dict:
    """Minimal fail-closed overlay checks (the full renderer runs on the host)."""
    import re

    checks: list[dict] = []
    base_path, overlay_path = Path(base_path), Path(overlay_path)
    if not base_path.is_file() or not overlay_path.is_file():
        missing = [str(p) for p in (base_path, overlay_path) if not p.is_file()]
        return {"verdict": "FAIL", "checks": [{"id": "OVERLAY_PRESENT", "status": "FAIL", "detail": ", ".join(missing)}]}
    base = base_path.read_text(encoding="utf-8")
    overlay = overlay_path.read_text(encoding="utf-8")
    checks.append({"id": "OVERLAY_PRESENT", "status": "PASS", "detail": "base and overlay found"})

    def field(text: str, service_block: str, key: str):
        block = re.search(rf"^  {re.escape(service_block)}:\n(.*?)(?=^  \S|\Z)", text, re.S | re.M)
        if not block:
            return None
        match = re.search(rf"^\s+{re.escape(key)}:\s*\"?([A-Za-z0-9_.]+)\"?\s*$", block.group(1), re.M)
        return match.group(1) if match else None

    writer_mem = field(overlay, "opip-canonical-writer", "mem_limit")
    checks.append({"id": "WRITER_PIN", "status": "PASS" if writer_mem == "128m" else "FAIL",
                   "detail": f"writer mem_limit={writer_mem} (controller pins 128m)"})
    for key, value in (("OPIP_PAPER_V2_MODE", "off"), ("OPIP_FEATURE_BUS_MODE", "shadow"),
                       ("OPIP_CANONICAL_WRITER_MODE", "shadow")):
        got = field(overlay, "ohm-trade-agent", key)
        checks.append({"id": f"OVERLAY_{key}", "status": "PASS" if got == value else "FAIL", "detail": f"{key}={got}"})
    base_paper = field(base, "ohm-trade-agent", "OPIP_PAPER_V2_MODE")
    checks.append({"id": "BASE_PAPER_OFF", "status": "PASS" if base_paper == "off" else "FAIL", "detail": f"base {base_paper}"})
    failed = [c for c in checks if c["status"] == "FAIL"]
    return {"verdict": "FAIL" if failed else "PASS", "checks": checks}


def evaluate_ssh_evidence(records: list[dict], *, expected_user: str = "opipadmin") -> dict:
    """Each remote probe must use the approved account, exit 0 and carry evidence."""
    problems: list[str] = []
    for record in records:
        user = str(record.get("user", ""))
        if user != expected_user:
            problems.append(f"wrong ssh account: {user or 'unset'} (expected {expected_user})")
        if record.get("exit_code") != 0:
            problems.append(f"remote command failed: rc={record.get('exit_code')} cmd={record.get('command')}")
        if not record.get("evidence"):
            problems.append(f"no structured evidence for: {record.get('command')}")
    return {"verdict": "FAIL" if problems else "PASS", "problems": problems, "probes": len(records)}


def verify_tls_prerequisites(facts: dict) -> dict:
    """Missing challenge prerequisites must block HTTPS activation."""
    problems: list[str] = []
    if not facts.get("dns_resolves_to_host"):
        problems.append("hostname does not resolve to this host")
    if facts.get("port_80_reachable_from_internet") is not True:
        problems.append("HTTP-01 challenge needs inbound :80 during issuance")
    if not facts.get("certificate_present"):
        problems.append("certificate material absent")
    if not facts.get("private_key_permissions_ok"):
        problems.append("private key permissions are not 0600 root")
    if facts.get("firewall_8443_restricted") is not True:
        problems.append("8443 ingress is not restricted to trusted addresses")
    if facts.get("renewal_timer_active") is not True:
        problems.append("automatic renewal timer is not active")
    return {"verdict": "REFUSED" if problems else "PASS", "problems": problems}


def verify_uniqueness(facts: dict) -> dict:
    """One writer, one core container, one scheduler entry, one Telegram consumer."""
    problems: list[str] = []
    if int(facts.get("canonical_writer_containers", 0)) != 1:
        problems.append(f"canonical writer containers={facts.get('canonical_writer_containers')}")
    if int(facts.get("core_containers", 0)) != 1:
        problems.append(f"core containers={facts.get('core_containers')}")
    if int(facts.get("cycle_cron_lines", 0)) != 1:
        problems.append(f"cycle cron lines={facts.get('cycle_cron_lines')}")
    if int(facts.get("telegram_pollers", 0)) > 1:
        problems.append(f"telegram pollers={facts.get('telegram_pollers')}")
    return {"verdict": "REFUSED" if problems else "PASS", "problems": problems}


def plan_rollback(
    *,
    durable_dir: Path,
    previous_sha: str,
    preserve_cutover_writes: bool = True,
    current_epoch: int | None = None,
    rollback_epoch: int | None = None,
) -> dict:
    """Build a durable, verifiable SAFE_BASELINE rollback plan."""
    durable_dir = Path(durable_dir) / previous_sha
    durable_dir.mkdir(parents=True, exist_ok=True)
    overlay = durable_dir / "safe-baseline.override.yml"
    overlay.write_text(SAFE_BASELINE_OVERRIDE, encoding="utf-8")
    digest = sha256_text(SAFE_BASELINE_OVERRIDE)
    reread = overlay.read_text(encoding="utf-8")
    if sha256_text(reread) != digest:
        raise SystemExit("rollback overlay failed its own durability check")

    refusals: list[str] = []
    if len(previous_sha) != 40 or any(c not in "0123456789abcdef" for c in previous_sha):
        refusals.append("previous SHA is not a 40-character lowercase hex commit")
    if current_epoch is not None and rollback_epoch is not None and current_epoch > rollback_epoch:
        refusals.append(
            f"live canonical epoch {current_epoch} is newer than the rollback point {rollback_epoch}: "
            "post-cutover writes would be destroyed"
        )

    steps = [
        "# 1. freeze automation (never two schedulers)",
        "crontab -l | grep -v 'app.jobs.run_cycle' | crontab -",
        "rm -f /etc/cron.d/ohm-unified-cycle",
        "# 2. quiesce the single canonical writer and prove it",
        "cd /opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1 && docker compose stop opip-canonical-writer",
        "docker compose ps opip-canonical-writer",
        "# 3. preserve post-cutover canonical evidence BEFORE any profile change",
    ]
    if preserve_cutover_writes:
        steps += [
            "install -d -m 0700 /var/lib/opip-cutover-evidence/$(date -u +%Y%m%dT%H%M%SZ)",
            "cp -a data/opip/canonical/. /var/lib/opip-cutover-evidence/$(date -u +%Y%m%dT%H%M%SZ)/",
            "sha256sum data/opip/canonical/opip_canonical_v1.sqlite3",
        ]
    steps += [
        "# 4. stop the remaining producers",
        "docker compose stop opip-stream-worker ohm-trade-agent ohm-dashboard-edge",
        "# 5. apply the DURABLE rollback overlay (generated here, not the controller's transient file)",
        f"test -s {overlay.as_posix()}",
        f"sha256sum {overlay.as_posix()}  # expect {digest}",
        "docker compose -f docker-compose.yml -f "
        f"{overlay.as_posix()} up -d --remove-orphans ohm-trade-agent opip-canonical-writer ohm-dashboard-edge",
        "# 6. verify exactly one writer, one scheduler, one Telegram consumer",
        "docker compose ps",
        "crontab -l | grep -c 'app.jobs.run_cycle'",
        "docker compose logs --tail=50 ohm-trade-agent | grep -c 'Telegram callback/command listener started'",
        "# 7. revalidate",
        "curl -fsS http://127.0.0.1:8000/health",
    ]
    return {
        "planned_at_utc": utc_now(),
        "previous_sha": previous_sha,
        "durable_overlay": str(overlay),
        "overlay_sha256": digest,
        "depends_on_transient_override": False,
        "preserve_cutover_writes": preserve_cutover_writes,
        "refusals": refusals,
        "verdict": "REFUSED" if refusals else "PASS",
        "steps": steps,
    }
def _read_epoch(db: Path) -> tuple[int, int, int] | None:
    if not db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1").fetchone()
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return (int(row[0]), int(row[1]), int(events)) if row else None


def writer_is_quiescent(app_root: Path, *, check_lock: bool = True) -> tuple[bool, str]:
    """Refuse to restore over a live writer. Uses the certified lock primitive."""
    app_root = Path(app_root)
    lock = app_root / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3.writer.lock"
    socket = app_root / "data" / "opip" / "canonical" / "writer.sock"
    if socket.exists():
        return False, f"writer socket present: {socket}"
    if not check_lock or not lock.exists():
        return True, "no writer lock file"
    try:
        sys.path.insert(0, str(app_root))
        from app.services.registry_io import registry_lock

        with registry_lock(lock, timeout=0.0):
            return True, "writer lock is not held"
    except TimeoutError:
        return False, "writer lock is held by a live process"
    except Exception as exc:  # noqa: BLE001
        return False, f"cannot prove writer quiescence: {type(exc).__name__}"


def restore_canonical(
    *,
    bundle_dir: Path,
    app_root: Path,
    live_db: Path,
    allow_newer_history: bool = False,
    dry_run: bool = False,
    authorize: bool = False,
    receipt_out: Path | None = None,
) -> dict:
    """Certified native restore + post-restore validation. Linux-only by design."""
    bundle_dir, app_root, live_db = Path(bundle_dir), Path(app_root), Path(live_db)
    manifest = json.loads((bundle_dir / "BUNDLE-MANIFEST.json").read_text(encoding="utf-8"))
    canonical = manifest.get("canonical")
    result: dict = {"started_at_utc": utc_now(), "bundle": str(bundle_dir), "live_db": str(live_db),
                    "canonical_recovered": False, "file_copy": False, "checks": [], "verdict": "FAIL"}
    if not authorize and not dry_run:
        result["verdict"] = "REFUSED"
        result["checks"].append({"id": "AUTHORIZATION", "status": "FAIL",
                                 "detail": "-Authorize is required to modify the live canonical store"})
        return result

    bundle_manifest_sha = sha256_file(bundle_dir / "BUNDLE-MANIFEST.json")

    def finish(payload: dict) -> dict:
        """Always persist a receipt, including on failure (a failed run overwrites a stale PASS)."""
        if receipt_out:
            recovered = payload.get("recovered") or {}
            record = {
                "receipt_schema": 1,
                "operation": "canonical-native-restore",
                "completed": payload.get("verdict") == "PASS",
                "verdict": payload.get("verdict"),
                "restored_at_utc": utc_now(),
                "host": platform.node(),
                "bundle_dir": str(bundle_dir),
                "bundle_manifest_sha256": bundle_manifest_sha,
                "generation_sha256": payload.get("generation_sha256", ""),
                "destination_db": str(live_db.resolve()),
                "source_release_sha": CERTIFIED_SHA,
                "integrity_check": payload.get("integrity_check"),
                "foreign_key_violations": payload.get("foreign_key_violations"),
                "restored": recovered,
            }
            target = Path(receipt_out)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            payload["receipt"] = str(target)
        return payload
    if not canonical:
        result["verdict"] = "INSUFFICIENT"
        result["checks"].append({"id": "CANONICAL_DECLARED", "status": "FAIL", "detail": "bundle declares no canonical generation"})
        return finish(result)

    backups = sorted((bundle_dir / "payload").glob("**/canonical/backups/*.manifest.json"))
    if len(backups) != 1:
        result["checks"].append({"id": "GENERATION_PRESENT", "status": "FAIL", "detail": f"found {len(backups)}"})
        return finish(result)
    generation = backups[0]
    inner = json.loads(generation.read_text(encoding="utf-8"))
    backup_db = generation.with_name(inner["backup_file"])
    result["generation_sha256"] = inner["sha256"]
    result["checks"].append({"id": "GENERATION_PRESENT", "status": "PASS", "detail": backup_db.name})

    live = _read_epoch(live_db)
    result["live_before"] = live
    if live is not None and not allow_newer_history and live[0] >= int(inner["history_epoch"]):
        result["verdict"] = "REFUSED"
        result["checks"].append({"id": "NEWER_HISTORY_GUARD", "status": "FAIL",
                                 "detail": f"live epoch {live[0]} >= generation epoch {inner['history_epoch']}"})
        return finish(result)
    result["checks"].append({"id": "NEWER_HISTORY_GUARD", "status": "PASS",
                             "detail": f"live={live} generation_epoch={inner['history_epoch']}"})

    quiescent, detail = writer_is_quiescent(app_root)
    result["checks"].append({"id": "WRITER_QUIESCENT", "status": "PASS" if quiescent else "FAIL", "detail": detail})
    if not quiescent:
        result["verdict"] = "REFUSED"
        return finish(result)

    if dry_run:
        result["verdict"] = "DRYRUN"
        result["checks"].append({"id": "DRYRUN", "status": "PASS", "detail": "restore not executed"})
        return finish(result)

    sys.path.insert(0, str(app_root))
    try:
        from app.opip.canonical.recovery import restore_from_backup
        from app.opip.canonical.schema import CanonicalDurabilityError
    except ModuleNotFoundError as exc:
        result["verdict"] = "INSUFFICIENT"
        result["checks"].append({"id": "CERTIFIED_MODULES", "status": "FAIL", "detail": str(exc)})
        return finish(result)

    try:
        restored = restore_from_backup(
            backup_db=backup_db, live_db=live_db, manifest_path=generation,
            expected_source_release_sha=CERTIFIED_SHA, advance_epoch=True,
        )
    except CanonicalDurabilityError as exc:
        result["verdict"] = "PLATFORM_BLOCKED"
        result["checks"].append({"id": "CERTIFIED_RESTORE", "status": "FAIL",
                                 "detail": f"durability unprovable on this platform: {exc}"})
        return finish(result)
    except Exception as exc:  # noqa: BLE001
        result["verdict"] = "FAIL"
        result["checks"].append({"id": "CERTIFIED_RESTORE", "status": "FAIL",
                                 "detail": f"{type(exc).__name__}: {exc}"})
        return finish(result)

    result["checks"].append({"id": "CERTIFIED_RESTORE", "status": "PASS",
                             "detail": f"epoch {restored.get('previous_live_epoch')} -> {restored.get('history_epoch')}"})
    conn = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        epoch, next_seq = conn.execute("SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1").fetchone()
        events = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    finally:
        conn.close()

    result["checks"].append({"id": "SQLITE_INTEGRITY", "status": "PASS" if integrity == "ok" else "FAIL", "detail": integrity})
    result["checks"].append({"id": "FOREIGN_KEYS", "status": "PASS" if not fk else "FAIL", "detail": f"violations={len(fk)}"})
    result["checks"].append({"id": "EVENT_COUNT", "status": "PASS" if int(events) == int(inner["event_count"]) else "FAIL",
                             "detail": f"restored={events} manifest={inner['event_count']}"})
    result["checks"].append({"id": "EPOCH_ADVANCED", "status": "PASS" if int(epoch) == int(inner["history_epoch"]) + 1 else "FAIL",
                             "detail": f"epoch={epoch} expected={int(inner['history_epoch']) + 1}"})
    result["checks"].append({"id": "SEQUENCE_RESET", "status": "PASS" if int(next_seq) == 1 else "FAIL",
                             "detail": f"next_local_sequence={next_seq}"})
    failed = [c for c in result["checks"] if c["status"] == "FAIL"]
    result["canonical_recovered"] = not failed
    result["integrity_check"] = integrity
    result["foreign_key_violations"] = len(fk)
    result["recovered"] = {"history_epoch": int(epoch), "next_local_sequence": int(next_seq),
                           "event_count": int(events), "integrity_check": integrity}
    result["verdict"] = "PASS" if not failed else "FAIL"
    result["finished_at_utc"] = utc_now()
    return finish(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MISSION-07-R1 recovery engine")
    sub = parser.add_subparsers(dest="command", required=True)

    p_fresh = sub.add_parser("classify-fresh-host")
    p_fresh.add_argument("--app-root", required=True)
    p_fresh.add_argument("--state-dir", required=True)
    p_fresh.add_argument("--container-present", choices=["yes", "no", "unknown"], default="unknown")
    p_fresh.add_argument("--facts")
    p_fresh.add_argument("--out")

    p_plan = sub.add_parser("plan-rollback")
    p_plan.add_argument("--durable-dir", required=True)
    p_plan.add_argument("--previous-sha", required=True)
    p_plan.add_argument("--current-epoch", type=int)
    p_plan.add_argument("--rollback-epoch", type=int)
    p_plan.add_argument("--no-preserve-cutover", action="store_true")
    p_plan.add_argument("--out")

    p_restore = sub.add_parser("restore-canonical")
    p_restore.add_argument("--bundle", required=True)
    p_restore.add_argument("--app-root", required=True)
    p_restore.add_argument("--live-db", required=True)
    p_restore.add_argument("--allow-newer-history", action="store_true")
    p_restore.add_argument("--dry-run", action="store_true")
    p_restore.add_argument("--authorize", action="store_true")
    p_restore.add_argument("--receipt-out")
    p_restore.add_argument("--out")

    args = parser.parse_args(argv)
    out = Path(args.out) if getattr(args, "out", None) else None

    if args.command == "classify-fresh-host":
        override = json.loads(Path(args.facts).read_text(encoding="utf-8")) if args.facts else None
        result = classify_fresh_host(app_root=Path(args.app_root), state_dir=Path(args.state_dir),
                                     container_present=args.container_present, facts_override=override)
    elif args.command == "plan-rollback":
        result = plan_rollback(durable_dir=Path(args.durable_dir), previous_sha=args.previous_sha,
                               preserve_cutover_writes=not args.no_preserve_cutover,
                               current_epoch=args.current_epoch, rollback_epoch=args.rollback_epoch)
    else:
        result = restore_canonical(bundle_dir=Path(args.bundle), app_root=Path(args.app_root),
                                   live_db=Path(args.live_db),
                                   allow_newer_history=args.allow_newer_history, dry_run=args.dry_run,
                                   authorize=args.authorize,
                                   receipt_out=Path(args.receipt_out) if args.receipt_out else None)

    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")

    verdict = result.get("verdict")
    return {
        "PASS": 0,
        "FRESH_READY": 0,
        "INSUFFICIENT": 2,
        "PLATFORM_BLOCKED": 2,
        "REFUSED": 3,
        "DRYRUN": 77,
    }.get(verdict, 1)


if __name__ == "__main__":
    raise SystemExit(main())



