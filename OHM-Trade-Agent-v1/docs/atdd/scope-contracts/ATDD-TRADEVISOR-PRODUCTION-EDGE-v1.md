INCREMENT:
ATDD-TRADEVISOR-PRODUCTION-EDGE-v1

OWNER-APPROVED INTENT:
Put the already-certified Tradevisor V2 webhook virtual host onto the existing
production edge so a genuine TradingView delivery can reach O'Pip. TradingView
documents that only ports 80 and 443 are accepted for webhooks, and the existing
edge serves the dashboard on 8443, so the vhost is composed onto that same
container on 443 instead of provisioning another host, service, database or
broker. The change is additive and fail-closed: the dashboard keeps 8443, its
single read-only tracked mount and every existing location; the webhook vhost
serves exactly one route and refuses everything else; the edge stamp is rendered
at container start from the tracked template using the host `.env`, so no secret
enters Git; and an absent or unsafe value leaves the vhost dark while nginx still
starts for the dashboard. Authentication does not change: source-IP allowlist,
edge stamp and body token, with the application additionally pinning the
immediate peer to the edge address on a dedicated compose network. TradingView
publishes no CA or public key, so client-certificate chain verification cannot be
configured without inventing a trust anchor; the vhost only requests the
certificate for observation and records the presented subject as delivery
evidence. This increment activates no trading, learning, PAPER_V2 or Committee
authority, changes no threshold and does not relax the trusted 4H requirement.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.example: the reviewed dedicated-host example this production file is derived from.
- OHM-Trade-Agent-v1/deploy/nginx/dashboard-sidecar.conf: the ARCH-5 protected dashboard edge configuration.
- OHM-Trade-Agent-v1/app/api/tradevisor.py: the route, the 404-on-mismatch contract and the verification header name.
- OHM-Trade-Agent-v1/app/core/config.py: the `OPIP_TRADEVISOR_*` schema and its production boot validation.
- OHM-Trade-Agent-v1/TRADEVISOR_V2.md: operator documentation and the production composition section.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1.md: the increment that owns the edge TLS and rate-limit contract.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: a shared edge container that already serves the dashboard on 8443 and a webhook route that TradingView can only reach on 443
WHEN: the rendered webhook configuration is read
THEN: it declares exactly one 443 listener at a TLS 1.2 floor, serves only `location = /webhooks/tradevisor/v1`, refuses every other path with 404, and contains no weaker protocol

AC-002:
GIVEN: the four TradingView webhook source addresses, a three second delivery budget and a shared application-side rate limiter
WHEN: the webhook location is read
THEN: it allows exactly those four sources and denies all others, accepts POST only, caps the body at 8 KB, throttles with a 429 and keeps every proxy timeout within two seconds

AC-003:
GIVEN: an edge stamp that must equal the application's verification value
WHEN: the tracked template and the compose entrypoint are read
THEN: the template carries only a render placeholder with no credential, and the container substitutes the value from the host `.env` into a tmpfs file, so no secret is committed or written to host disk

AC-004:
GIVEN: TradingView documents a client certificate but publishes no CA or public key to verify it against
WHEN: the vhost and its log format are read
THEN: the certificate is requested for observation only, never treated as authentication, no invented trust anchor is configured, and the webhook log records no request body, headers or query string

AC-005:
GIVEN: an existing production stack with a read-only container filesystem and a single approved application secret file
WHEN: the compose service definition is read
THEN: 443 is published alongside 8443, the application stays loopback-only, the render target is tmpfs, the template is a read-only mount, the edge receives no `env_file` and no body token, and an absent or non-URL-safe value skips the render while nginx still starts

AC-006:
GIVEN: an application that accepts the ingress only from a configured peer address
WHEN: both ends of the webhook path are read
THEN: the edge and the application carry pinned addresses on a dedicated compose network, the vhost proxies to the pinned application address, and the default network is otherwise unchanged

AC-007:
GIVEN: an ARCH-5 protected dashboard configuration mounted read-only into the edge
WHEN: the dashboard file and the compose mount are read
THEN: the dashboard keeps its 8443 listener, certificate and locations, gains exactly one additive wildcard include, cannot serve the webhook route, and remains the only tracked nginx mount with no generated override

AC-008:
GIVEN: a verification header declared by the application
WHEN: the edge and the application are compared
THEN: the header name and the route path match exactly, so the edge cannot drift from the application contract

