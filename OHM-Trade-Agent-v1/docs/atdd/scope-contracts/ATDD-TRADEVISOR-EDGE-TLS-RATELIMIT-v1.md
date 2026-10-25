INCREMENT:
ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1

OWNER-APPROVED INTENT:
Address the two P2 review findings raised after the Tradevisor edge example merged. (1) TLS: with several virtual hosts sharing :443, nginx selects the TLS policy of the SNI host, and a client that sends no SNI or an unknown one is served by the default server for that socket, so a sibling vhost could silently negotiate a weaker protocol for this webhook. The example now declares the webhook vhost as the dedicated default server on :443, states the consequence (a second default server, or a host that already has one, fails `nginx -t` with "duplicate default server"), and the TLS floor remains TLS 1.2 at server scope. (2) Rate limits: the edge allows 30 requests/minute per TradingView source while the application allows 60/minute per *immediate peer*, and that peer is always the single trusted proxy - so the application limit is an aggregate ceiling shared by all four sources. This increment documents that arithmetic, the 429-versus-503 distinction, the no-receipt-on-refusal rule and the owner decision that `60` is an unapproved aggregate capacity. It raises no limit, changes no admission policy, and activates nothing.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.example: the reviewed edge example.
- OHM-Trade-Agent-v1/app/api/tradevisor.py: the in-process limiter (per immediate peer) and the 404/422/429/503 status contract.
- OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py: the durable inbox whose capacity produces 503 backpressure.
- OHM-Trade-Agent-v1/app/core/config.py: `OPIP_TRADEVISOR_MAX_REQUESTS_PER_MINUTE` (default 60) and the trusted-proxy setting.
- OHM-Trade-Agent-v1/TRADEVISOR_V2.md: the operator documentation, including "Two edge deployment modes".
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-V2-ADVISORY-v1.md: the increment that owns the edge contract.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the reviewed edge example, a host that serves the webhook on :443 alongside other virtual hosts, and a client that offers no SNI or an unknown SNI
WHEN:
the configuration is read and, where nginx and openssl are available, a real handshake is attempted
THEN:
the webhook vhost declares `listen 443 ssl default_server`, the TLS minimum is TLS 1.2 at server scope with no weaker protocol anywhere in the file, the reasoning is documented, and the negotiated floor really is TLS 1.2 (TLS 1.1 is refused) when the opt-in handshake test runs

AC-002:
GIVEN:
a conflicting configuration in which a second virtual host also claims the default server for the same port, and separately a competing handshake that would negotiate an older protocol
WHEN:
deployment validation runs
THEN:
the example declares the default server exactly once, keeps every non-webhook path at 404, and a real `nginx -t` refuses the conflict with "duplicate default server" instead of letting the weaker policy win silently

AC-003:
GIVEN:
the operator documentation and the two limiter layers
WHEN:
an operator reads the rate-limit contract
THEN:
it states the edge limit as 30 requests per minute per TradingView source, the application limit as 60 requests per minute per immediate peer, that the application limit is an aggregate ceiling shared by all sources, that 429 is throttling and 503 is capacity backpressure, and that the aggregate capacity is an unapproved owner decision that this increment does not change

AC-004:
GIVEN:
one source, several independent sources arriving through the single trusted proxy, 59, 60 and 61 aggregate requests, a full durable inbox, a duplicate delivery, a refused request and a spoofed edge header
WHEN:
the route is exercised
THEN:
the first 60 aggregate requests are accepted and the 61st is throttled with 429, distinct sources do not receive separate application budgets, a full inbox produces 503 and never 429 (and throttling never produces 503), no refused request creates a durable receipt, a duplicate delivery is acknowledged once with a `duplicate` status and no second row, and a client-supplied verification header or unknown ingestion token cannot bypass admission

AC-005:
GIVEN:
the shipped application configuration
WHEN:
this increment is reviewed
THEN:
`OPIP_TRADEVISOR_MAX_REQUESTS_PER_MINUTE` is still 60, the limiter window is still 60 seconds, and no admission control, source restriction, header control or body cap was relaxed

EXPLICITLY OUT OF SCOPE:
- Any change to `OPIP_TRADEVISOR_MAX_REQUESTS_PER_MINUTE`, the limiter window, the limiter key or the burst allowance
- Any change to the four-IP allowlist, the verification-header stamp, the body-token check, the body cap or the opaque-404 behaviour
- Any change to the FastAPI admission, evaluation, notification or learning paths
- Any deployment, nginx reload, certificate issuance, DNS, Cloudflare or firewall change, and any modification of a running staging nginx
- Implementing the Cloudflare-proxied edge mode
- Any trading, paper, execution, canonical-writer or release-profile change

