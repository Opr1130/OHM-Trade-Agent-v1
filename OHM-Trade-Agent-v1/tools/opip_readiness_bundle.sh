#!/usr/bin/env bash
# O'Pip production-readiness evidence bundle: READ-ONLY, bounded, low impact.
#
# Paste-and-run once on the droplet:   sudo bash opip_readiness_bundle.sh | tee /tmp/opip-readiness.txt
#
# It never restarts, signals, writes, locks or deletes anything, copies no database,
# and runs no full-table scan: row evidence is the O(1) sequence counter plus ONE
# time-boxed, niced count of the single event type that drives writer start-up.
# Paste the output back verbatim; every section prints UNAVAILABLE rather than failing.
#
# It invokes NO other repository script: a callee can mutate state indirectly (the
# learning diagnostic reaches a loader that quarantines a malformed registry with
# os.replace), and filtering a callee's stdout cannot undo a side effect. Every
# observation below is a command shown here, and a test pins that rule.
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

section "5 canonical store size and bounded, read-only row evidence"
ls -l "$DB" "$DB-wal" "$DB-shm" 2>&1 | sed 's/^/  /'
if command -v python3 >/dev/null 2>&1 && [ -r "$DB" ]; then
  nice -n 19 ionice -c3 timeout 30 python3 - "$DB" <<'PY' 2>&1 || echo "UNAVAILABLE: sqlite read (timeout or WAL not readable read-only)"
import sqlite3, sys, time
path = sys.argv[1]
conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
conn.execute("PRAGMA query_only=ON")
t0 = time.monotonic()
conn.set_progress_handler(lambda: 1 if time.monotonic() - t0 > 20 else 0, 100000)
epoch, seq, schema = conn.execute("SELECT history_epoch, next_local_sequence, schema_version FROM meta WHERE id=1").fetchone()
# A supported restore advances history_epoch and resets next_local_sequence to 1
# WITHOUT deleting earlier events, so this pair is a position in the CURRENT epoch,
# never a total.
print("current history_epoch=%s next_local_sequence=%s (current-epoch position, NOT a total) schema_version=%s" % (epoch, seq, schema))
names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_events_%'")]
print("episode/role/di indexes present:", sorted(n for n in names if n in (
    "idx_events_episode_identity", "idx_events_role_result_identity", "idx_events_di_request_state")))
try:
    # Upper bound over ALL epochs from O(epochs * log n) index seeks on UNIQUE(history_epoch, local_sequence).
    # local_sequence is assigned from a counter that starts at 1 in every epoch, so the
    # per-epoch maximum is >= that epoch's row count and the sum is >= the true total.
    bound, epochs, e = 0, 0, conn.execute("SELECT MIN(history_epoch) FROM events").fetchone()[0]
    while e is not None and epochs < 1000:
        top = conn.execute("SELECT MAX(local_sequence) FROM events WHERE history_epoch = ?", (e,)).fetchone()[0]
        print("  epoch %s: max local_sequence=%s" % (e, top))
        bound += int(top or 0)
        epochs += 1
        e = conn.execute("SELECT MIN(history_epoch) FROM events WHERE history_epoch > ?", (e,)).fetchone()[0]
    if e is None:
        print("EVENT_TOTAL_UPPER_BOUND=%d across %d epoch(s) (>= true total)" % (bound, epochs))
    else:
        print("EVENT_TOTAL_UPPER_BOUND=UNVERIFIED (more than 1000 epochs)")
except sqlite3.OperationalError:
    print("EVENT_TOTAL_UPPER_BOUND=UNVERIFIED (aborted at the 20s budget)")
# The one count that drives start-up cost, served from the type index, time-boxed.
t1 = time.monotonic()
conn.set_progress_handler(lambda: 1 if time.monotonic() - t1 > 20 else 0, 100000)
try:
    n = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='opportunity_lifecycle.transition.recorded'").fetchone()[0]
    print("opportunity_lifecycle.transition.recorded rows:", n, "in %.1fs" % (time.monotonic() - t1))
except sqlite3.OperationalError:
    print("opportunity count UNVERIFIED (aborted at the 20s budget; store is large)")
PY
else
  echo "UNAVAILABLE: python3 or database not readable (EVENT_TOTAL_UPPER_BOUND=UNVERIFIED)"
fi

section "6 cycle bound and recent cycle outcomes"
sed -n '/run_cycle/p' /etc/cron.d/ohm-unified-cycle 2>/dev/null | grep -o "timeout [^ ]* [^ ]* [^ ]* [0-9]*s\? \|timeout -s TERM -k 5s [0-9]*" | head -n 4
grep -E 'OPIP_UNIFIED_CYCLE_(STATUS|COMPLETED_AT|TERMINATED_AT|TERMINATED_REASON)=' /var/log/ohm-unified-cycle.log 2>/dev/null | tail -n 60
echo "status counts (last 1000 lines):"
tail -n 1000 /var/log/ohm-unified-cycle.log 2>/dev/null | grep -o 'OPIP_UNIFIED_CYCLE_STATUS=[A-Z_]*' | sort | uniq -c

