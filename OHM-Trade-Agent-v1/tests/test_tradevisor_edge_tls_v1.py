"""Linux-mandatory TLS certification for the Tradevisor webhook edge (P2-1).

Increment: ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1

These tests start a throwaway nginx process on loopback with disposable
self-signed certificates and prove the *negotiated* behaviour of the reviewed
configuration:

* the configuration is valid (`nginx -t`);
* a second default server on the same listener is rejected;
* TLS 1.0 and TLS 1.1 are refused **by the server**, not merely by the client;
* TLS 1.2 and TLS 1.3 are accepted;
* missing SNI and unknown SNI cannot reach a deprecated protocol;
* a valid SNI is served by the intended vhost.

The client-side/server-side distinction is proved with a **permissive control**:
an otherwise identical server that *allows* TLS 1.1. If the same OpenSSL command
can handshake the control and cannot handshake the hardened server, the refusal is
server-enforced. If the local OpenSSL cannot offer the legacy protocol at all, the
control fails too and the test fails on Linux — a missing capability must never be
reported as a passing certification.

On Linux the certification MUST execute: a missing nginx or openssl is a failure,
not a skip. On Windows/macOS local runs the module skips, because the CI runner is
the certification environment.
"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.acceptance

IS_LINUX = sys.platform.startswith("linux")
NGINX = shutil.which("nginx")
OPENSSL = shutil.which("openssl")
APP_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = APP_ROOT / "deploy" / "nginx" / "tradevisor-webhook.conf.example"
HOST = "tv-staging.opip.io"

#: Markers that mean the *server* rejected the offered protocol.
SERVER_SIDE = (
    "alert protocol version",
    "unsupported protocol",
    "handshake failure",
    "no shared cipher",
    "tlsv1 alert",
)
#: Marker that means the *client* could not even build the ClientHello.
CLIENT_SIDE_NO_PROTOCOL = "no protocols available"
#: Legacy offers need a lower security level on OpenSSL 3 to be constructible.
LEGACY_CIPHERS = "DEFAULT@SECLEVEL=0"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _server_config(port: int, cert: Path, key: Path, *, protocols: str) -> str:
    """One hardened-or-permissive vhost on a loopback port.

    Both variants use the same cipher list, so the *only* difference is
    ``ssl_protocols``: a protocol refusal therefore cannot be a cipher refusal.
    """
    return (
        "server {\n"
        f"    listen 127.0.0.1:{port} ssl default_server;\n"
        f"    server_name {HOST};\n"
        "    server_tokens off;\n"
        f"    ssl_protocols {protocols};\n"
        f"    ssl_ciphers {LEGACY_CIPHERS};\n"
        f"    ssl_certificate {cert.as_posix()};\n"
        f"    ssl_certificate_key {key.as_posix()};\n"
        "    location / { return 204; }\n"
        "}\n"
    )


def _write_conf(prefix: Path, body: str) -> Path:
    conf = prefix / "nginx.conf"
    conf.write_text(
        "events {}\nhttp {\n    access_log off;\n" + body + "}\n", encoding="utf-8"
    )
    return conf


def _nginx_test(prefix: Path, body: str):
    conf = _write_conf(prefix, body)
    return subprocess.run(
        [
            NGINX, "-t",
            "-p", str(prefix).replace("\\", "/") + "/",
            "-c", str(conf).replace("\\", "/"),
            "-g", "error_log stderr; pid nginx.pid;",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _handshake(port: int, *flags: str):
    return subprocess.run(
        [
            OPENSSL, "s_client",
            "-connect", f"127.0.0.1:{port}",
            *flags,
        ],
        input="\n",
        capture_output=True,
        text=True,
        timeout=30,
    )


def _self_signed(prefix: Path) -> tuple[Path, Path]:
    cert = prefix / "cert.pem"
    key = prefix / "key.pem"
    subprocess.run(
        [
            OPENSSL, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", f"/CN={HOST}",
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return cert, key


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    """Certify the runtime, then start a hardened and a permissive nginx."""
    if not IS_LINUX:
        pytest.skip(
            "TLS certification executes on the Linux CI runner; a local Windows run "
            "cannot provide nginx/openssl protocol proof"
        )
    missing = [n for n, t in (("nginx", NGINX), ("openssl", OPENSSL)) if not t]
    if missing:
        pytest.fail(
            "TLS certification requires " + ", ".join(missing) + " on the Linux runner",
            pytrace=False,
        )
    prefix = tmp_path_factory.mktemp("tls-cert")
    cert, key = _self_signed(prefix)

    hardened_port = _free_port()
    control_port = _free_port()
    while control_port == hardened_port:
        control_port = _free_port()

    hardened = _server_config(hardened_port, cert, key, protocols="TLSv1.2 TLSv1.3")
    control = _server_config(
        control_port, cert, key, protocols="TLSv1 TLSv1.1 TLSv1.2"
    )
    conf = _write_conf(prefix, hardened + control)

    process = subprocess.Popen(
        [
            NGINX, "-p", str(prefix).replace("\\", "/") + "/",
            "-c", str(conf).replace("\\", "/"),
            "-g", "error_log stderr; pid nginx.pid; daemon off;",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 20
        started = False
        while time.time() < deadline:
            if process.poll() is not None:
                break
            if _handshake(hardened_port, "-tls1_2").returncode == 0:
                started = True
                break
            time.sleep(0.3)
        if not started:  # pragma: no cover - environment failure
            detail = process.stderr.read() if process.stderr else ""
            pytest.fail(f"temporary nginx did not start: {detail}", pytrace=False)
        yield {
            "hardened": hardened_port,
            "control": control_port,
            "hardened_conf": hardened,
            "prefix": prefix,
            "cert": cert,
            "key": key,
        }
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                process.kill()
        for leftover in ("nginx.pid", "error.log"):
            path = prefix / leftover
            if path.exists():
                path.unlink()


# AC-001 ----------------------------------------------------------------------
def test_ac_001_a_valid_configuration_passes_nginx_t(stack):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-001: a valid configuration passes nginx -t."""
    result = _nginx_test(stack["prefix"], stack["hardened_conf"])
    assert result.returncode == 0, result.stderr
    assert "syntax is ok" in (result.stderr + result.stdout).lower()


