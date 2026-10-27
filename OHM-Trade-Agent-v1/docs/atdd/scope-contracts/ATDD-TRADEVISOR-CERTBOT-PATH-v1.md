INCREMENT:
ATDD-TRADEVISOR-CERTBOT-PATH-v1

OWNER-APPROVED INTENT:
The edge vhost merged in ATDD-TRADEVISOR-HOSTNAME-TLS-v1 pointed its certificate
paths at `/etc/letsencrypt/live/178-128-147-125.sslip.io/`, a directory that does
not exist on the Trading droplet. The owner's verified Certbot report shows
certificate name `161-35-106-207.sslip.io` covering the served name
`178-128-147-125.sslip.io`, so the only managed material lives in
`/etc/letsencrypt/live/161-35-106-207.sslip.io/`. Because nginx refuses to start
when a vhost certificate file is missing, the merged paths would have taken the
dashboard down with the webhook. This increment corrects only the certificate and
private-key paths (and the render gate that checks the same path), keeps the
served identity `178-128-147-125.sslip.io` on 443, keeps the dashboard on 8443 and
its own certificate untouched, and adds one operator script that proves every
activation prerequisite before advisory ingress is enabled. No certificate is
issued, renamed, replaced or renewed; no Certbot symlink is touched; no deployment
path other than the GitHub controller is introduced.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.template: the webhook vhost whose certificate paths are corrected.
- OHM-Trade-Agent-v1/docker-compose.yml: the edge entrypoint render gate that checks the same certificate path.
- OHM-Trade-Agent-v1/deploy/nginx/dashboard-sidecar.conf: the ARCH-5 protected dashboard edge, deliberately unchanged.
- OHM-Trade-Agent-v1/deploy/tradevisor/activate-tradevisor-advisory.sh: the single operator activation script added here.
- OHM-Trade-Agent-v1/TRADEVISOR_V2.md: the operator documentation carrying the corrected certificate prerequisite.
- OHM-Trade-Agent-v1/app/services/tradevisor_ingress.py: the parser that defines the operator CSV formats.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
certificate name `161-35-106-207.sslip.io` covering the served name `178-128-147-125.sslip.io`, and a merged vhost pointing at a live directory named after the served host
WHEN:
the tracked webhook template is read
THEN:
its only `server_name` is `178-128-147-125.sslip.io`, `ssl_certificate` and `ssl_certificate_key` both resolve inside `/etc/letsencrypt/live/161-35-106-207.sslip.io/`, no `/etc/letsencrypt/live/178-128-147-125.sslip.io/` path remains, and the file explains in place that the Certbot name is a directory name that is never renamed

AC-002:
GIVEN:
a render gate that must not start nginx with a certificate path that does not exist, and a dashboard that must keep serving on 8443
WHEN:
the production compose file is read
THEN:
the gate checks exactly `/etc/letsencrypt/live/161-35-106-207.sslip.io/fullchain.pem`, an absent file leaves the vhost dark while nginx still starts, and neither the template nor the compose file retains the nonexistent served-name directory

AC-003:
GIVEN:
an operator who must enable advisory ingress on the existing Trading droplet without bypassing the GitHub deploy controller
WHEN:
the tracked activation script is read
THEN:
it is fail-fast, bound to the verified droplet addresses and the Certbot name, requires an exact 40-hex approved SHA, embeds no secret value and never invokes `ohm-deploy` or SSH, validates the composed nginx configuration in a throwaway container before the edge is replaced, writes the master switch exactly once and only after the gates, and restores the pre-activation posture on failure

AC-004:
GIVEN:
two operator-facing CSV variables whose formats must not be guessed
WHEN:
the application resolves its Tradevisor configuration from defaults and from an explicit override
THEN:
`OPIP_TRADEVISOR_ALERT_CONFIGS` parses as CSV of `<alert-config>=BUY|SELL`, `OPIP_TRADEVISOR_INSTRUMENTS` parses as CSV of `EXCHANGE:SYMBOL=INSTRUMENT:MARKET`, the shipped defaults are the ten explicit Coinbase pairs, and `OPIP_TRADEVISOR_OBSERVATION_ENABLED` remains false

EXPLICITLY OUT OF SCOPE:
- Issuing, renewing, installing, relocating or renaming any certificate, and any ACME change.
- Renaming or replacing any Certbot-managed directory or symlink.
- Enabling the observation/WATCH notification lane, the >94% confidence policy or the trusted 4H qualification.
- Any DigitalOcean firewall change; the exact rule set is an owner action.
- Running `/deploy`, `/deploy-learning`, or any container recreation on the host from this repository change.
- Correcting the dashboard's historical `161-35-106-207.sslip.io` server name.
- Rotating or reading any production secret value.

FROZEN BOUNDARIES:
- Authentication stays: TradingView source allowlist, edge stamp, body token, peer pinning, 404-on-mismatch.
- The dashboard keeps 8443, its certificate paths and its read-only tracked mount (ARCH-5).
- The application port stays `127.0.0.1:8000` and is never published publicly.
- No secret value, and no value derived from one, may appear in the repository or in script output.
- Paper-v2, Committee, canonical-writer, learning activation, F11 protection and funded/live execution are untouched.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_tradevisor_production_edge_v1.py::test_ac_001_the_edge_resolves_the_served_name_from_the_certbot_certificate_name
AC-002 -> tests/test_tradevisor_production_edge_v1.py::test_ac_002_the_render_gate_checks_one_certbot_path_and_stays_fail_closed
AC-003 -> tests/test_tradevisor_production_edge_v1.py::test_ac_003_the_activation_script_is_failfast_bound_and_secret_free
AC-003 -> tests/test_tradevisor_production_edge_v1.py::test_ac_003_the_activation_script_orders_gates_before_enabling_ingress
AC-004 -> tests/test_tradevisor_production_edge_v1.py::test_ac_004_the_documented_csv_formats_are_what_the_application_parses

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.template
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-001 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-HOSTNAME-TLS-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-002 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-003 -> OHM-Trade-Agent-v1/deploy/tradevisor/activate-tradevisor-advisory.sh
AC-003 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-004 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-CERTBOT-PATH-v1.md

DEFERRED DISCOVERIES:
- The certificate directory name (`161-35-106-207.sslip.io`) and the served hostname (`178-128-147-125.sslip.io`) differ because Certbot names a lineage after the first requested name; the SAN is the authority, and the operator script verifies it with `openssl x509 -checkhost` on the host.
- Certificate existence, SAN, key pairing and expiry on the droplet cannot be verified from the agent environment; they are host gates in the activation script.
- A genuine TradingView delivery remains required for G-01; no synthetic request in this increment may be counted as one.
- The >94% confidence policy and the trusted 4H qualification remain deferred and are not weakened here.

UNAPPROVED SCOPE CHANGES:
NONE

