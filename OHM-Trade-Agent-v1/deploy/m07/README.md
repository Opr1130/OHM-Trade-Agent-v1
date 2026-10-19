# Mission 07 — V2 cutover toolkit (`deploy/m07`)

Offline release-engineering tooling for the O'Pip V1→V2 cutover: bundle
verification, canonical promotion, host bootstrap, rollback, readiness and
provenance. **It grants no trading authority and performs no deployment by
itself.**

## Document authority

Release authority for this toolkit is the **R3** set:

1. `R3-FINAL-VERDICT.md` — certification decision
2. `R3-RELEASE-QUALIFICATION.md` — what was proven, how, and what is blocked
3. `R3-OWNER-GATES.md` — the live gates (L-01…L-10) and acceptance criteria
4. `06-SINGLE-CUTOVER-RUNBOOK.md` — the ordered procedure (**R2 paths updated by R3**)
5. `R3-SECURITY-REVIEW.md` — security/adversarial review

`01-…` through `15-…` are the preserved R1/R2 evidence chain. They are
**historical**: where they contradict R3 (toolkit path, owner-staged overlay,
`L-05`), R3 wins. Nothing in them was rewritten.

## Layout

```
deploy/m07/
├── scripts/
│   ├── opip_archive.py          digest-first, member-safe archive extraction
│   ├── opip_bundle.py           inventory / export / verify / stage / promote / reconcile
│   ├── opip_recovery.py         native canonical restore + post-restore validation
│   ├── opip_provenance.py       export identity + provenance gates
│   ├── opip_layout.py           the single host-layout contract
│   ├── opip_receipts.py         digest-bound, expiring, single-use receipts
│   ├── opip_rollback_guard.py   WAL-safe snapshot, history guard, writer gate
│   ├── Invoke-*.ps1             operator entrypoints (PowerShell 7)
│   ├── lib/OpipR1.Common.ps1    strict known-host SSH, receipts
│   └── v2/{bootstrap,rollback}-v2.sh   host scripts (LF-only, bash 4.3+)
├── tests/                       offline regressions (no network, no host)
├── drafts/                      dataset spec + overlay fixture
└── evidence/                    generated at test time (git-ignored)
```

## Runtime contract (R3)

| Item | Path |
|---|---|
| Release source (immutable) | `/srv/opip-v2/releases/<sha>/OHM-Trade-Agent-v1` |
| Runtime app root | `/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1` |
| Compose project | `cd "$APP_ROOT" && docker compose …` — **one** file: `docker-compose.yml` |
| Environment | `$APP_ROOT/.env` (the only `.env`) |
| Data / canonical DB | `$APP_ROOT/data/opip/canonical/opip_canonical_v1.sqlite3` |

The bootstrap and rollback invoke Compose **exactly as the certified controller
does**. They never layer an owner-supplied overlay on the core stack, and they
never generate an nginx config over the tracked one. `test_r3_controller_parity.py`
enforces this against `deploy/remote/ohm-deploy` itself.

## Run the offline suite

```bash
cd OHM-Trade-Agent-v1/deploy/m07
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/scenarios -q     # 116 tests
```

No test contacts the network, a container daemon, a database server or a host.
