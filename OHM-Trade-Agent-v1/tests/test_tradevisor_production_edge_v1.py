"""Production edge composition for the Tradevisor V2 webhook (port 443).

Static contract for putting the already-certified webhook virtual host onto the
*existing* production edge container.

* TradingView documents webhook ports 80/443 only, four source addresses and a
  3 second budget, so the vhost must be reachable on 443 and proxy nothing else.
* The edge stamp is a shared secret: rendered from the tracked template at
  container start, never committed, and an unprovisioned value leaves the vhost
  dark instead of breaking the dashboard.
* The dashboard on 8443 and its read-only tracked mount (ARCH-5) are untouched,
  and the dashboard server must not be able to serve the webhook route.
* The application accepts the ingress only from one pinned peer address, so both
  ends of the edge<->application network carry static addresses.

Nothing here claims runtime validation: nginx syntax and container behaviour are
validated on the host before traffic is enabled.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.acceptance

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "deploy" / "nginx" / "tradevisor-webhook.conf.template"
DASHBOARD = ROOT / "deploy" / "nginx" / "dashboard-sidecar.conf"
COMPOSE = ROOT / "docker-compose.yml"
ROUTE_SOURCE = ROOT / "app" / "api" / "tradevisor.py"

PLACEHOLDER = "__EDGE_VERIFICATION_VALUE__"
EDGE_IP = "172.31.240.2"
APP_IP = "172.31.240.3"
ROUTE = "/webhooks/tradevisor/v1"
TRADINGVIEW_IPS = ("52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code_lines(text: str) -> list[str]:
    """Directive lines only: comments stripped, blank lines dropped."""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def test_ac_001_the_webhook_vhost_listens_on_443_with_a_modern_floor():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-001: static contract for this increment."""
    lines = _code_lines(_text(TEMPLATE))
    listen = [line for line in lines if line.startswith("listen ")]
    assert listen == ["listen 443 ssl default_server;"]
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in lines
    joined = " ".join(lines)
    for weaker in ("TLSv1 ", "TLSv1.1", "SSLv3", "SSLv2", "ssl_protocols TLSv1;"):
        assert weaker not in joined


def test_ac_001_the_vhost_serves_exactly_one_route_and_refuses_everything_else():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-001: static contract for this increment."""
    lines = _code_lines(_text(TEMPLATE))
    assert f"location = {ROUTE} {{" in lines
    assert "location / {" in lines
    assert lines[lines.index("location / {") + 1] == "return 404;"


def test_ac_002_only_the_documented_tradingview_sources_are_allowed():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-002: static contract for this increment."""
    text = _text(TEMPLATE)
    allows = re.findall(r"^\s*allow\s+(\S+);", text, flags=re.MULTILINE)
    assert tuple(allows) == TRADINGVIEW_IPS
    assert re.search(r"^\s*deny all;", text, flags=re.MULTILINE)


def test_ac_002_the_route_is_post_only_bounded_and_throttled_to_429():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-002: static contract for this increment."""
    lines = _code_lines(_text(TEMPLATE))
    joined = " ".join(lines)
    assert "limit_except POST {" in joined
    assert "client_max_body_size 8k;" in lines
    assert "limit_req zone=tradevisor_webhook burst=20 nodelay;" in lines
    assert "limit_req_status 429;" in lines
    for name, value in re.findall(r"(proxy_\w+_timeout) (\d+s);", joined):
        assert int(value.rstrip("s")) <= 2, name


def test_ac_003_the_edge_stamp_is_a_render_placeholder_and_not_a_secret():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-003: static contract for this increment."""
    text = _text(TEMPLATE)
    assert f'proxy_set_header x-ohm-tradevisor-verification "{PLACEHOLDER}";' in text
    for forbidden in ("WEBHOOK_TOKEN", "ingest_token", "PRIVATE KEY"):
        assert forbidden not in text


def test_ac_003_rendering_substitutes_the_placeholder_completely():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-003: static contract for this increment."""
    rendered = _text(TEMPLATE).replace(PLACEHOLDER, "A" * 43)
    assert PLACEHOLDER not in rendered
    assert '"' + "A" * 43 + '"' in rendered
    assert rendered.count("{") == rendered.count("}")


def test_ac_004_the_client_certificate_is_requested_but_not_trusted():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-004: static contract for this increment."""
    text = _text(TEMPLATE)
    assert "ssl_verify_client optional_no_ca;" in text
    assert "no CA certificate or public key" in text
    assert "never treated as authentication" in text
    assert "ssl_verify_client on;" not in text
    assert "ssl_client_ca_certificate" not in text


