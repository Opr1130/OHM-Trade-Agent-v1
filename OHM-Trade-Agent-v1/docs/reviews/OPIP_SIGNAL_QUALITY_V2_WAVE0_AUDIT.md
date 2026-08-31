# O'Pip Signal Quality v2 — Wave 0 Production Audit

Status: PARTIAL — repository/workflow evidence verified; live container state still requires one read-only production command.

## Verified before shell audit

- Main/design baseline reviewed at `67b2500cb569f2665e3e19099e07bd844bebcc75`.
- Raw deployment workflow logs show the remote deploy reached `O'Pip deployment succeeded`, reported `sha=67b2500cb569f2665e3e19099e07bd844bebcc75`, the core container healthy, paper workers healthy, and `/health` returned `{"status":"ok"}`.
- The GitHub deployment workflow nevertheless labeled that run `SERVER DEPLOY FAILED` because the workflow greps for `OHM scheduler reconciliation: OK` while the remote script emits `O'Pip scheduler reconciliation: OK`. This is a receipt/observability false negative, not evidence that the remote deploy rolled back.
- DigitalOcean reports the primary `OHM-AI-Trading-Platform` droplet active.

## Live values still required

1. `KRAKEN_RECONCILIATION_ENABLED`
2. `KRAKEN_RECONCILIATION_MODE`
3. counts of pending lifecycle rows terminalized `tracking_failed` and `send_failed`
4. Kraken non-zero account assets versus active registry base assets
5. Kraken open-position count/pairs
6. read-only key verification

## Read-only production command

Run from the production host. This command does not place, modify, cancel, fill, or close any order/trade.

```bash
cd /opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1

printf 'host_repo_sha='
git -C /opt/OHM-Trade-Agent-v1 rev-parse HEAD
printf 'last_good_sha='
cat /var/lib/ohm-deploy/last-good-sha 2>/dev/null || echo UNKNOWN

docker compose exec -T ohm-trade-agent python - <<'PY'
import json
from collections import Counter

from app.exchanges.kraken_identity import (
    canonicalize_asset,
    canonicalize_pair,
    split_canonical_pair,
)
from app.exchanges.kraken_private import KrakenPrivateClient
from app.services.active_trade_registry import get_active_trades
from app.services.kraken_reconciliation import reconciliation_enabled, reconciliation_mode
from app.services.pending_setup_registry import PENDING_FILE
from app.services.registry_io import load_json, registry_lock

def base_asset(symbol):
    identity = split_canonical_pair(symbol)
    return identity[0] if identity else None

result = {
    'reconciliation': {
        'enabled': reconciliation_enabled(),
        'mode': reconciliation_mode(),
    },
}

active = get_active_trades()
result['active_registry'] = {
    'count': len(active),
    'symbols': sorted({t.symbol for t in active}),
    'base_assets': sorted({a for a in (base_asset(t.symbol) for t in active) if a}),
}

with registry_lock(PENDING_FILE.parent / '.trade_registry.lock'):
    raw = load_json(PENDING_FILE)

canonical_rows = []
seen_trade_ids = set()
for row in raw.values():
    if not isinstance(row, dict) or row.get('legacy_symbol_tombstone'):
        continue
    trade_id = str(row.get('trade_id') or '')
    if trade_id and trade_id in seen_trade_ids:
        continue
    if trade_id:
        seen_trade_ids.add(trade_id)
    canonical_rows.append(row)

counts = Counter(str(row.get('status') or 'UNKNOWN') for row in canonical_rows)
result['pending_lifecycle'] = {
    'canonical_rows': len(canonical_rows),
    'status_counts': dict(sorted(counts.items())),
    'tracking_failed': counts.get('tracking_failed', 0),
    'send_failed': counts.get('send_failed', 0),
}

client = KrakenPrivateClient()
result['kraken'] = {'credentials_configured': client.enabled}

if client.enabled:
    try:
        key = client.assert_read_only()
        result['kraken']['read_only_verified'] = key.is_read_only
        balances = client.get_balance()
        positions = client.get_open_positions()

        nonzero_assets = sorted({
            canonicalize_asset(asset)
            for asset, amount in balances.items()
            if float(amount) > 0
        })
        active_assets = set(result['active_registry']['base_assets'])
        result['kraken']['nonzero_assets'] = nonzero_assets
        result['kraken']['assets_not_in_active_registry'] = sorted(set(nonzero_assets) - active_assets)
        result['kraken']['open_positions_count'] = len(positions)
        result['kraken']['open_position_pairs'] = sorted({
            canonicalize_pair(str(row.get('pair') or ''))
            for row in positions.values()
            if isinstance(row, dict) and row.get('pair')
        })
    except Exception as exc:
        result['kraken']['audit_error'] = f'{type(exc).__name__}: {exc}'

attention = []
if not client.enabled:
    attention.append('KRAKEN_CREDENTIALS_UNAVAILABLE')
if not result['reconciliation']['enabled']:
    attention.append('KRAKEN_RECONCILIATION_DISABLED')
if result['reconciliation']['mode'] != 'apply':
    attention.append('KRAKEN_RECONCILIATION_NOT_APPLY')
if result['pending_lifecycle']['tracking_failed']:
    attention.append('TRACKING_FAILED_SETUPS_PRESENT')
if result['pending_lifecycle']['send_failed']:
    attention.append('SEND_FAILED_SETUPS_PRESENT')
if result.get('kraken', {}).get('audit_error'):
    attention.append('KRAKEN_AUDIT_ERROR')

result['attention'] = attention
result['status'] = 'PASS' if not attention else 'ATTENTION_REQUIRED'
print(json.dumps(result, indent=2, sort_keys=True))
PY
```

## Interpretation

- `enabled=false` or `mode!=apply` is a blocking Wave-0 finding because current qualified-alert tracking requires reconciliation apply mode.
- non-zero Kraken assets absent from the active registry are candidate unmanaged holdings; settlement/stablecoin/dust assets must be classified conservatively before any automatic protection lifecycle is created.
- `tracking_failed` and `send_failed` counts estimate qualified-opportunity recall loss caused by terminalization on tracking/delivery failure.
- no mutation should be made from this audit output. Wave 1 design should consume it and then implement Kraken-primary exposure resolution.

## Additional operational defect found

The production deployment receipt workflow should later be corrected to match the scheduler reconciliation string actually emitted by the deploy script. This is an operational-resilience fix, separate from the trading architecture.