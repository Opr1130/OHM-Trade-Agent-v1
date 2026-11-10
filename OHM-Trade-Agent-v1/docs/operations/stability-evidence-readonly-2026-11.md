# O'Pip — bounded stability evidence (read-only)

This increment is **observability only**, not trading, learning or alert activation.

## Run on an authorized host/operator shell

```sh
cd /opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1
python3 tools/opip_stability_snapshot.py \
  --root ./data \
  --cycle-log /var/log/ohm-unified-cycle.log \
  --hours 24
```

Output is JSON containing aggregate outcomes, AI call/token counts, router
provider/error counters, Telegram status/family/suppression counts, current
`/proc/meminfo` and filesystem available capacity. No private trade content,
Telegram tokens, exchange credentials or model prompts are emitted.

Read access to host files is necessary. The forced-command deploy gateway does
**not** permit shell commands; do **not** bypass it or change credentials.
This command is not automatically installed or executed by the release.

Important limitations:

- Missing, unreadable or tail-truncated files are marked INCOMPLETE.
- Unstamped cycle log lines do not establish a 24-hour success/failure rate;
  timestamped `OPIP_UNIFIED_CYCLE_COMPLETED_AT` markers count completions only.
- The machine metrics describe the command's Linux namespace, **not** necessarily
  the full host when called from inside a container.
- No Docker per-container RSS/restart/OOM measurements or database table sizes
  are inferred by this command. Those need separate authorized host probes.
- AI token counts are not billing amounts; USD cost remains unknown.
- Missing or disabled delivery capture must never be reported as zero messages.

## Existing controls discovered

- `CHIEF_REVIEW_CACHE_TTL_SECONDS` defaults to 0 (disabled).
- `OPENAI_DAILY_CALL_BUDGET` and `OPENAI_DAILY_TOKEN_BUDGET` default to 0
  (unlimited). The current runtime values **have not been verified**.
- Do not set a cache TTL or a budget without measuring candidate freshness,
  routing and actual usage. A cache hit is advisory and cannot grant entry
  authority. No settings are changed by this increment.
- Current notification deduplication and attention budgets already exist;
  identify the actual Telegram source before changing notification gates.

## Log rotation policy

`deploy/logrotate.d/opip` specifies daily/max-10M rotation for host cycle,
and dispatcher logs, retaining seven compressed generations.
Unique per-attempt scan logs are **not** controlled by this policy; their age-
based removal needs a separate owner-approved, evidence-preserving retention task.
It is deliberately **not installed** by this PR. Host release ownership,
logrotate presence, and running writer behavior must be checked before a
separately authorized installation.

## Release policy

Keep funded auto-trading, learning, Committee and all new strategy activation
OFF. Preserve F11 independent fail-closed protection. No database deletion,
legacy learning-export repair, host restart or production deployment is part of
this implementation.