def test_ac_004_the_webhook_log_carries_no_body_and_no_headers():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-004: static contract for this increment."""
    text = _text(TEMPLATE)
    assert "$request_body" not in text
    assert "$http_" not in text
    assert "$ssl_client_verify" in text and "$ssl_client_s_dn" in text
    assert "access_log /dev/stdout tradevisor_edge;" in text


def test_ac_005_compose_publishes_443_and_preserves_the_existing_surface():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-005: static contract for this increment."""
    text = _text(COMPOSE)
    assert '- "443:443"' in text
    assert '- "8443:8443"' in text
    assert '"127.0.0.1:8000:8000"' in text


def test_ac_005_the_render_target_is_tmpfs_and_the_template_is_read_only():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-005: static contract for this increment."""
    text = _text(COMPOSE)
    assert (
        "./deploy/nginx/tradevisor-webhook.conf.template:"
        "/etc/nginx/templates/tradevisor-webhook.conf.template:ro" in text
    )
    assert "- /etc/nginx/tradevisor" in text
    assert "read_only: true" in text


def test_ac_005_the_application_secret_file_is_not_handed_to_the_edge():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-005: static contract for this increment."""
    edge_block = _text(COMPOSE).split("ohm-dashboard-edge:", 1)[1].split(
        "ohm-trade-agent:", 1
    )[0]
    assert "env_file" not in edge_block
    assert "OPIP_TRADEVISOR_WEBHOOK_TOKEN" not in edge_block


def test_ac_005_an_unprovisioned_or_unsafe_value_leaves_the_vhost_dark():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-005: static contract for this increment."""
    edge_block = _text(COMPOSE).split("ohm-dashboard-edge:", 1)[1].split(
        "ohm-trade-agent:", 1
    )[0]
    assert '""|*[!A-Za-z0-9_-]*)' in edge_block
    assert 'rm -f "$out"' in edge_block
    assert "exec nginx -g 'daemon off;'" in edge_block
    assert "$${OPIP_TRADEVISOR_INTERNAL_VERIFICATION_VALUE:-}" in edge_block


def test_ac_005_the_edge_stamp_is_interpolated_from_the_host_env_file():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-005: static contract for this increment."""
    edge_block = _text(COMPOSE).split("ohm-dashboard-edge:", 1)[1].split(
        "ohm-trade-agent:", 1
    )[0]
    assert (
        "OPIP_TRADEVISOR_INTERNAL_VERIFICATION_VALUE: "
        "${OPIP_TRADEVISOR_INTERNAL_VERIFICATION_VALUE:-}" in edge_block
    )


def test_ac_006_both_ends_of_the_webhook_path_have_pinned_addresses():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-006: static contract for this increment."""
    text = _text(COMPOSE)
    template = _text(TEMPLATE)
    assert f"ipv4_address: {EDGE_IP}" in text
    assert f"ipv4_address: {APP_IP}" in text
    assert f"proxy_pass http://{APP_IP}:8000{ROUTE};" in template
    assert "subnet: 172.31.240.0/28" in text


def test_ac_007_the_dashboard_server_cannot_serve_the_webhook_route():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-007: static contract for this increment."""
    text = _text(DASHBOARD)
    assert ROUTE not in text
    assert "location = /dashboard {" in text
    assert "location = /api/analytics/summary {" in text
    assert "location / {" in text
    assert "include /etc/nginx/tradevisor/*.conf;" in text
    assert text.count("161-35-106-207.sslip.io/fullchain.pem") == 1
    assert "listen 8443 ssl;" in text


def test_ac_007_the_tracked_dashboard_config_is_still_the_only_mount():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-007: static contract for this increment."""
    text = _text(COMPOSE)
    assert "./deploy/nginx/dashboard-sidecar.conf:/etc/nginx/nginx.conf:ro" in text
    assert "nginx-mount.override.yml" not in text


def test_ac_008_the_edge_stamp_header_matches_the_application_contract():
    """ATDD-TRADEVISOR-PRODUCTION-EDGE-v1/AC-008: static contract for this increment."""
    source = _text(ROUTE_SOURCE)
    match = re.search(r'TRADEVISOR_VERIFICATION_HEADER = "([^"]+)"', source)
    assert match, "the application declares its verification header"
    assert match.group(1) == "x-ohm-tradevisor-verification"
    assert f"proxy_set_header {match.group(1)}" in _text(TEMPLATE)
    assert f'@router.post("{ROUTE}"' in source