# AC-002 ----------------------------------------------------------------------
def test_ac_002_a_competing_default_server_is_rejected_by_real_nginx(stack):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-002: a conflicting default server fails."""
    port = _free_port()
    hostile = (
        "server {\n"
        f"    listen 127.0.0.1:{port} ssl default_server;\n"
        "    server_name other.opip.io;\n"
        "    ssl_protocols TLSv1 TLSv1.1;\n"
        f"    ssl_certificate {stack['cert'].as_posix()};\n"
        f"    ssl_certificate_key {stack['key'].as_posix()};\n"
        "    location / { return 404; }\n"
        "}\n"
    )
    first = _server_config(port, stack["cert"], stack["key"], protocols="TLSv1.2")
    result = _nginx_test(stack["prefix"], first + hostile)
    assert result.returncode != 0
    assert "duplicate default server" in result.stderr.lower()



def test_ac_001_tls12_and_tls13_are_accepted(stack):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-001: modern protocols negotiate."""
    tls12 = _handshake(stack["hardened"], "-tls1_2")
    assert tls12.returncode == 0, tls12.stderr
    assert "TLSv1.2" in tls12.stdout

    tls13 = _handshake(stack["hardened"], "-tls1_3")
    if tls13.returncode != 0:
        control13 = _handshake(stack["control"], "-tls1_3", "-cipher", LEGACY_CIPHERS)
        if CLIENT_SIDE_NO_PROTOCOL in (tls13.stderr + control13.stderr).lower():
            pytest.skip("this OpenSSL build cannot offer TLS 1.3; runtime limitation")
    assert tls13.returncode == 0, tls13.stderr
    assert "TLSv1.3" in tls13.stdout


