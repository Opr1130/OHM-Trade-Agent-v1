# 03 — Revised Deployment Procedure (R1)

Two distinct paths. They must never be mixed.

| Situation | Path | Entry point |
|---|---|---|
| No `ohm-trade-agent` container on V2 | **Bootstrap** | `scripts/Invoke-V2Bootstrap.ps1` |
| An O'Pip deployment already exists | **In-place upgrade** | certified `/usr/local/sbin/ohm-deploy` |

## Why split (proven from source)

`ohm-deploy:1347` reads the image from the **existing container**:

```bash
image="$(docker inspect --format '{{.Image}}' ohm-trade-agent 2>/dev/null || true)"
...
if [[ -d "$candidate_app" && -n "$image" && -f "$APP_ROOT/.env" ]]; then   # :1369
```

With no container, `image` is empty, the preflight body is skipped, `rc` stays `1`, and the deploy exits `77` (`PROTECTION PREFLIGHT BLOCKED`). The controller's success path also starts the Freqtrade paper stack (`:1684`) and retires the P1 outbox (`:1815`). None of that belongs in a first install.

## A. Bootstrap (first install)

1. **Prove the host is fresh** (read-only, as `opipadmin`):

```
ssh opipadmin@178.128.147.125 'curl -fsS http://169.254.169.254/metadata/v1/id'   # 610307364
ssh opipadmin@178.128.147.125 'docker ps -a --format "{{.Names}}" | grep -c "^ohm-trade-agent$"'
ssh opipadmin@178.128.147.125 'test -s /var/lib/ohm-deploy/last-good-sha; echo $?'
```

Write the facts to `host-facts.json`, then:

```
pwsh -File scripts/Invoke-V2Bootstrap.ps1 -ContainerPresent no -HostFactsPath host-facts.json
```

Exit `77` = plan (expected). A local path can only return `2` (INSUFFICIENT); mutation requires host facts.

2. **Authorise** (owner):

```
pwsh -File scripts/Invoke-V2Bootstrap.ps1 -ContainerPresent no -HostFactsPath host-facts.json `
  -Authorize -ConfirmSha c196dd674678b3b1ee91c351ee97c1c9e2b3938d -ConfirmHost 610307364 `
  -AcknowledgeFreshHostPreflight
```

`-AcknowledgeFreshHostPreflight` is mandatory because the certified preflight is **expected to block** on a fresh host. The block is recorded, never skipped.

3. **Execute** the printed sequence. Its critical properties:

| Step | Property |
|---|---|
| `git checkout --detach <sha>` | exact-release tree, no branch build |
| overlay installed at `/etc/opip/v2/…` | outside the checkout; controller path untouched |
| `docker compose build --build-arg OPIP_RELEASE_SHA=<sha>` | image label = certified SHA |
| `docker run … preflight_protection_health` | **certified preflight executed and its receipt kept** |
| `up -d --no-deps ohm-trade-agent` | core only — no paper stack, no writer race |
| `release_profiles --profile EVIDENCE_SHADOW` | authority gate |
| `up -d --no-deps opip-canonical-writer` | single writer |
| `reconcile-scheduler.sh` | one scheduler file, legacy entries stripped |
| uniqueness counters | core=1, writer=1, cycle cron=1, paper=0 |

Not performed by bootstrap: `ohm-deploy`, `start_paper_stack`, `retire-p1-shadow-outbox.sh`, any V1 action.

## B. In-place upgrade (after bootstrap)

```
export COMPOSE_FILE=/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1/docker-compose.yml:/etc/opip/v2/docker-compose.v2.yml
/usr/local/sbin/ohm-deploy <certified-sha>
```

Prerequisites proven by `Invoke-V2Readiness.ps1 -Execute -Authorized` (opipadmin, metadata identity, image label read on the host, `docker compose config` on the host, no paper service in the project).

The controller keeps its own semantics: protection preflight → build → writer/core → health → writer resource pin → scheduler reconciliation → paper topology (unchanged) → runtime verifier → P1 retirement (post-commit, irreversible, owner-authorised).

## Guardrails retained

* `EVIDENCE_SHADOW` literals live in the base compose; the overlay repeats them at identical values and is rejected if it widens anything.
* Writer cgroup stays at the controller-pinned `128m/128m/0.15/64`.
* No new published port: core `127.0.0.1:8000`, edge `8443`.
* Paper V2 stays `off`; a selected Freqtrade service fails readiness with exit 3.

## What still requires live authorisation

Host facts, image build, TLS issuance, container creation. Nothing in this document was executed against V2.
