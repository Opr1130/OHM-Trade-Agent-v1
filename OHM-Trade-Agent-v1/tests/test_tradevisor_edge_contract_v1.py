"""ATDD: Tradevisor edge TLS and rate-limit contract (P2-1 / P2-2).

Increment: ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1

* P2-1 — with several virtual hosts sharing :443 the TLS policy is chosen per SNI
  host, and a client that sends no SNI (or an unknown one) is served by the
  *default* server's policy. The dedicated webhook edge must therefore be the
  default server, so no sibling vhost can silently weaken the handshake, and a
  conflicting configuration must fail deployment validation.
* P2-2 — the edge limits 30 requests/minute per TradingView source while the
  application limits 60 requests/minute per *immediate peer*, and that peer is
  always the single trusted proxy. The application limit is therefore an
  aggregate ceiling shared by all four providers; that behaviour, the 429/503
  distinction and the no-receipt-on-refusal rule are asserted here.

Nothing is deployed or activated. No limit is raised.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import tradevisor as routes
from app.core.config import Settings
from app.services import tradevisor_inbox as inbox

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1"
APP_ROOT = Path(__file__).resolve().parents[1]
NGINX = APP_ROOT / "deploy" / "nginx" / "tradevisor-webhook.conf.example"
NGINX_TEXT = NGINX.read_text(encoding="utf-8")
DOC = (APP_ROOT / "TRADEVISOR_V2.md").read_text(encoding="utf-8")

TOKEN = "T" * 43
EDGE_VALUE = "synthetic-edge-verification-value"
ROUTE = "/webhooks/tradevisor/v1"


def make_settings(**over):
    values = dict(
        _env_file=None,
        webhook_secret="x" * 12,
        opip_tradevisor_enabled=True,
        opip_tradevisor_webhook_token=TOKEN,
        opip_tradevisor_trusted_proxy_ips="testclient",
        opip_tradevisor_internal_verification_value=EDGE_VALUE,
    )
    values.update(over)
    return Settings(**values)


@pytest.fixture()
def edge(tmp_path, monkeypatch):
    resolved = make_settings()
    monkeypatch.setattr(routes, "get_settings", lambda: resolved)
    monkeypatch.setattr(inbox, "INBOX_FILE", tmp_path / "inbox.json")
    monkeypatch.setattr(inbox, "LOCK_FILE", tmp_path / ".inbox.lock")
    monkeypatch.setattr(inbox, "get_settings", lambda: resolved)
    routes.reset_rate_limiter_for_tests()
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def bar_times():
    now = datetime.now(timezone.utc)
    opened = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    return opened, now - timedelta(seconds=1)


def payload(**over):
    opened, triggered = bar_times()
    body = {
        "schema_version": "tradevisor-ingress-v1",
        "source": "TRADEVISOR",
        "indicator": "TRADEVISOR_V2",
        "signal": "BUY",
        "symbol": "BTCUSD",
        "exchange": "COINBASE",
        "timeframe": "60",
        "signal_price": 67000.5,
        "bar_opened_at": opened.isoformat().replace("+00:00", "Z"),
        "triggered_at": triggered.isoformat().replace("+00:00", "Z"),
        "alert_config_version": "tradevisor-1h-buy",
        "ingest_token": TOKEN,
    }
    body.update(over)
    return body


def body_bytes(**over) -> bytes:
    return json.dumps(payload(**over)).encode("utf-8")


def headers() -> dict:
    return {routes.TRADEVISOR_VERIFICATION_HEADER: EDGE_VALUE}


def rows() -> list[dict]:
    return inbox.iter_records() if inbox.INBOX_FILE.exists() else []


def _self_signed(tmp_path):
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    subprocess.run(
        [
            shutil.which("openssl"), "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", "/CN=tv-staging.opip.io",
        ],
        check=True,
        capture_output=True,
    )
    return cert, key


def _render(cert, key, *, port: int = 38443) -> str:
    return (
        NGINX_TEXT.replace("<WEBHOOK_HOST>", "tv-staging.opip.io")
        .replace("<EDGE_VERIFICATION_VALUE>", EDGE_VALUE)
        .replace("/etc/letsencrypt/live/tv-staging.opip.io/fullchain.pem", cert.as_posix())
        .replace("/etc/letsencrypt/live/tv-staging.opip.io/privkey.pem", key.as_posix())
        .replace("listen 443 ssl", f"listen {port} ssl")
    )


def _nginx_conf(tmp_path, rendered: str) -> Path:
    conf = tmp_path / "nginx.conf"
    conf.write_text(
        "events {}\nhttp {\n    access_log off;\n" + rendered + "\n}\n", encoding="utf-8"
    )
    return conf


def _nginx_t(tmp_path, rendered: str):
    conf = _nginx_conf(tmp_path, rendered)
    return subprocess.run(
        [
            shutil.which("nginx"), "-t",
            "-p", str(tmp_path).replace("\\", "/") + "/",
            "-c", str(conf).replace("\\", "/"),
            "-g", "error_log stderr; pid nginx.pid;",
        ],
        capture_output=True,
        text=True,
    )


# AC-001 ----------------------------------------------------------------------
def test_ac_001_the_dedicated_edge_is_the_default_server_so_no_sni_falls_elsewhere():
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-001: no-SNI handshakes use this policy."""
    listen = [
        line.strip()
        for line in NGINX_TEXT.splitlines()
        if line.strip().startswith("listen ")
    ]
    assert listen == ["listen 443 ssl default_server;"]


