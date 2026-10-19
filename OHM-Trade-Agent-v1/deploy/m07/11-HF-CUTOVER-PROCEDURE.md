# 11 — Single Ordered V2 Cutover Procedure

One sequence, executed by the owner. Nothing here was executed by this mission.

**Phase 0 — live prerequisites (read-only)**
```
ssh opipadmin@178.128.147.125 'curl -fsS http://169.254.169.254/metadata/v1/id'          # 610307364
pwsh -File scripts/Invoke-V2Readiness.ps1 -Phase PreBootstrap -Execute -Authorized        # must reach 0
```
PreBootstrap proves: identity, `/srv/opip-v2/releases/<sha>`, certified image label, ≥512 MiB free, ≥1 canonical backup, container count.

**Phase 1 — prepare bootstrap (host writes only receipts)**
```
sudo -n install -d -m 0700 /tmp/opip-v2
sudo -n install -m 0700 /dev/stdin /tmp/opip-v2/bootstrap-v2.sh   < scripts/v2/bootstrap-v2.sh
sudo -n install -m 0644 /dev/stdin /tmp/opip-v2/docker-compose.v2.yml  < drafts/docker-compose.v2.yml
sudo -n install -m 0644 /dev/stdin /tmp/opip-v2/nginx-v2.conf          < drafts/nginx-v2.conf
sudo -n bash /tmp/opip-v2/bootstrap-v2.sh --mode prepare
```
Produces `BOOTSTRAP_PREPARED.json` containing the **independently evaluated** protection verdict (`ready`, `reason_codes`, `blocking_incidents`, `fresh_host_only`).

**Phase 2 — owner authorization**
```
sudo -n bash /tmp/opip-v2/bootstrap-v2.sh --mode authorize \
  --confirm-sha c196dd674678b3b1ee91c351ee97c1c9e2b3938d --confirm-host 610307364 \
  [--acknowledge-fresh-host]
```
- `blocking_incidents > 0` → refused, always.
- `ready=false` + fresh-host-only reason codes + acknowledgement → allowed (documented migration).
- `ready=false` for any other reason → refused; acknowledgement cannot override it.

**Phase 3 — execute**
```
sudo -n bash /tmp/opip-v2/bootstrap-v2.sh --mode execute [--activate-scheduler]
```
Order inside: dirs/permissions → external config installed → compose render + paper-service guard → core up → `/health` → release gate (`ARCHITECTURE_GATE=PASS`) → writer up → scheduler only if authorised → `BOOTSTRAP_EXECUTED.json`. The tracked nginx file is never modified.

**Phase 4 — post-configuration readiness**
```
pwsh -File scripts/Invoke-V2Readiness.ps1 -Phase PostConfiguration -Execute -Authorized
```
Proves checkout at SHA, `.env` 0600 root, effective compose limits/modes, volumes, release gate.

**Phase 5 — data migration**
```
# 5a inventory (V1 evidence, read-only)          -> -Mode Inventory
# 5b export (certified generation only)          -> -Mode Export -Authorize
# 5c transfer archive + bundle.sha256
# 5d verify from the archive alone                -> -Mode Verify
# 5e provenance (host evidence)                   -> -Mode Provenance
# 5f stage                                        -> -Mode Stage
# 5g native restore (Linux, writer stopped)       -> -Mode RestoreCanonical -Authorize
# 5h promote (receipt re-verified)                -> -Mode Promote -Authorize -RecoveryReceipt <path>
# 5i reconcile                                    -> -Mode Reconcile
```
Step 5g writes the recovery receipt; 5h refuses without it and re-reads the destination database.

**Phase 6 — HTTPS**
```
sudo -n certbot certonly --standalone -d 178-128-147-125.sslip.io --agree-tos -m <owner> --non-interactive
curl -sS -o /dev/null -w '%{http_code}' --resolve 178-128-147-125.sslip.io:8443:178.128.147.125 https://178-128-147-125.sslip.io:8443/dashboard   # 200
curl -sS -o /dev/null -w '%{http_code}' … /api/analytics/summary                                                                                  # 401
```
Restrict 8443 to the owner address range; keep 8000 loopback-only; confirm `certbot renew --dry-run`.

**Phase 7 — Telegram (owner credentials)**
```
docker compose exec -T ohm-trade-agent python -c "…send_tracked_telegram(identity='R1PROBE'…)"
docker compose exec -T ohm-trade-agent python -c "…build_delivery_summary(scope='today')"
```
The tracked receipt (DELIVERED + message id) is the acceptance evidence, not the probe alone.

**Phase 8 — scheduler activation**
Only after phases 3–7 pass, and only `--activate-scheduler` (or the certified reconciliation script), then confirm exactly one cron line.

**Rollback (any point)**
```
sudo -n bash /tmp/opip-v2/rollback-v2.sh --mode prepare --kind safe-baseline --previous-sha <sha>
pwsh -File scripts/Invoke-V2Rollback.ps1 -Phase Execute -Kind safe-baseline -PreviousSha <sha> -Authorize -ConfirmSha <sha>
```
Epochs come from the destination database; a newer epoch refuses; canonical is preserved and hashed before any switch.
