"""ATDD: optional V2 edge overlay remains isolated from legacy trading hosts."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EDGE = ROOT / "deploy/nginx/dashboard-sidecar.conf"
V2 = ROOT / "deploy/control-center"


@pytest.mark.acceptance
def test_side_by_side_route_and_exact_api_allowlist():
    """ATDD-OPIP-CONTROL-CENTER-V2-v1/AC-005: V2 leaves legacy routing intact."""
    main = EDGE.read_text(encoding="utf-8")
    routes = (V2 / "nginx/v2.conf").read_text(encoding="utf-8")
    assert "location = /dashboard {" in main
    assert "include /etc/nginx/opip-control-center/*.conf;" in main
    assert "include /etc/nginx/tradevisor/*.conf;" in main
    assert "location = /dashboard/v2 {" in routes
    assert "location ^~ /dashboard/v2/ {" in routes
    assert "/dashboard/v2/index.html" in routes
    assert "Content-Security-Policy" in routes
    assert "X-Frame-Options" in routes
    for route in (
        "/api/opip/scoring-comparison",
        "/api/opip/zero-trade-explanation",
        "/api/opip/strategy-admin/draft",
        "/api/opip/strategy-admin/active",
        "/api/opip/strategy-admin/activate",
        "/api/opip/strategy-admin/rollback",
    ):
        assert f"location = {route} {{" in routes
    assert "limit_except GET PUT { deny all; }" in routes
    assert routes.count("limit_except POST { deny all; }") == 2
    assert "location /api/" not in routes
    assert "location = /webhooks" not in routes


@pytest.mark.acceptance
def test_overlay_is_opt_in_and_does_not_change_resource_or_exchange_authority():
    """ATDD-OPIP-CONTROL-CENTER-V2-v1/AC-005: opt-in build-only release input."""
    overlay = (V2 / "docker-compose.v2.yml").read_text(encoding="utf-8")
    dockerfile = (V2 / "Dockerfile.edge").read_text(encoding="utf-8")
    readme = (V2 / "README.md").read_text(encoding="utf-8")
    assert "ohm-dashboard-edge:" in overlay
    assert "OPIP_DEPLOYED_SHA:?" in overlay
    assert "deploy/control-center/Dockerfile.edge" in overlay
    assert "deploy/control-center/nginx:/etc/nginx/opip-control-center:ro" in overlay
    assert "FROM node:22-alpine" in dockerfile
    assert "FROM nginx:1.27-alpine" in dockerfile
    assert "npm ci --no-audit --no-fund" in dockerfile
    assert "npm run build" in dockerfile
    assert "never creates a new port" in readme.lower()
    for forbidden in ("KRAKEN_", "FUNDED_TRADING", "allow_funded_orders", "OPIP_F11"):
        assert forbidden not in overlay
    assert "ports:" not in overlay
    assert "networks:" not in overlay
    assert "mem_limit:" not in overlay