def test_ac_001_the_tls_floor_is_declared_at_server_scope_and_never_weakened():
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-001: effective minimum is TLS 1.2."""
    server_block = NGINX_TEXT.split("server {", 1)[1]
    head = server_block.split("location", 1)[0]
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in head
    for weaker in ("TLSv1 ", "TLSv1.1", "SSLv3", "SSLv2", "ssl_protocols TLSv1;"):
        assert weaker not in NGINX_TEXT


def test_ac_001_the_multi_vhost_reasoning_is_documented():
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-001: the SNI/default-server rule is stated."""
    assert "default_server" in NGINX_TEXT
    assert "SNI" in NGINX_TEXT
    assert "default server" in DOC.lower()


# AC-002 ----------------------------------------------------------------------
def test_ac_002_a_competing_default_server_must_fail_validation():
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-002: a conflicting vhost is refused."""
    directives = [
        line.strip()
        for line in NGINX_TEXT.splitlines()
        if line.strip().startswith("listen ") and "default_server" in line
    ]
    # Exactly one default-server declaration, so a second claimant is a
    # deployment error rather than a silent policy downgrade.
    assert directives == ["listen 443 ssl default_server;"]
    assert NGINX_TEXT.count("server {") == 1
    assert "default" in NGINX_TEXT and "nginx -t" in NGINX_TEXT
    assert "return 404;" in NGINX_TEXT


@pytest.mark.skipif(
    shutil.which("nginx") is None or shutil.which("openssl") is None,
    reason="nginx/openssl unavailable: a real syntax check cannot run here",
)
def test_ac_002_nginx_refuses_two_default_servers_on_one_port(tmp_path):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-002: real nginx rejects the conflict."""
    cert, key = _self_signed(tmp_path)
    rendered = _render(cert, key)
    hostile = (
        "server {\n"
        "    listen 38443 ssl default_server;\n"
        "    server_name other.opip.io;\n"
        "    ssl_protocols TLSv1 TLSv1.1;\n"
        f"    ssl_certificate {cert.as_posix()};\n"
        f"    ssl_certificate_key {key.as_posix()};\n"
        "    location / { return 404; }\n"
        "}\n"
    )
    completed = _nginx_t(tmp_path, rendered + hostile)
    assert completed.returncode != 0
    assert "duplicate default server" in completed.stderr.lower()



@pytest.mark.skipif(
    not all(shutil.which(tool) for tool in ("nginx", "openssl"))
    or __import__("os").environ.get("OPIP_NGINX_HANDSHAKE_TEST") != "1",
    reason="opt-in TLS handshake test (needs nginx+openssl and OPIP_NGINX_HANDSHAKE_TEST=1)",
)
def test_ac_001_a_real_handshake_refuses_tls11_and_accepts_tls12(tmp_path):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-001: the negotiated floor is really 1.2.

    Opt-in because it starts a real nginx process on a loopback port. It is the
    only test that proves the *negotiated* protocol version; the syntax test and
    the structural assertions cannot.
    """
    import time

    cert, key = _self_signed(tmp_path)
    rendered = _render(cert, key, port=38443)
    conf = _nginx_conf(tmp_path, rendered)
    nginx = shutil.which("nginx")
    process = subprocess.Popen(
        [
            nginx, "-p", str(tmp_path).replace("\\", "/") + "/",
            "-c", str(conf).replace("\\", "/"),
            "-g", "error_log stderr; pid nginx.pid; daemon off;",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            probe = _s_client("127.0.0.1", 38443, "-tls1_2")
            if probe.returncode == 0:
                break
            time.sleep(0.5)
        tls12 = _s_client("127.0.0.1", 38443, "-tls1_2")
        assert tls12.returncode == 0, tls12.stderr
        tls11 = _s_client("127.0.0.1", 38443, "-tls1_1")
        assert tls11.returncode != 0
        assert "handshake" in (tls11.stderr + tls11.stdout).lower() or tls11.returncode != 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive teardown
            process.kill()


def _s_client(host: str, port: int, flag: str):
    return subprocess.run(
        [shutil.which("openssl"), "s_client", "-connect", f"{host}:{port}", flag],
        input="\n",
        capture_output=True,
        text=True,
        timeout=20,
    )


# AC-003 ----------------------------------------------------------------------
def test_ac_003_the_aggregate_rate_limit_contract_is_documented():
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-003: the aggregate ceiling is stated."""
    assert "Aggregate rate-limit contract" in DOC
    for phrase in (
        "30 requests per minute per TradingView source",
        "60 requests per minute per immediate peer",
        "aggregate",
        "429",
        "503",
    ):
        assert phrase in DOC, phrase
    # The application limit must not be presented as a per-source budget.
    assert "per-source application budget" not in DOC