EXPLICITLY OUT OF SCOPE:
- Provisioning a staging host, a second VM, another proxy or any new service; the existing edge container is the only deployment target.
- Enabling Tradevisor ingestion, evaluation, Telegram delivery or any production flag; this increment ships configuration and tests only.
- Deploying, restarting or reloading production services, and any change to cloud firewall rules or host secrets.
- Exchange order placement, learning activation, PAPER_V2, Committee authority and canonical-writer changes.
- Implementing TradingView client-certificate chain verification, which is impossible without a published trust anchor.
- G-04 trusted-4H integration, which is preserved rather than relaxed, and G-05 load or soak evidence, which this increment does not produce.

FROZEN BOUNDARIES:
- The dashboard listener on 8443, its certificate paths and its existing locations are unchanged; exactly one additive wildcard include is permitted.
- `dashboard-sidecar.conf` remains the single tracked nginx mount, read-only, and is never rewritten on the host (ARCH-5).
- The application port stays `127.0.0.1:8000` and is never published publicly.
- The three authentication checks (source allowlist, edge stamp, body token) and the 404-on-mismatch behaviour stay exactly as merged.
- No secret value, and no value derived from one, may appear in the repository.
- Fixed risk sizing, minimum reward-to-risk, position verification and execution safeguards are untouched.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_tradevisor_production_edge_v1.py::test_ac_001_the_webhook_vhost_listens_on_443_with_a_modern_floor
AC-001 -> tests/test_tradevisor_production_edge_v1.py::test_ac_001_the_vhost_serves_exactly_one_route_and_refuses_everything_else
AC-002 -> tests/test_tradevisor_production_edge_v1.py::test_ac_002_only_the_documented_tradingview_sources_are_allowed
AC-002 -> tests/test_tradevisor_production_edge_v1.py::test_ac_002_the_route_is_post_only_bounded_and_throttled_to_429
AC-003 -> tests/test_tradevisor_production_edge_v1.py::test_ac_003_the_edge_stamp_is_a_render_placeholder_and_not_a_secret
AC-003 -> tests/test_tradevisor_production_edge_v1.py::test_ac_003_rendering_substitutes_the_placeholder_completely
AC-004 -> tests/test_tradevisor_production_edge_v1.py::test_ac_004_the_client_certificate_is_requested_but_not_trusted
AC-004 -> tests/test_tradevisor_production_edge_v1.py::test_ac_004_the_webhook_log_carries_no_body_and_no_headers
AC-005 -> tests/test_tradevisor_production_edge_v1.py::test_ac_005_compose_publishes_443_and_preserves_the_existing_surface
AC-005 -> tests/test_tradevisor_production_edge_v1.py::test_ac_005_the_render_target_is_tmpfs_and_the_template_is_read_only
AC-005 -> tests/test_tradevisor_production_edge_v1.py::test_ac_005_the_application_secret_file_is_not_handed_to_the_edge
AC-005 -> tests/test_tradevisor_production_edge_v1.py::test_ac_005_an_unprovisioned_or_unsafe_value_leaves_the_vhost_dark
AC-005 -> tests/test_tradevisor_production_edge_v1.py::test_ac_005_the_edge_stamp_is_interpolated_from_the_host_env_file
AC-006 -> tests/test_tradevisor_production_edge_v1.py::test_ac_006_both_ends_of_the_webhook_path_have_pinned_addresses
AC-007 -> tests/test_tradevisor_production_edge_v1.py::test_ac_007_the_dashboard_server_cannot_serve_the_webhook_route
AC-007 -> tests/test_tradevisor_production_edge_v1.py::test_ac_007_the_tracked_dashboard_config_is_still_the_only_mount
AC-008 -> tests/test_tradevisor_production_edge_v1.py::test_ac_008_the_edge_stamp_header_matches_the_application_contract

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.template
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-PRODUCTION-EDGE-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.template
AC-002 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-003 -> OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.template
AC-003 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-003 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-004 -> OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.template
AC-004 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-005 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-005 -> OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.template
AC-005 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-006 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-006 -> OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.template
AC-006 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-007 -> OHM-Trade-Agent-v1/deploy/nginx/dashboard-sidecar.conf
AC-007 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-007 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py
AC-008 -> OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.template
AC-008 -> OHM-Trade-Agent-v1/tests/test_tradevisor_production_edge_v1.py

DEFERRED DISCOVERIES:
- Runtime validation of the rendered vhost (`nginx -t`) and container startup is not executable off-host: no Docker or nginx binary is available to this increment, so it remains an operator gate before traffic is enabled.
- Client-certificate chain verification stays unimplemented because TradingView publishes no CA or public key; subject observation is recorded as evidence only and is explicitly not authentication.
- Cloud firewall access for 443 from the four TradingView addresses and the production secret values are outside repository control and are operator steps.
- Genuine TradingView delivery (G-01) remains unproven until an owner-created alert is delivered; this increment proves configuration only.

UNAPPROVED SCOPE CHANGES:
NONE