FROZEN BOUNDARIES:
- TLS minimum stays TLS 1.2 for any handshake that reaches this edge.
- The webhook vhost stays dedicated: `location / { return 404; }` is the only other location.
- `limit_req_status 429` stays at server scope, so throttling is never confused with capacity backpressure.
- The application limiter remains keyed on the immediate peer and remains defense in depth.
- A refused request never receives a durable acceptance receipt.
- No secret, credential, certificate or running service is touched by this increment.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_001_the_dedicated_edge_is_the_default_server_so_no_sni_falls_elsewhere
AC-001 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_001_the_tls_floor_is_declared_at_server_scope_and_never_weakened
AC-001 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_001_the_multi_vhost_reasoning_is_documented
AC-001 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_001_a_real_handshake_refuses_tls11_and_accepts_tls12
AC-001 -> tests/test_tradevisor_edge_tls_v1.py::test_ac_001_a_valid_configuration_passes_nginx_t
AC-001 -> tests/test_tradevisor_edge_tls_v1.py::test_ac_001_tls12_and_tls13_are_accepted
AC-001 -> tests/test_tradevisor_edge_tls_v1.py::test_ac_001_deprecated_protocols_are_refused_by_the_server
AC-001 -> tests/test_tradevisor_edge_tls_v1.py::test_ac_001_missing_sni_cannot_reach_a_deprecated_protocol
AC-001 -> tests/test_tradevisor_edge_tls_v1.py::test_ac_001_unknown_sni_cannot_reach_a_deprecated_protocol
AC-001 -> tests/test_tradevisor_edge_tls_v1.py::test_ac_001_a_valid_sni_is_served_by_the_intended_vhost
AC-002 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_002_a_competing_default_server_must_fail_validation
AC-002 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_002_nginx_refuses_two_default_servers_on_one_port
AC-002 -> tests/test_tradevisor_edge_tls_v1.py::test_ac_002_a_competing_default_server_is_rejected_by_real_nginx
AC-003 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_003_the_aggregate_rate_limit_contract_is_documented
AC-004 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_004_the_aggregate_ceiling_is_59_60_accepted_and_61_refused
AC-004 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_004_multiple_sources_share_the_single_peer_bucket
AC-004 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_004_throttling_is_429_and_capacity_is_503
AC-004 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_004_a_refused_request_never_receives_a_durable_receipt
AC-004 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_004_a_duplicate_delivery_is_acknowledged_once
AC-004 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_004_a_spoofed_edge_header_cannot_bypass_admission
AC-005 -> tests/test_tradevisor_edge_contract_v1.py::test_ac_005_the_application_limit_is_unchanged_by_this_increment
AC-005 -> tests/test_tradevisor_edge_tls_v1.py::test_ac_005_the_shipped_listener_is_ipv4_only_and_declares_one_default_server

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.example
AC-001 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_edge_contract_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_edge_tls_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_webhook_v1.py
AC-002 -> OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.example
AC-002 -> OHM-Trade-Agent-v1/tests/test_tradevisor_edge_contract_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_tradevisor_edge_tls_v1.py
AC-003 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_tradevisor_edge_contract_v1.py
AC-004 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_tradevisor_edge_contract_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_tradevisor_edge_contract_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_tradevisor_edge_tls_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1.md

DEFERRED DISCOVERIES:
- The aggregate application ceiling (60/minute across all four sources) is documented but not decided. Raising it, or giving each source its own budget, changes `OPIP_TRADEVISOR_MAX_REQUESTS_PER_MINUTE` or the limiter key and needs an owner-approved capacity decision.
- The Cloudflare-proxied edge mode remains unimplemented; its identity and rate-limit keying requirements are documented only.
- The opt-in TLS handshake test starts a real nginx process and is skipped unless nginx, openssl and `OPIP_NGINX_HANDSHAKE_TEST=1` are all present, so a green suite never implies the negotiated floor was observed.
- The Linux-mandatory module `tests/test_tradevisor_edge_tls_v1.py` executes the negotiated-protocol matrix on Linux CI: a missing nginx or openssl there is a FAILURE, not a skip, and a client that cannot offer a deprecated protocol fails the certification instead of passing it. Local Windows runs skip that module because they cannot provide the proof.
- The `nginx -t` conflict test is skipped where nginx or openssl is absent; the structural assertions still pin that exactly one default-server declaration exists.
- This example assumes a dedicated edge host. A future multi-vhost host would need its own reviewed configuration rather than this file.

UNAPPROVED SCOPE CHANGES:
NONE