# AC-004 ----------------------------------------------------------------------
def test_ac_004_the_aggregate_ceiling_is_59_60_accepted_and_61_refused(edge):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-004: 60 per peer, shared by all sources."""
    # The immediate peer is always the single trusted proxy, so the four
    # TradingView sources share one bucket: 60 accepted, the 61st refused.
    routes.reset_rate_limiter_for_tests()
    codes = [edge.post(ROUTE, content=body_bytes(), headers=headers()).status_code for _ in range(61)]
    assert codes[:60] == [202] * 60
    assert codes[60] == 429
    assert len(rows()) == 1  # every request deduplicates to the same bar


def test_ac_004_multiple_sources_share_the_single_peer_bucket(edge):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-004: distinct sources do not get separate budgets."""
    routes.reset_rate_limiter_for_tests()
    codes = []
    for index in range(4):
        codes.extend(
            edge.post(
                ROUTE,
                content=body_bytes(),
                headers={**headers(), "x-forwarded-for": f"52.89.214.{index}"},
            ).status_code
            for _ in range(16)
        )
    assert codes.count(202) == 60
    assert codes[60:] == [429] * 4


def test_ac_004_throttling_is_429_and_capacity_is_503(edge):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-004: the two refusals stay distinguishable."""
    # Capacity: fill the durable inbox to its configured limit (the schema floor
    # is 100 events) so the next genuinely new alert is refused with 503.
    full = make_settings(opip_tradevisor_max_inbox_events=100)
    inbox.get_settings = lambda: full
    registry = inbox.load_json(inbox.INBOX_FILE) or inbox._empty_registry()
    for index in range(100):
        registry["events"][f"capacity-{index}"] = {"status": inbox.STATUS_QUEUED}
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    routes.reset_rate_limiter_for_tests()
    capacity = edge.post(ROUTE, content=body_bytes(), headers=headers())
    assert capacity.status_code == 503
    assert capacity.status_code != 429
    assert [row for row in rows() if row.get("dedup_id")] == []

    # And throttling is 429, never 503, on the same route.
    inbox.INBOX_FILE.unlink()
    routes.reset_rate_limiter_for_tests()
    codes = [
        edge.post(ROUTE, content=body_bytes(), headers=headers()).status_code
        for _ in range(61)
    ]
    assert codes[60] == 429
    assert 503 not in codes


def test_ac_004_a_refused_request_never_receives_a_durable_receipt(edge):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-004: refusals write no row."""
    assert edge.post(ROUTE, content=body_bytes(), headers=headers()).status_code == 202
    before = len(rows())

    # 404: missing edge stamp / wrong token / unknown peer path
    assert edge.post(ROUTE, content=body_bytes()).status_code == 404
    assert edge.post(
        ROUTE, content=body_bytes(ingest_token="wrong"), headers=headers()
    ).status_code == 404
    # 422: schema-valid but refused upstream (stale bar)
    stale = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat().replace("+00:00", "Z")
    assert edge.post(
        ROUTE, content=body_bytes(triggered_at=stale), headers=headers()
    ).status_code == 422
    # 429: throttled
    routes.reset_rate_limiter_for_tests()
    for _ in range(60):
        edge.post(ROUTE, content=body_bytes(), headers=headers())
    assert edge.post(ROUTE, content=body_bytes(), headers=headers()).status_code == 429

    assert len(rows()) == before


def test_ac_004_a_duplicate_delivery_is_acknowledged_once(edge):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-004: a duplicate is not a second receipt."""
    first = edge.post(ROUTE, content=body_bytes(), headers=headers())
    second = edge.post(ROUTE, content=body_bytes(), headers=headers())
    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    assert first.json()["dedup_id"] == second.json()["dedup_id"]
    assert len(rows()) == 1


def test_ac_004_a_spoofed_edge_header_cannot_bypass_admission(edge):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-004: the stamp is not a client credential."""
    # A trusted peer may not assert a *different* stamp value.
    assert edge.post(
        ROUTE,
        content=body_bytes(),
        headers={routes.TRADEVISOR_VERIFICATION_HEADER: "client-supplied"},
    ).status_code == 404
    # An unknown body token is refused even with the correct stamp.
    assert edge.post(
        ROUTE, content=body_bytes(ingest_token="X" * 43), headers=headers()
    ).status_code == 404
    assert rows() == []


# AC-005 ----------------------------------------------------------------------
def test_ac_005_the_application_limit_is_unchanged_by_this_increment():
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-005: no limit was raised."""
    settings = Settings(_env_file=None, webhook_secret="x" * 12)
    assert settings.opip_tradevisor_max_requests_per_minute == 60
    assert routes.RATE_LIMIT_WINDOW_SECONDS == 60.0

