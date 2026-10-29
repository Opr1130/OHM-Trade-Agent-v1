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

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services.tradevisor_ingress import config_from_settings

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
    assert 'rm -f "$$out"' in edge_block
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


def test_ac_005_compose_preserves_edge_shell_variables_after_interpolation(tmp_path):
    """The Compose render must preserve shell variables used at edge startup."""
    edge_block = _text(COMPOSE).split("ohm-dashboard-edge:", 1)[1].split(
        "ohm-trade-agent:", 1
    )[0]
    entrypoint = edge_block.split("    entrypoint:", 1)[1]
    for name in ("value", "cert", "tpl", "out"):
        assert f"$${name}" in entrypoint, name
        assert f"${name}" not in entrypoint.replace(f"$${name}", ""), name

    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose CLI unavailable; source assertions passed")

    # Isolated Compose service: never read a host .env or emit a secret.
    mini = tmp_path / "compose.yml"
    mini.write_text(
        "services:\n  edge:\n    image: nginx:1.27-alpine\n    entrypoint:"
        + entrypoint,
        encoding="utf-8",
    )
    result = subprocess.run(
        [docker, "compose", "-f", str(mini), "config", "--format", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for name in ("value", "cert", "tpl", "out"):
        assert f'The "{name}" variable is not set' not in result.stderr
    rendered = json.loads(result.stdout)["services"]["edge"]["entrypoint"][-1]
    assert 'case "$value" in' in rendered
    assert 'if [ ! -s "$cert" ]; then' in rendered
    assert '"$tpl" > "$out"' in rendered


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


# --- Hostname and TLS identity (ATDD-TRADEVISOR-HOSTNAME-TLS-v1) -------------
HOSTNAME = "178-128-147-125.sslip.io"
#: Certbot reports this *certificate name* for the certificate that covers
#: HOSTNAME. It is the directory the managed material lives in; it is not a
#: served identity and it is never renamed (see ATDD-TRADEVISOR-CERTBOT-PATH-v1).
CERTBOT_NAME = "161-35-106-207.sslip.io"
CERTBOT_LIVE_DIR = f"/etc/letsencrypt/live/{CERTBOT_NAME}/"


def test_ac_001_the_webhook_vhost_names_the_trading_droplet():
    """ATDD-TRADEVISOR-HOSTNAME-TLS-v1/AC-001: the vhost names the droplet that serves it."""
    text = _text(TEMPLATE)
    server_names = re.findall(r"^\s*server_name\s+([^;]+);", text, flags=re.MULTILINE)
    assert server_names == [HOSTNAME]
    # The historical name resolves to 161.35.106.207, not to this droplet, so it
    # is never a served name. It may appear only as Certbot's directory name.
    assert f"server_name {CERTBOT_NAME}" not in text


def test_ac_001_the_certificate_path_matches_the_hostname():
    """ATDD-TRADEVISOR-HOSTNAME-TLS-v1/AC-001: certificate material belongs to that name."""
    text = _text(TEMPLATE)
    cert = re.search(r"ssl_certificate\s+(\S+);", text)
    key = re.search(r"ssl_certificate_key\s+(\S+);", text)
    assert cert is not None and key is not None
    directory = CERTBOT_LIVE_DIR
    assert cert.group(1) == directory + "fullchain.pem"
    assert key.group(1) == directory + "privkey.pem"


def test_ac_002_a_missing_certificate_leaves_the_vhost_dark():
    """ATDD-TRADEVISOR-HOSTNAME-TLS-v1/AC-002: absent certificate must not break the edge."""
    edge_block = _text(COMPOSE).split("ohm-dashboard-edge:", 1)[1].split(
        "ohm-trade-agent:", 1
    )[0]
    assert f"cert={CERTBOT_LIVE_DIR}fullchain.pem" in edge_block
    assert 'if [ ! -s "$$cert" ]; then' in edge_block
    assert "webhook vhost disabled" in edge_block
    # nginx still starts for the dashboard: the guard never exits early.
    assert "exec nginx -g 'daemon off;'" in edge_block


def test_ac_002_the_dashboard_certificate_and_listener_are_untouched():
    """ATDD-TRADEVISOR-HOSTNAME-TLS-v1/AC-002: dashboard traffic is unaffected by this change."""
    dashboard = _text(DASHBOARD)
    assert "listen 8443 ssl;" in dashboard
    assert f"{CERTBOT_NAME}/fullchain.pem" in dashboard
    assert HOSTNAME not in dashboard


# --- Certbot-managed certificate paths (ATDD-TRADEVISOR-CERTBOT-PATH-v1) -----
ACTIVATION = ROOT / "deploy" / "tradevisor" / "activate-tradevisor-advisory.sh"


def test_ac_001_the_edge_resolves_the_served_name_from_the_certbot_certificate_name():
    """ATDD-TRADEVISOR-CERTBOT-PATH-v1/AC-001: served identity and Certbot name are distinct."""
    text = _text(TEMPLATE)
    # A live directory named after the served host does not exist on the host;
    # pointing at it would stop nginx starting and take the dashboard with it.
    assert f"/etc/letsencrypt/live/{HOSTNAME}/" not in text
    assert f"ssl_certificate     {CERTBOT_LIVE_DIR}fullchain.pem;" in text
    assert f"ssl_certificate_key {CERTBOT_LIVE_DIR}privkey.pem;" in text
    assert f"server_name {HOSTNAME};" in text
    # The distinction is explained in place, so no operator is invited to rename
    # the Certbot directory or replace its managed symlinks.
    assert "Certbot" in text
    assert "never renamed" in text


def test_ac_002_the_render_gate_checks_one_certbot_path_and_stays_fail_closed():
    """ATDD-TRADEVISOR-CERTBOT-PATH-v1/AC-002: one path, checked, still fail-closed."""
    edge_block = _text(COMPOSE).split("ohm-dashboard-edge:", 1)[1].split(
        "ohm-trade-agent:", 1
    )[0]
    assert f"cert={CERTBOT_LIVE_DIR}fullchain.pem" in edge_block
    assert 'if [ ! -s "$$cert" ]; then' in edge_block
    assert "webhook vhost disabled" in edge_block
    assert "exec nginx -g 'daemon off;'" in edge_block
    # No stale live directory survives anywhere in the deployed contract.
    for path in (TEMPLATE, COMPOSE):
        assert f"/etc/letsencrypt/live/{HOSTNAME}" not in _text(path)


def test_ac_003_the_activation_script_is_failfast_bound_and_secret_free():
    """ATDD-TRADEVISOR-CERTBOT-PATH-v1/AC-003: bounded operator script, no secrets, no deploy bypass."""
    text = _text(ACTIVATION)
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -Eeuo pipefail" in text
    # Bound to the verified droplet, the Certbot name and an exact approved SHA.
    assert 'readonly PUBLIC_IP="178.128.147.125"' in text
    assert 'readonly PRIVATE_IP="10.116.0.3"' in text
    assert f'readonly CERTBOT_NAME="{CERTBOT_NAME}"' in text
    assert "--sha must be 40 lowercase hex characters" in text
    # No secret value is embedded: the token and stamp are read from .env.
    assert "OPIP_TRADEVISOR_WEBHOOK_TOKEN=" not in text
    assert "OPIP_TRADEVISOR_INTERNAL_VERIFICATION_VALUE=" not in text
    # The GitHub controller stays the only deployment path.
    code = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "ssh " not in code
    assert not re.search(r"(^|\s)(sudo\s+)?(/usr/local/sbin/)?ohm-deploy(\s|$)", code)
    assert "/deploy " not in code


def test_ac_003_the_activation_script_is_valid_bash():
    """ATDD-TRADEVISOR-CERTBOT-PATH-v1/AC-003: the operator script parses as bash before it is shipped."""
    bash = shutil.which("bash")
    if bash is None:  # Windows dev hosts have no bash; the Linux runner is the gate.
        pytest.skip("bash is not installed on this host")
    result = subprocess.run(
        [bash, "-n", str(ACTIVATION)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_ac_003_the_activation_script_orders_gates_before_enabling_ingress():
    """ATDD-TRADEVISOR-CERTBOT-PATH-v1/AC-003: gates first, one flip, recoverable."""
    text = _text(ACTIVATION)
    validate = text.index("validate_composed_nginx() {")
    edge = text.index("recreate_edge() {")
    enable = text.index("enable_ingress() {")
    assert validate < edge < enable
    main_body = text.split("\nmain() {", 1)[1]
    # rindex: the rollback branch calls final_verification earlier, so the
    # activation sequence is the last occurrence of each gate.
    order = [
        main_body.rindex(name)
        for name in (
            "verify_host_identity",
            "verify_env_file_and_credentials",
            "verify_release_and_recovery",
            "verify_certificate",
            "validate_composed_nginx",
            "recreate_edge",
            "enable_ingress",
            "verify_route_authenticated",
            "final_verification",
        )
    ]
    assert order == sorted(order)
    # One atomic flip of the master switch, and no earlier write to it.
    assert text.count('set_env_value "$ENABLE_KEY" "true"') == 1
    # Failure restores the pre-activation posture instead of leaving ingress lit.
    assert 'compose up -d --no-deps --force-recreate "$EDGE_SERVICE"' in text
    assert "recreated edge did not render the validated webhook vhost" in text
    assert "restore_pre_activation_posture" in text
    assert "trap 'on_error $LINENO' ERR" in text
    # The only authenticated probe is a refusal path that writes no durable row.
    assert "422" in text
    assert "no durable row" in text


def test_ac_004_the_documented_csv_formats_are_what_the_application_parses():
    """ATDD-TRADEVISOR-CERTBOT-PATH-v1/AC-004: operator formats come from the parser, not a guess."""
    settings = Settings(_env_file=None, webhook_secret="x" * 12)
    resolved = config_from_settings(settings)
    assert resolved.alert_configs == {
        "tradevisor-1h-buy": "BUY",
        "tradevisor-1h-sell": "SELL",
    }
    assert resolved.instruments[("COINBASE", "BTCUSD")] == ("BTC/USD", "XBTUSD")
    assert len(resolved.instruments) == 10
    assert resolved.interval_minutes == 60
    assert settings.opip_tradevisor_enabled is False
    assert settings.opip_tradevisor_observation_enabled is False

    # The grammar is CSV of the two documented row shapes, nothing else.
    overridden = Settings(
        _env_file=None,
        webhook_secret="x" * 12,
        opip_tradevisor_alert_configs="tradevisor-4h-buy=BUY",
        opip_tradevisor_instruments="COINBASE:ETHUSD=ETH/USD:ETHUSD",
    )
    narrowed = config_from_settings(overridden)
    assert narrowed.alert_configs == {"tradevisor-4h-buy": "BUY"}
    assert narrowed.instruments == {("COINBASE", "ETHUSD"): ("ETH/USD", "ETHUSD")}

