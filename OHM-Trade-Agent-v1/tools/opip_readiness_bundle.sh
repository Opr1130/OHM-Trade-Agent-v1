#!/usr/bin/env bash
# O'Pip production-readiness evidence bundle: READ-ONLY, bounded, low impact.
#
# Paste-and-run once on the droplet:   sudo bash opip_readiness_bundle.sh | tee /tmp/opip-readiness.txt
#
# It never restarts, signals, writes, locks or deletes anything, copies no database,
# and runs no full-table scan: row evidence is the O(1) sequence counter plus ONE
# time-boxed, niced count of the single event type that drives writer start-up.
# Paste the output back verbatim; every section prints UNAVAILABLE rather than failing.
set -u
APP_ROOT="${OPIP_APP_ROOT:-/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1}"
DB="${OPIP_CANONICAL_DB:-$APP_ROOT/data/opip/canonical/opip_canonical_v1.sqlite3}"
W=opip-canonical-writer
section() { printf '\n===== %s =====\n' "$1"; }
try() { "$@" 2>&1 || echo "UNAVAILABLE: $*"; }

section "1 host: RAM, swap, CPU, disk"
try free -m
try swapon --show
try nproc
try df -h "$APP_ROOT" /var/lib/docker /var/log
try uptime

section "2 writer container: limits, health config, restarts, OOM"
try docker inspect --format 'mem={{.HostConfig.Memory}} swap={{.HostConfig.MemorySwap}} nano_cpus={{.HostConfig.NanoCpus}} pids={{.HostConfig.PidsLimit}} restart={{.HostConfig.RestartPolicy.Name}} restarts={{.RestartCount}} oom_killed={{.State.OOMKilled}} started={{.State.StartedAt}} status={{.State.Status}}' "$W"
try docker inspect --format 'healthcheck={{json .Config.Healthcheck}}' "$W"
try docker inspect --format 'health={{if .State.Health}}{{.State.Health.Status}} failing_streak={{.State.Health.FailingStreak}}{{else}}none{{end}}' "$W"

section "3 observed memory/CPU of every container (one sample, no streaming)"
try docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.CPUPerc}}'

section "4 writer start-up duration actually observed (start -> ready log line)"
try docker inspect --format 'last_started_at={{.State.StartedAt}}' "$W"
docker logs --timestamps --tail 400 "$W" 2>&1 | grep -E "ready db=|Traceback|Error|OOM|stopped" | tail -n 12 || echo "UNAVAILABLE: writer logs"

section "5 canonical store size and O(1) row evidence (read-only, no scan)"
ls -l "$DB" "$DB-wal" "$DB-shm" 2>&1 | sed 's/^/  /'
if command -v python3 >/dev/null 2>&1 && [ -r "$DB" ]; then
  nice -n 19 ionice -c3 timeout 30 python3 - "$DB" <<'PY' 2>&1 || echo "UNAVAILABLE: sqlite read (timeout or WAL not readable read-only)"
import sqlite3, sys, time
path = sys.argv[1]
conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
conn.execute("PRAGMA query_only=ON")
print("next_local_sequence (~= total events + 1):",
      conn.execute("SELECT next_local_sequence, schema_version FROM meta WHERE id=1").fetchone())
names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_events_%'")]
print("episode/role/di indexes present:", sorted(n for n in names if n in (
    "idx_events_episode_identity","idx_events_role_result_identity","idx_events_di_request_state")))
# The ONE count that drives start-up cost, served from the type index, time-boxed.
t0 = time.monotonic()
conn.set_progress_handler(lambda: 1 if time.monotonic() - t0 > 20 else 0, 100000)
try:
    n = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='opportunity_lifecycle.transition.recorded'").fetchone()[0]
    print("opportunity_lifecycle.transition.recorded rows:", n, "in %.1fs" % (time.monotonic() - t0))
except sqlite3.OperationalError:
    print("opportunity count aborted at the 20s budget (store is large)")
PY
else
  echo "UNAVAILABLE: python3 or database not readable"
fi

section "6 cycle bound and recent cycle outcomes"
sed -n '/run_cycle/p' /etc/cron.d/ohm-unified-cycle 2>/dev/null | grep -o "timeout [^ ]* [^ ]* [^ ]* [0-9]*s\? \|timeout -s TERM -k 5s [0-9]*" | head -n 4
grep -E 'OPIP_UNIFIED_CYCLE_(STATUS|COMPLETED_AT|TERMINATED_AT|TERMINATED_REASON)=' /var/log/ohm-unified-cycle.log 2>/dev/null | tail -n 60
echo "status counts (last 1000 lines):"
tail -n 1000 /var/log/ohm-unified-cycle.log 2>/dev/null | grep -o 'OPIP_UNIFIED_CYCLE_STATUS=[A-Z_]*' | sort | uniq -c

section "7 orphaned or stuck cycle processes (read-only ps)"
ps -eo pid,etimes,args 2>/dev/null | grep -E "[r]un_cycle|[o]hm-unified-cycle" | head -n 20
docker exec "$(docker ps -q --filter name=ohm-trade-agent | head -n 1)" sh -c 'ps -eo pid,etimes,args | grep -c "[r]un_cycle"' 2>&1 | sed 's/^/in-container run_cycle count: /'
[ -x "$APP_ROOT/deploy/remote/diagnose-opip-learning.sh" ] && bash "$APP_ROOT/deploy/remote/diagnose-opip-learning.sh" 2>&1 | grep -E 'unified_cycle_(host_lock|lock_state|holder_elapsed_seconds|holder_overdue|in_flight|orphan_suspected|log_latest_status|log_age_seconds)'

section "8 deploy health deadline in the deployed script"
grep -n -A8 '^wait_writer_health' "$APP_ROOT/deploy/remote/ohm-deploy" 2>/dev/null | grep -E 'seq 1|sleep' 

section "9 safety flags in the live environment (read-only)"
docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$(docker ps -q --filter name=ohm-trade-agent | head -n 1)" 2>/dev/null \
  | grep -E '^(OPIP_EARLY_WATCH_MODE|OPIP_PHASE_RESOURCE_TELEMETRY|OPIP_UNIFIED_CYCLE_BUDGET_SECONDS|KRAKEN_.*(TRADING|LIVE|ORDER).*|OPIP_.*(LEARNING|COMMITTEE|AUTO).*)=' | sed 's/\(KEY\|SECRET\|TOKEN\)=.*/\1=REDACTED/'
echo "(absence of a line = variable unset = default)"
echo; echo "BUNDLE_COMPLETE (no state was changed)"