section "7 orphaned or stuck cycle processes (read-only)"
ps -eo pid,etimes,args 2>/dev/null | grep -E "[r]un_cycle|[o]hm-unified-cycle" | head -n 20
# The container image has no ps, so count from /proc. The pattern is split with quotes so
# this shell's own command line never matches itself. A failed exec prints UNAVAILABLE,
# never a count: "0" must mean "looked and found none".
CORE="$(docker ps -q --filter name=ohm-trade-agent 2>/dev/null | head -n 1)"
if [ -n "$CORE" ]; then
  COUNT="$(docker exec "$CORE" sh -c 'n=0; for d in /proc/[0-9]*; do c=$(tr "\0" " " < "$d/cmdline" 2>/dev/null); case "$c" in *run_"cycle"*) n=$((n+1));; esac; done; echo "$n"' 2>/dev/null)"
  case "$COUNT" in
    ''|*[!0-9]*) echo "in-container run_cycle count: UNAVAILABLE (exec failed)" ;;
    *) echo "in-container run_cycle count: $COUNT (from /proc)" ;;
  esac
else
  echo "in-container run_cycle count: UNAVAILABLE (core container not found)"
fi
LOCK="${OPIP_CYCLE_LOCK:-/var/run/ohm-unified-cycle.lock}"
if [ -e "$LOCK" ]; then
  # Read-only: /proc/locks lists kernel locks by inode; the lock file is never opened,
  # created, locked or probed with flock.
  INO="$(stat -c %i "$LOCK" 2>/dev/null)"
  HOLDER="$(awk -v i=":$INO" '$6 ~ i"$" {print $5; exit}' /proc/locks 2>/dev/null)"
  if [ -n "$HOLDER" ]; then
    echo "unified_cycle_host_lock=HELD pid=$HOLDER elapsed_seconds=$(ps -o etimes= -p "$HOLDER" 2>/dev/null | tr -d ' ') comm=$(ps -o comm= -p "$HOLDER" 2>/dev/null)"
  else
    echo "unified_cycle_host_lock=IDLE_OR_UNOBSERVABLE (no kernel lock held on the lock file)"
  fi
else
  echo "unified_cycle_host_lock=ABSENT"
fi

section "8 deploy health deadline: the INSTALLED controller (what a deploy runs) and the release copy"
# /usr/local/sbin/ohm-deploy is what actually executes; the release-tree copy is only what
# the NEXT bootstrap would install. A deadline is NEW when WRITER_HEALTH_* is defined and
# LEGACY when only a fixed `seq 1 N` poll loop is found.
for f in "${OPIP_INSTALLED_CONTROLLER:-/usr/local/sbin/ohm-deploy}" "$APP_ROOT/deploy/remote/ohm-deploy"; do
  if [ -r "$f" ]; then
    T="$(grep -E '^WRITER_HEALTH_TIMEOUT_SECONDS=' "$f" | head -n 1)"
    P="$(grep -E '^WRITER_HEALTH_POLL_SECONDS=' "$f" | head -n 1)"
    if [ -n "$T" ]; then
      echo "$f: ${T} ${P} verdict=NEW"
    elif grep -qE '^wait_writer_health' "$f"; then
      echo "$f: $(grep -A10 '^wait_writer_health' "$f" | grep -E 'seq 1' | head -n 1 | sed 's/^ *//') verdict=LEGACY (fixed poll loop, no WRITER_HEALTH_*)"
    else
      echo "$f: wait_writer_health not found verdict=UNVERIFIED"
    fi
  else
    echo "$f: UNAVAILABLE (not readable)"
  fi
done

section "9 runtime configuration of the core container (set vs unset; unset is NOT disabled)"
# Each name is listed explicitly. NAME=<UNSET> means the variable is absent from the
# container environment, so the code default applies; it is not evidence that a feature
# is off. A value of off/false/0 is the only evidence of a disabled feature here.
CORE="$(docker ps -q --filter name=ohm-trade-agent 2>/dev/null | head -n 1)"
if [ -n "$CORE" ]; then
  ENVDUMP="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$CORE" 2>/dev/null)"
  for NAME in OPIP_RELEASE_PROFILE OPIP_COMMITTEE_MODE OPIP_PAPER_V2_MODE OPIP_EARLY_WATCH_ALERTS_ENABLED \
              OPIP_EARLY_WATCH_MODE OPIP_CANONICAL_WRITER_MODE OPIP_FEATURE_BUS_MODE OPIP_TARGET_SPINE_MODE \
              OPIP_PRODUCTION_SCORING_VERSION OPIP_UNIFIED_CYCLE_BUDGET_SECONDS OPIP_PHASE_RESOURCE_TELEMETRY; do
    LINE="$(printf '%s\n' "$ENVDUMP" | grep -E "^${NAME}=" | head -n 1)"
    if [ -n "$LINE" ]; then echo "$LINE"; else echo "${NAME}=<UNSET> (code default applies; not evidence of OFF)"; fi
  done
  echo "-- any other learning/auto/trading-flag variables present --"
  printf '%s\n' "$ENVDUMP" | grep -E '^(KRAKEN_.*(TRADING|LIVE|ORDER).*|OPIP_.*(LEARNING|AUTO).*)=' \
    | sed 's/\(KEY\|SECRET\|TOKEN\)=.*/\1=REDACTED/' || true
else
  echo "UNAVAILABLE: core container not found"
fi

echo; echo "BUNDLE_COMPLETE (no state was changed)"