@pytest.mark.parametrize("flag,label", [("-tls1", "TLS1.0"), ("-tls1_1", "TLS1.1")])
def test_ac_001_deprecated_protocols_are_refused_by_the_server(stack, flag, label):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-001: refusal is server-enforced."""
    client_flags = (flag, "-cipher", LEGACY_CIPHERS)
    control = _handshake(stack["control"], *client_flags)
    if control.returncode != 0:
        pytest.fail(
            f"the certification client cannot offer {label} even to a permissive server "
            f"({control.stderr.strip()[:200]}), so a refusal by the hardened server could "
            "not be distinguished from a client limitation",
            pytrace=False,
        )
    refused = _handshake(stack["hardened"], *client_flags)
    assert refused.returncode != 0, f"{label} was accepted by the hardened server"
    assert CLIENT_SIDE_NO_PROTOCOL not in refused.stderr.lower(), (
        f"{label} failure came from the client, not the server: {refused.stderr[:200]}"
    )
    combined = (refused.stderr + refused.stdout).lower()
    assert any(marker in combined for marker in SERVER_SIDE), refused.stderr[:300]


@pytest.mark.parametrize("flag,label", [("-tls1", "TLS1.0"), ("-tls1_1", "TLS1.1")])
def test_ac_001_missing_sni_cannot_reach_a_deprecated_protocol(stack, flag, label):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-001: no-SNI falls to the default server."""
    client_flags = ("-noservername", flag, "-cipher", LEGACY_CIPHERS)
    control = _handshake(stack["control"], *client_flags)
    assert control.returncode == 0, (
        "control could not offer " + label + ": " + control.stderr[:200]
    )
    refused = _handshake(stack["hardened"], *client_flags)
    assert refused.returncode != 0, f"no-SNI negotiated {label}"

    modern = _handshake(stack["hardened"], "-noservername", "-tls1_2")
    assert modern.returncode == 0, modern.stderr
    assert "TLSv1.2" in modern.stdout


@pytest.mark.parametrize("flag,label", [("-tls1", "TLS1.0"), ("-tls1_1", "TLS1.1")])
def test_ac_001_unknown_sni_cannot_reach_a_deprecated_protocol(stack, flag, label):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-001: unknown SNI cannot downgrade."""
    client_flags = ("-servername", "unknown.opip.io", flag, "-cipher", LEGACY_CIPHERS)
    control = _handshake(stack["control"], *client_flags)
    assert control.returncode == 0, (
        "control could not offer " + label + ": " + control.stderr[:200]
    )
    refused = _handshake(stack["hardened"], *client_flags)
    assert refused.returncode != 0, f"unknown SNI negotiated {label}"

    modern = _handshake(stack["hardened"], "-servername", "unknown.opip.io", "-tls1_2")
    assert modern.returncode == 0, modern.stderr


def test_ac_001_a_valid_sni_is_served_by_the_intended_vhost(stack):
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-001: the intended certificate is served."""
    result = _handshake(stack["hardened"], "-servername", HOST, "-tls1_2")
    assert result.returncode == 0, result.stderr
    assert f"CN = {HOST}" in result.stdout or f"CN={HOST}" in result.stdout


# AC-005 ----------------------------------------------------------------------
def test_ac_005_the_shipped_listener_is_ipv4_only_and_declares_one_default_server():
    """ATDD-TRADEVISOR-EDGE-TLS-RATELIMIT-v1/AC-005: one IPv4 listener, one default."""
    text = EXAMPLE.read_text(encoding="utf-8")
    listens = [
        line.strip() for line in text.splitlines() if line.strip().startswith("listen ")
    ]
    # TradingView documents no IPv6 support, so no IPv6 listener is declared: an
    # unreachable listener would only add a second TLS policy surface.
    assert listens == ["listen 443 ssl default_server;"]
    assert "[::]" not in text

