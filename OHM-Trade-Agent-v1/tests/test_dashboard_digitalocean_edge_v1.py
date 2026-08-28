from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
EDGE = ROOT / "deploy" / "nginx" / "dashboard-sidecar.conf"


def test_dashboard_edge_keeps_main_app_loopback_only():
    text = COMPOSE.read_text(encoding="utf-8")
    assert '"127.0.0.1:8000:8000"' in text
    assert '"8443:8443"' in text
    assert "/etc/letsencrypt:/etc/letsencrypt:ro" in text
    assert "read_only: true" in text
    assert "no-new-privileges:true" in text


def test_dashboard_edge_exposes_only_dashboard_and_read_only_analytics():
    text = EDGE.read_text(encoding="utf-8")
    assert "location = /dashboard {" in text
    assert "location = /api/analytics/summary {" in text
    assert "location = /api/analytics/intelligence {" in text
    assert "limit_except GET" in text
    assert "location / {" in text
    assert "return 404;" in text
    assert "/webhooks/tradingview/v2" not in text
    assert "/operator/" not in text
    assert "/order" not in text


def test_dashboard_edge_uses_existing_tls_certificate_and_no_embedded_secret():
    text = EDGE.read_text(encoding="utf-8")
    assert "161-35-106-207.sslip.io/fullchain.pem" in text
    assert "161-35-106-207.sslip.io/privkey.pem" in text
    assert "X-Webhook-Secret" not in text
    assert "WEBHOOK_SECRET" not in text
    assert "verification_token" not in text
