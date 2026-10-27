INCREMENT:
ATDD-TRADEVISOR-HOSTNAME-TLS-v1

OWNER-APPROVED INTENT:
The webhook vhost merged in ATDD-TRADEVISOR-PRODUCTION-EDGE-v1 named
`161-35-106-207.sslip.io`, the hostname the tracked dashboard edge carries. DNS
shows that name resolves to `161.35.106.207`, which is not either production
droplet, while the Trading droplet is `178.128.147.125` and
`178-128-147-125.sslip.io` resolves to it. Presenting certificate material for
the wrong name on :443 would fail hostname verification for any genuine
TradingView delivery, so the webhook vhost is corrected to the name that actually
resolves to the host serving it, and to that name's certificate directory. The
dashboard keeps 8443 and its existing certificate untouched: it is a pre-existing
condition outside this correction. Because a vhost whose certificate file is
absent prevents nginx from starting at all, the render is also gated on the
certificate existing for this name, so an unissued certificate leaves the webhook
dark instead of taking the dashboard down. Authentication, the source allowlist,
the edge stamp, the body token, the peer pinning and the observation-only
client-certificate handling are unchanged.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.template: the webhook vhost corrected here.
- OHM-Trade-Agent-v1/docker-compose.yml: the edge entrypoint that renders the vhost.
- OHM-Trade-Agent-v1/deploy/nginx/dashboard-sidecar.conf: the ARCH-5 protected dashboard edge, deliberately unchanged.
- OHM-Trade-Agent-v1/deploy/m07/11-HF-CUTOVER-PROCEDURE.md: the recorded certificate-issuance and validation procedure for `178-128-147-125.sslip.io`.
- OHM-Trade-Agent-v1/TRADEVISOR_V2.md: the operator documentation updated with the hostname and certificate prerequisite.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: a Trading droplet at 178.128.147.125, a webhook URL that must resolve to it, and a historical dashboard hostname that resolves elsewhere
WHEN: the webhook template is read
THEN: its only `server_name` is `178-128-147-125.sslip.io`, the historical name appears nowhere in it, and `ssl_certificate`/`ssl_certificate_key` both resolve inside `/etc/letsencrypt/live/178-128-147-125.sslip.io/`

AC-002:
GIVEN: a certificate that may not yet exist for the corrected name and a dashboard that must keep serving on 8443
WHEN: the edge entrypoint and the tracked dashboard configuration are read
THEN: the render happens only when the certificate file is present and non-empty, an absent certificate leaves the webhook vhost dark while nginx still starts, and the dashboard listener, certificate paths and locations are unchanged

EXPLICITLY OUT OF SCOPE:
- Issuing, renewing or installing any certificate, and any ACME or firewall change; those require owner-controlled host access.
- Correcting the dashboard's historical `161-35-106-207.sslip.io` name, which is a pre-existing condition recorded here but deliberately not changed.
- Rewriting historical evidence such as `deploy/m07/*` records and validation scripts.
- Deploying, restarting containers, enabling the webhook or changing any `OPIP_TRADEVISOR_*` value.
- Rotating or reading the existing production secrets.

FROZEN BOUNDARIES:
- Authentication stays: TradingView source allowlist, edge stamp, body token, peer pinning, 404-on-mismatch.
- `ssl_verify_client optional_no_ca` remains observation only and is not authentication.
- The dashboard keeps 8443, its certificate paths and its read-only tracked mount (ARCH-5).
- The application port stays `127.0.0.1:8000` and is never published publicly.
- No secret value, and no value derived from one, may appear in the repository.
- Fixed risk sizing, minimum reward-to-risk, PAPER_V2, Committee, learning, canonical-writer and F11 behaviour are untouched.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_tradevisor_production_edge_v1.py::test_ac_001_the_webhook_vhost_names_the_trading_droplet
AC-001 -> tests/test_tradevisor_production_edge_v1.py::test_ac_001_the_certificate_path_matches_the_hostname
AC-002 -> tests/test_tradevisor_production_edge_v1.py::test_ac_002_a_missing_certificate_leaves_the_vhost_dark
AC-002 -> tests/test_tradevisor_production_edge_v1.py::test_ac_002_the_dashboard_certificate_and_listener_are_untouched

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.template
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-001 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-HOSTNAME-TLS-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-002 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-002 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md

DEFERRED DISCOVERIES:
- Certificate existence, SAN and expiry on the Trading droplet cannot be verified from the agent environment: TCP 443 and 8443 reject connections from here, so `openssl s_client -servername 178-128-147-125.sslip.io -connect 178.128.147.125:443` remains an owner verification step.
- DNS for `178-128-147-125.sslip.io` resolves to `178.128.147.125`; `161-35-106-207.sslip.io` resolves to `161.35.106.207`, confirming the merged name was wrong.
- The dashboard's own certificate name is historical and unresolved to the current droplet; correcting it is a separate decision not taken here.
- No ACME renewal automation exists in the repository; renewal remains host-managed.

UNAPPROVED SCOPE CHANGES:
NONE
