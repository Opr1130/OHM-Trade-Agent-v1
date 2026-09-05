from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _shell_function(source: str, name: str) -> str:
    marker = f"{name}() {{"
    start = source.index(marker)
    end = source.index("\n}", start) + 2
    return source[start:end]


def test_grafana_runtime_env_is_isolated_from_platform_credentials():
    compose = (ROOT / "deploy" / "analytics" / "docker-compose.yml").read_text(encoding="utf-8")
    bootstrap = (
        ROOT / "deploy" / "analytics" / "bootstrap-opip-data-platform.sh"
    ).read_text(encoding="utf-8")

    grafana_block = compose.split("  opip-grafana:\n", 1)[1].split("\n  opip-shipper:\n", 1)[0]
    assert "/etc/opip-grafana.env" in grafana_block
    assert "/etc/opip-data-platform.env" not in grafana_block

    writer = _shell_function(bootstrap, "write_grafana_env_file")
    for required in (
        "OPIP_GRAFANA_ADMIN_USER",
        "OPIP_GRAFANA_ADMIN_PASSWORD",
        "OPIP_GRAFANA_DB_USER",
        "OPIP_GRAFANA_DB_PASSWORD",
        "OPIP_GRAFANA_DB_NAME",
        "OPIP_GRAFANA_DB_SSLMODE",
        "OPIP_GRAFANA_POSTGRES_HOST",
        "OPIP_GRAFANA_POSTGRES_PORT",
        "OPIP_GRAFANA_BIND_ADDRESS",
        "OPIP_GRAFANA_HOST_PORT",
        "OPIP_GRAFANA_HTTP_PORT",
        "OPIP_GRAFANA_DOMAIN",
        "OPIP_GRAFANA_ROOT_URL",
        "OPIP_GRAFANA_SERVE_FROM_SUB_PATH",
    ):
        assert required in writer
    assert "OPIP_POSTGRES_ADMIN_PASSWORD" not in writer
    assert "OPIP_SHIPPER_PASSWORD" not in writer
    assert 'chmod 0600 "$temporary"' in writer
    assert 'mv -f -- "$temporary" "$GRAFANA_ENV_FILE"' in writer


def test_grafana_state_directory_is_preprovisioned_for_uid_472():
    bootstrap = (
        ROOT / "deploy" / "analytics" / "bootstrap-opip-data-platform.sh"
    ).read_text(encoding="utf-8")

    assert 'install -d -o root -g root -m 0711 "$STATE_ROOT"' in bootstrap
    assert 'install -d -o 472 -g 472 -m 0750 "$STATE_ROOT/grafana"' in bootstrap
    assert bootstrap.index('install -d -o 472 -g 472 -m 0750 "$STATE_ROOT/grafana"') < bootstrap.index(
        "validate_postgres_tls_key"
    )


@pytest.mark.parametrize("mode", ["disable", "require", "verify-ca"])
def test_grafana_tls_mode_rejects_insecure_overrides(mode: str):
    bootstrap = (
        ROOT / "deploy" / "analytics" / "bootstrap-opip-data-platform.sh"
    ).read_text(encoding="utf-8")
    function = _shell_function(bootstrap, "require_grafana_verify_full")
    script = (
        f"{function}\n"
        f"OPIP_GRAFANA_DB_SSLMODE={shlex.quote(mode)}\n"
        "require_grafana_verify_full\n"
    )
    result = subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 78
    assert "must be exactly verify-full" in result.stderr


def test_grafana_tls_mode_accepts_only_verify_full():
    bootstrap = (
        ROOT / "deploy" / "analytics" / "bootstrap-opip-data-platform.sh"
    ).read_text(encoding="utf-8")
    function = _shell_function(bootstrap, "require_grafana_verify_full")
    result = subprocess.run(
        [
            "bash",
            "-c",
            f"{function}\nOPIP_GRAFANA_DB_SSLMODE=verify-full\nrequire_grafana_verify_full\n",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0


def test_grafana_hardening_is_documented_as_bootstrap_managed():
    readme = (ROOT / "deploy" / "grafana" / "README.md").read_text(encoding="utf-8")
    assert "/etc/opip-grafana.env" in readme
    assert "strict `OPIP_GRAFANA_*` allowlist" in readme
    assert "UID 472" in readme
    assert "rejects any weaker `OPIP_GRAFANA_DB_SSLMODE` value" in readme
    assert "docker compose --env-file /etc/opip-data-platform.env" in readme
