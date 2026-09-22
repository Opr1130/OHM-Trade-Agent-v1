"""PR-B/C-4E.4: restore the Cockpit loopback publish on the internal analytics network.

The production failure this suite guards against
================================================
`cockpit-ready` built the image, verified the replica and started a healthy container,
then failed its final operator-reachability preflight with:

    cockpit container is healthy but NOT published on host loopback 127.0.0.1:8000
    container health does not imply operator reachability; the port must be published

The host evidence was unambiguous:

    docker port opip-cockpit                      -> empty
    docker inspect ... '{{json .NetworkSettings.Ports}}' -> {"8000/tcp":null}
    ss -ltnp | grep ':8000'                       -> empty
    curl http://127.0.0.1:8000/cockpit            -> connection refused (HTTP=000)

Root cause: the service correctly declared
`127.0.0.1:8000:8000`, but the container was attached ONLY to `opip-analytics`, which
is `internal: true`. An internal bridge supplies no gateway/forwarding path for the
requested host mapping, so Docker accepted the declaration and produced no mapping at
all. Shell-level and Compose-level correctness were both intact; the network topology
was the defect.

The fix keeps `opip-analytics` internal and adds a dedicated ordinary bridge
(`opip-cockpit-publish`) used only by the Cockpit, so Docker has a path to install the
loopback mapping.

This suite therefore has two layers:

* structural assertions on the real Compose artifact and the real bootstrap;
* an executed `docker compose` runtime test that reproduces the topology and proves a
  host-loopback mapping actually appears and is reachable. That second layer is the
  point: the defect was invisible to every source-text assertion.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO.parent

ANALYTICS_COMPOSE = REPO / "deploy/analytics/docker-compose.yml"
COCKPIT_COMPOSE = REPO / "deploy/analytics/docker-compose.cockpit.yml"
BOOTSTRAP = (REPO / "deploy/analytics/bootstrap-opip-data-platform.sh").read_text(
    encoding="utf-8"
)
COCKPIT_TEXT = COCKPIT_COMPOSE.read_text(encoding="utf-8")
ANALYTICS_TEXT = ANALYTICS_COMPOSE.read_text(encoding="utf-8")
README = (REPO / "deploy/analytics/README.md").read_text(encoding="utf-8")

PUBLISH_NETWORK = "opip-cockpit-publish"
ANALYTICS_NETWORK = "opip-analytics"

#: The three independent runtime proofs the preflight must keep.
PREFLIGHT_PROOFS = (
    "ss",  # a host loopback listener exists
    "bound on a public interface",  # and no public listener exists
    "docker port opip-cockpit",  # and the mapping belongs to this container
)

COCKPIT_VARS = (
    "OPIP_COCKPIT_SECRET",
    "OPIP_COCKPIT_BIND_ADDRESS",
    "OPIP_COCKPIT_HOST_PORT",
    "OPIP_COCKPIT_HTTP_PORT",
)

ANALYTICS_PLANE_VARS = (
    "OPIP_GRAFANA_ADMIN_USER",
    "OPIP_GRAFANA_ADMIN_PASSWORD",
    "OPIP_GRAFANA_DB_PASSWORD",
    "OPIP_POSTGRES_ADMIN_PASSWORD",
    "OPIP_SHIPPER_PASSWORD",
    "OPIP_ANALYTICS_ADMIN_DATABASE_URL",
    "OPIP_ANALYTICS_DATABASE_URL",
)


def _cockpit() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(COCKPIT_TEXT)["services"]["opip-cockpit"]


def _cockpit_networks() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(COCKPIT_TEXT)["networks"]


def _extract_function(text: str, name: str) -> str:
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _strip_comments(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


# ---------------------------------------------------------------------------
# 1-9: the topology, asserted on the real artifact
# ---------------------------------------------------------------------------


def test_1_analytics_network_remains_internal():
    """The analytics plane's isolation must not be weakened to fix the publish."""
    networks = _cockpit_networks()
    assert networks[ANALYTICS_NETWORK]["internal"] is True
    assert networks[ANALYTICS_NETWORK]["driver"] == "bridge"
    # The shared analytics file keeps the same definition, so the Cockpit file cannot
    # drift from it.
    shared = yaml.safe_load(ANALYTICS_TEXT)["networks"][ANALYTICS_NETWORK]
    assert shared["internal"] is True
    assert networks[ANALYTICS_NETWORK] == shared


def test_2_a_separate_cockpit_publish_network_exists():
    networks = _cockpit_networks()
    assert PUBLISH_NETWORK in networks, sorted(networks)
    assert PUBLISH_NETWORK != ANALYTICS_NETWORK


def test_3_the_publish_network_is_not_internal():
    """A non-internal bridge is what supplies the path Docker needs to publish."""
    publish = _cockpit_networks()[PUBLISH_NETWORK]
    assert "internal" not in publish or publish["internal"] is False
    assert publish.get("internal") is not True


def test_4_only_the_cockpit_attaches_to_the_publish_network():
    """No analytics-plane service may join the publish network."""
    # In the Cockpit file, only opip-cockpit exists and it is the only service on it.
    cockpit_file = yaml.safe_load(COCKPIT_TEXT)
    for name, service in cockpit_file["services"].items():
        attached = service.get("networks") or {}
        assert (PUBLISH_NETWORK in attached) == (name == "opip-cockpit"), name

    # The publish network is not declared in the shared analytics file at all, so no
    # PostgreSQL/Grafana service can join it.
    shared = yaml.safe_load(ANALYTICS_TEXT)
    assert PUBLISH_NETWORK not in (shared.get("networks") or {})
    for name, service in shared["services"].items():
        attached = service.get("networks") or {}
        assert PUBLISH_NETWORK not in attached, name


def test_5_the_cockpit_remains_attached_to_the_analytics_network():
    """It still needs the internal network; the publish network is additive."""
    attached = _cockpit()["networks"]
    assert list(attached) == [ANALYTICS_NETWORK, PUBLISH_NETWORK], list(attached)


def test_6_the_publish_network_uses_the_bridge_driver():
    assert _cockpit_networks()[PUBLISH_NETWORK]["driver"] == "bridge"


def test_7_the_publish_network_binds_host_ports_to_loopback_by_default():
    """Defense in depth: a forgotten explicit bind still cannot reach a public iface."""
    opts = _cockpit_networks()[PUBLISH_NETWORK]["driver_opts"]
    assert opts["com.docker.network.bridge.host_binding_ipv4"] == "127.0.0.1"


def test_7b_publish_network_disables_ip_masquerade():
    """Config-level fact only: masquerade is disabled on the publish network.

    Review finding (valid): this asserts a *configuration* fact, not egress denial.
    Disabling masquerade removes source-NAT for container-originated traffic leaving this
    bridge; it is not an egress firewall. The network is deliberately not `internal`, so
    Docker still installs a gateway and a default route, and host/direct-routing
    reachability is not denied.

    Hard egress denial is deliberately NOT implemented (see the README section and the
    Compose comment). The runtime test
    `test_runtime_publish_network_has_no_source_nat_and_no_egress_denial` proves the
    behavioural half that actually holds, and no test claims the half that does not.
    """
    opts = _cockpit_networks()[PUBLISH_NETWORK]["driver_opts"]
    assert opts["com.docker.network.bridge.enable_ip_masquerade"] == "false"


def test_7c_egress_claims_are_precise_and_not_overclaimed():
    """The security contract must not claim egress denial that is not implemented.

    Review finding (valid): an earlier revision said the option "removes that egress". A
    security claim the configuration cannot support is a defect in its own right, so this
    pins the precise statement on both the Compose surface and the README.
    """
    lowered = README.lower()
    # It must say what is removed...
    assert "nat-based internet egress" in lowered
    # ...and, in the same place, what is NOT.
    assert "gateway" in lowered
    assert "default route" in lowered
    assert "not firewall-denied" in lowered or "not firewall denied" in lowered
    assert "deliberately accepted" in lowered or "deliberate residual" in lowered
    assert "no firewall enforcement is installed" in lowered

    # The overclaiming phrasings must be absent from both surfaces.
    for surface, text in (("README", README), ("compose", COCKPIT_TEXT)):
        assert "removes that egress" not in text, surface
        assert "no credentials" not in text, surface

    # The Compose comment carries the same precision as the README.
    assert "NOT an egress firewall" in COCKPIT_TEXT
    assert "No firewall enforcement is installed, by design." in COCKPIT_TEXT
    assert "Residual capability, deliberately accepted" in COCKPIT_TEXT


def test_7d_credential_claims_name_the_one_secret_that_is_present():
    """The Cockpit does hold OPIP_COCKPIT_SECRET, so "no credentials" was wrong.

    Review finding (valid): what is true is the absence of trading, exchange and Telegram
    authority, plus the presence of exactly one dedicated read-only secret.
    """
    assert "no Kraken credentials" in README
    assert "no Telegram authority" in README
    assert "no trading `WEBHOOK_SECRET`" in README
    assert "only the dedicated read-only `OPIP_COCKPIT_SECRET` is present" in README

    # The claim matches the artifact: the secret really is provisioned and delivered.
    env_example = (REPO / "deploy/analytics/env.example").read_text(encoding="utf-8")
    assert "OPIP_COCKPIT_SECRET=" in env_example
    write_env = _extract_function(BOOTSTRAP, "write_cockpit_env_file")
    assert "OPIP_COCKPIT_SECRET" in write_env
    service = _cockpit()
    assert service["env_file"] == ["/etc/opip-cockpit.env"]


def test_8_service_level_port_mapping_is_explicitly_loopback():
    """driver_opts alone is not the guarantee; the service mapping is explicit."""
    ports = _cockpit()["ports"]
    assert ports == [
        "${OPIP_COCKPIT_BIND_ADDRESS:-127.0.0.1}:"
        "${OPIP_COCKPIT_HOST_PORT:-8000}:${OPIP_COCKPIT_HTTP_PORT:-8000}"
    ], ports
    assert "${OPIP_COCKPIT_BIND_ADDRESS:-127.0.0.1}" in ports[0]


def test_9_no_wildcard_or_public_bind_anywhere_in_the_cockpit_surface():
    code = _strip_comments(COCKPIT_TEXT)
    assert "0.0.0.0:" not in code
    assert "[::]" not in code
    assert "network_mode" not in code
    # The uvicorn container-interface bind is expected and is not a host exposure; the
    # publish is what must stay loopback.
    assert "127.0.0.1" in code
    # No subnet was pinned for the publish network, so it cannot collide or be guessed.
    publish = _cockpit_networks()[PUBLISH_NETWORK]
    assert "ipam" not in publish, publish


# ---------------------------------------------------------------------------
# 10: the preflight keeps all three independent proofs, plus health
# ---------------------------------------------------------------------------


def test_10_preflight_keeps_health_and_all_three_runtime_proofs():
    """The preflight correctly caught this defect; it must not be relaxed."""
    preflight = _extract_function(BOOTSTRAP, "cockpit_preflight")

    # Container health remains a separate prerequisite.
    assert ".State.Health" in preflight
    assert '"healthy"' in preflight
    assert "not 'healthy'" in preflight

    # Proof 1: a host loopback listener exists.
    assert re.search(r'\bss -ltn', preflight)
    assert "NOT published on host loopback" in preflight

    # Proof 2: nothing is bound on a public interface.
    assert "bound on a public interface" in preflight
    assert "0\\\\.0\\\\.0\\\\.0" in preflight

    # Proof 3: the mapping belongs to this container.
    assert "docker port opip-cockpit" in preflight
    assert "publishes no host port" in preflight
    assert "is not published on host loopback" in preflight

    # And still fails closed on a non-loopback configured bind.
    assert "OPIP_COCKPIT_BIND_ADDRESS must be host loopback" in preflight

    # No swallowed failures. Every `|| true` may only be a best-effort *capture* inside a
    # command substitution, whose emptiness the next explicit check then tests; it must
    # never wrap a decision. An earlier revision ended this test with `... or True`, which
    # made the assertion vacuous for every possible preflight.
    code = _strip_comments(preflight)
    total = code.count("|| true")
    captured = len(re.findall(r"\$\([^)]*\|\| true", code))
    assert total == captured, (
        "every `|| true` in the preflight must be a capture inside $( ), not a guard "
        f"around a decision: total={total} captured={captured}"
    )
    for line in code.splitlines():
        if "|| true" in line:
            assert "exit" not in line, f"`|| true` swallows a failing exit: {line.strip()}"


def test_10_preflight_still_runs_before_readiness_is_recorded():
    start = _extract_function(BOOTSTRAP, "cockpit_start")
    assert start.index("cockpit_wait_healthy") < start.index("cockpit_preflight")
    assert start.index("cockpit_preflight") < start.index("write_cockpit_state ")


def test_10_cockpit_start_does_not_pre_create_or_bypass_the_network_bind():
    """No host-side forwarding shim, no manual network connect, no preflight bypass."""
    for forbidden in (
        "docker network connect",
        "socat",
        "iptables",
        "network_mode",
        "systemctl start opip-cockpit",
    ):
        assert forbidden not in BOOTSTRAP, forbidden
        # Comments in the Compose file explain why these were rejected, so the check is
        # on the configuration itself.
        assert forbidden not in _strip_comments(COCKPIT_TEXT), forbidden


# ---------------------------------------------------------------------------
# 11-12: hardening and replica mount unchanged
# ---------------------------------------------------------------------------


def test_11_replica_mount_remains_read_only():
    volumes = _cockpit()["volumes"]
    assert volumes == [
        "/var/lib/opip-learning/canonical-replica:/app/canonical-replica:ro"
    ], volumes
    assert volumes[0].endswith(":ro")


def test_12_hardening_and_resource_limits_are_unchanged():
    service = _cockpit()
    assert service["read_only"] is True
    assert service["tmpfs"] == ["/tmp:rw,noexec,nosuid,size=32m"]
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["mem_limit"] == "256m"
    assert service["memswap_limit"] == "256m"
    assert service["cpus"] == "0.25"
    assert service["pids_limit"] == 128
    assert service["image"] == "opip-data-platform:${OPIP_DEPLOYED_SHA:-local}"
    assert service["env_file"] == ["/etc/opip-cockpit.env"]
    assert service["environment"] == {
        "OPIP_COCKPIT_HTTP_PORT": "${OPIP_COCKPIT_HTTP_PORT:-8000}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    assert service["restart"] == "unless-stopped"
    assert service["container_name"] == "opip-cockpit"
    assert service["healthcheck"]["interval"] == "30s"
    assert service["healthcheck"]["retries"] == 5
    # No trading, exchange or Telegram credential is *configured*. Comments explain that
    # these are absent, so this inspects the configuration rather than the prose.
    code = _strip_comments(COCKPIT_TEXT)
    for forbidden in ("WEBHOOK_SECRET", "KRAKEN", "TELEGRAM"):
        assert forbidden not in code, forbidden


# ---------------------------------------------------------------------------
# 13-14: stage isolation preserved
# ---------------------------------------------------------------------------


def test_13_cockpit_ready_performs_no_postgresql_or_grafana_work():
    guard = 'if [[ "$STAGE" == "$COCKPIT_STAGE" ]]; then'
    assert BOOTSTRAP.index(guard) < BOOTSTRAP.index("# PostgreSQL / Grafana plane")
    dispatch = BOOTSTRAP[
        BOOTSTRAP.index(guard) : BOOTSTRAP.index("# PostgreSQL / Grafana plane")
    ]
    for effect in (
        "opip-postgres",
        "opip-shipper",
        "opip-grafana",
        "admin_run",
        "validate_postgres_tls_key",
        "systemctl",
    ):
        assert effect not in dispatch, effect
    assert "write_state " not in dispatch


def test_14_reads_ready_semantics_are_unchanged():
    reads_ready = BOOTSTRAP[BOOTSTRAP.index('elif [[ "$STAGE" == "reads-ready" ]]') :]
    assert "7 * 86400" in reads_ready
    assert "shipper must soak for seven days" in reads_ready
    for gate in (
        "migrations migrate",
        "migrations sync-required-streams",
        "reconcile",
        "health --require-ready",
    ):
        assert gate in reads_ready
    assert "READS_READY_AT_UTC" in reads_ready
    assert "READS_READY_SHA" in reads_ready
    assert "COCKPIT_READY" not in _strip_comments(reads_ready)
    # It still starts the Cockpit without verifying the replica, so it publishes no
    # COCKPIT_READY_* (B/C-4E contract) and still reports Cockpit status.
    assert "cockpit_start " in reads_ready
    assert "unverified" in reads_ready
    assert "cockpit_compose ps" in reads_ready


def test_readme_documents_the_publish_network_rationale():
    assert PUBLISH_NETWORK in README
    lowered = README.lower()
    assert "internal" in lowered
    assert "host_binding_ipv4" in lowered or "loopback" in lowered


# ---------------------------------------------------------------------------
# Executed Docker layers
# ---------------------------------------------------------------------------

#: Images to probe with, in preference order, as (image, serve command). Each serves the
#: mounted directory so the response body can prove the answer came from our container.
_PROBE_IMAGES: tuple[tuple[str, list[str]], ...] = (
    ("busybox:1.37", ["httpd", "-f", "-p", "8080", "-h", "/srv"]),
    ("busybox:latest", ["httpd", "-f", "-p", "8080", "-h", "/srv"]),
    ("python:3.12-slim", ["python", "-m", "http.server", "8080", "--directory", "/srv"]),
)

#: A plain sentence, deliberately not credential-shaped: an assignment that is both named
#: like a credential and carries high entropy trips gitleaks' generic-api-key heuristic, and
#: widening the secret allowlist for a test constant would be the wrong trade.
_PROBE_MARKER = "opip cockpit publish probe marker"


def _docker(*args: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=check,
    )


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    return _docker("version", check=False, timeout=60).returncode == 0


def _resolve_probe_image() -> tuple[str, list[str]] | None:
    """First usable (image, command), preferring one already present locally."""
    override = os.environ.get("OPIP_DOCKER_PROBE_IMAGE", "").strip()
    candidates = (
        ((override, ["python", "-m", "http.server", "8080", "--directory", "/srv"]),)
        + _PROBE_IMAGES
        if override
        else _PROBE_IMAGES
    )
    for image, command in candidates:
        if _docker("image", "inspect", image, check=False, timeout=60).returncode == 0:
            return image, command
    for image, command in candidates:
        if _docker("pull", image, check=False, timeout=600).returncode == 0:
            return image, command
    return None


def _free_loopback_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _probe_container_id(compose_path: Path, project: str, *, service: str = "probe") -> str:
    """Resolve the container id through Compose, never by guessing its name.

    Guessing ``<project>-<service>-1`` is fragile, and a wrong name makes `docker port`
    print nothing to stdout, which is indistinguishable from a missing mapping. Compose
    labels are the authoritative handle.
    """
    listed = _docker(
        "compose", "-f", str(compose_path), "ps", "-q", service, check=False
    )
    if listed.returncode == 0 and listed.stdout.strip():
        return listed.stdout.split()[0]

    by_label = _docker(
        "ps",
        "-aq",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--filter",
        f"label=com.docker.compose.service={service}",
        check=False,
    )
    assert by_label.stdout.strip(), (
        f"could not resolve the {service} container: "
        f"compose ps -> {listed.stdout!r}/{listed.stderr!r}; "
        f"label filter -> {by_label.stdout!r}/{by_label.stderr!r}"
    )
    return by_label.stdout.split()[0]


def _http_marker_present(host: str, port: int, *, attempts: int = 40) -> tuple[bool, str]:
    """Fetch ``/`` and report whether the probe's marker came back.

    A bare TCP connect is not sufficient evidence: Docker's proxy can complete the
    handshake on the host port while nothing inside the container is serving, so a
    connect-only check can pass on a container that serves nothing. Requiring our own
    marker in the body proves the request reached this container's listener.
    """
    import urllib.error
    import urllib.request

    last = ""
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(f"http://{host}:{port}/", timeout=3) as response:
                body = response.read().decode("utf-8", "replace")
                if _PROBE_MARKER in body:
                    return True, body[:200]
                last = f"status={response.status} body={body[:120]!r}"
        except (urllib.error.URLError, OSError) as exc:
            last = repr(exc)
        time.sleep(0.5)
    return False, last


def _write_probe_server_root(directory: Path) -> Path:
    """The directory the probe serves, containing a uniquely identifiable index."""
    root = directory / "srv"
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        f"<html><body>{_PROBE_MARKER}</body></html>\n", encoding="utf-8"
    )
    return root


def _container_ports_report(container: str) -> str:
    """Everything needed to diagnose a port-mapping assertion from one CI run."""
    port = _docker("port", container, check=False)
    inspect = _docker(
        "inspect",
        container,
        "--format",
        "{{json .NetworkSettings.Ports}}",
        check=False,
    )
    networks = _docker(
        "inspect",
        container,
        "--format",
        "{{json .NetworkSettings.Networks}}",
        check=False,
    )
    ps = _docker("ps", "-a", "--no-trunc", check=False)
    return (
        f"\n  docker port rc={port.returncode} stdout={port.stdout!r} stderr={port.stderr!r}"
        f"\n  Ports    = {inspect.stdout.strip() or inspect.stderr.strip()!r}"
        f"\n  Networks = {networks.stdout.strip() or networks.stderr.strip()!r}"
        f"\n  docker ps -a:\n{ps.stdout}"
    )


# --- Egress claims: what is measured and what is not ---------------------------
#
# There is deliberately no test here asserting that the publish bridge *denies* outbound
# traffic, because no such denial is implemented. Two candidate designs were rejected:
#
#   * Measuring source NAT by having a peer report the address it observed requires the
#     peer to sit behind the host router on a different bridge, and Docker's own
#     inter-network isolation drops container-to-container traffic between distinct bridges.
#     Such a probe would therefore fail on Docker's isolation rather than on the property
#     under test, which is worse than not having it.
#   * Inspecting host packet-filter state (a MASQUERADE rule for this bridge's subnet)
#     requires root and a specific filter backend, and asserts host state rather than
#     container behaviour.
#
# What the executed tests do establish:
#
#   * the publish bridge is misconfigured-free for publishing: a real host-loopback
#     mapping exists and the served content is reachable through it;
#   * the mapping is loopback-only and no public mapping exists;
#   * masquerade is disabled on that bridge (configuration);
#   * the bridge still supplies a **default route**, measured from inside the container,
#     which is exactly the residual the README documents.
#
# Together those state the honest model: NAT-based Internet egress is removed, and the
# non-internal bridge was not turned into an egress firewall.


def _network_facts(container: str, suffix: str) -> tuple[str, str]:
    """(container_ip, gateway) on the container's network whose key ends with `suffix`."""
    settings = yaml.safe_load(
        _docker(
            "inspect",
            container,
            "--format",
            "{{json .NetworkSettings.Networks}}",
        ).stdout
    )
    keys = [k for k in settings if k.endswith(suffix)]
    assert keys, f"no network ending {suffix!r} on {container}: {list(settings)}"
    entry = settings[keys[0]]
    return str(entry.get("IPAddress", "")), str(entry.get("Gateway", ""))


def _parse_default_route(table: str) -> tuple[str, str]:
    """(iface, gateway) for the default route in a ``/proc/net/route`` table.

    Kept pure so the parsing is verified locally rather than only where Docker runs. The
    Gateway column is the little-endian hex form of the address.
    """
    for line in table.splitlines()[1:]:
        fields = line.split()
        if len(fields) >= 3 and fields[1] == "00000000":
            gateway_hex = fields[2]
            try:
                octets = bytes.fromhex(gateway_hex)[::-1]
            except ValueError:
                octets = b""
            gateway = (
                ".".join(str(b) for b in octets) if len(octets) == 4 else gateway_hex
            )
            return fields[0], gateway
    raise AssertionError(f"no default route found; table was:\n{table}")


def _default_route_iface_and_gateway(container: str) -> tuple[str, str]:
    """Read the container's default route from ``/proc/net/route``.

    ``/proc/net/route`` is always present, so this needs nothing installed in the image.
    """
    result = _docker("exec", container, "cat", "/proc/net/route", check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    return _parse_default_route(result.stdout)


def test_parse_default_route_handles_a_real_proc_net_route_table():
    """Locally verified: the parsing that the Docker probe depends on.

    Otherwise a parsing mistake would first appear in CI as a mysteriously failing
    egress claim.
    """
    sample = (
        "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n"
        "eth0\t0020A8C0\t00000000\t0001\t0\t0\t0\t00FFFFFF\t0\t0\t0\n"
    )
    assert _parse_default_route(sample) == ("eth0", "172.18.0.1")

    # A table with no default route must fail loudly rather than silently return junk.
    header_only = sample.splitlines()[0] + "\n"
    with pytest.raises(AssertionError):
        _parse_default_route(header_only)
    with pytest.raises(AssertionError):
        _parse_default_route("")



def _probe_compose_data(
    project: str,
    image: str,
    command: list[str],
    port: int,
    server_root: Path,
    *,
    with_publish_network: bool,
) -> dict:  # type: ignore[type-arg]
    """The probe topology as data.

    Built as a mapping and serialized by PyYAML rather than by string templating: a
    hand-rolled template has to re-solve YAML quoting, and a Windows path containing
    backslashes is silently invalid inside a double-quoted scalar.
    """
    internal = f"{project}-internal"
    publish = f"{project}-publish"
    networks: dict[str, dict] = {  # type: ignore[type-arg]
        internal: {"driver": "bridge", "internal": True}
    }
    attached = [internal]
    if with_publish_network:
        networks[publish] = {
            "driver": "bridge",
            "driver_opts": {
                "com.docker.network.bridge.host_binding_ipv4": "127.0.0.1",
                "com.docker.network.bridge.enable_ip_masquerade": "false",
            },
        }
        attached.append(publish)
    return {
        "name": project,
        "services": {
            "probe": {
                "image": image,
                "command": command,
                "ports": [f"127.0.0.1:{port}:8080"],
                "volumes": [f"{server_root}:/srv:ro"],
                "networks": {name: None for name in attached},
            }
        },
        "networks": networks,
    }


def _write_probe_compose(
    directory: Path,
    project: str,
    image: str,
    command: list[str],
    port: int,
    *,
    with_publish_network: bool,
) -> Path:
    data = _probe_compose_data(
        project,
        image,
        command,
        port,
        _write_probe_server_root(directory),
        with_publish_network=with_publish_network,
    )
    path = directory / "docker-compose.probe.yml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def _published_host_ips(container: str) -> list[tuple[str, str, str]]:
    """(host_ip, host_port, container_port) from the container's real port mapping."""
    raw = _docker(
        "inspect",
        container,
        "--format",
        "{{json .NetworkSettings.Ports}}",
        check=False,
    )
    if raw.returncode != 0:
        return []
    try:
        ports = yaml.safe_load(raw.stdout) or {}
    except yaml.YAMLError:
        return []
    mappings: list[tuple[str, str, str]] = []
    for container_port, bindings in ports.items():
        if not bindings:
            continue
        for binding in bindings:
            mappings.append(
                (
                    str(binding.get("HostIp", "")),
                    str(binding.get("HostPort", "")),
                    str(container_port),
                )
            )
    return mappings


requires_docker_compose = pytest.mark.skipif(
    not _docker_available(),
    reason="an executed `docker compose` runtime test requires a working Docker daemon",
)


def test_generated_probe_compose_is_valid_and_mirrors_the_cockpit_topology(tmp_path: Path):
    """The probe file itself is verified locally, not only where Docker exists.

    Otherwise a malformed generated file would first surface in CI, and a broken probe
    would look like a broken fix.
    """
    for with_publish, suffix in (
        (True, {"probe-project-internal", "probe-project-publish"}),
        (False, {"probe-project-internal"}),
    ):
        directory = tmp_path / ("both" if with_publish else "internal-only")
        directory.mkdir()
        compose = _write_probe_compose(
            directory,
            "probe-project",
            "busybox:1.37",
            ["httpd", "-f", "-p", "8080", "-h", "/srv"],
            43210,
            with_publish_network=with_publish,
        )
        rendered = yaml.safe_load(compose.read_text(encoding="utf-8"))
        assert rendered["name"] == "probe-project"
        service = rendered["services"]["probe"]
        assert set(service["networks"]) == suffix, service["networks"]
        # The publish syntax matches the Cockpit's exactly.
        assert service["ports"] == ["127.0.0.1:43210:8080"], service["ports"]
        # The served directory is mounted so the response body can be attributed.
        assert service["volumes"] == [
            f"{directory / 'srv'}:/srv:ro"
        ], service["volumes"]
        assert (directory / "srv" / "index.html").is_file()
        assert _PROBE_MARKER in (directory / "srv" / "index.html").read_text(
            encoding="utf-8"
        )

        # The internal network mirrors the analytics network; the publish network mirrors
        # the Cockpit publish network, including the loopback default.
        assert rendered["networks"]["probe-project-internal"]["internal"] is True
        if with_publish:
            publish = rendered["networks"]["probe-project-publish"]
            assert publish["driver"] == "bridge"
            assert publish.get("internal") is not True
            assert (
                publish["driver_opts"]["com.docker.network.bridge.host_binding_ipv4"]
                == "127.0.0.1"
            )
            # The probe mirrors the production network namespaces exactly, so a passing
            # probe is meaningful for the real file.
            cockpit_networks = _cockpit_networks()
            assert cockpit_networks[ANALYTICS_NETWORK]["internal"] is True
            assert cockpit_networks[PUBLISH_NETWORK]["driver"] == publish["driver"]
            assert (
                cockpit_networks[PUBLISH_NETWORK]["driver_opts"]
                == publish["driver_opts"]
            )


@requires_docker_compose
def test_runtime_two_network_topology_publishes_and_is_reachable(tmp_path: Path):
    """Executed proof of the fix on real Docker.

    Mirrors the Cockpit topology exactly: one ``internal: true`` bridge plus one ordinary
    bridge with the same ``host_binding_ipv4`` hardening and the same
    ``127.0.0.1:<port>:<port>`` publish syntax. Then asserts a real host mapping exists
    and the socket is actually reachable on host loopback.
    """
    probe = _resolve_probe_image()
    if probe is None:
        pytest.skip("no usable Docker image available for the runtime publish probe")
    image, command = probe

    # Allocate the port once so the project name and the published mapping cannot diverge.
    port = _free_loopback_port()
    project = f"opip-bc4e4-probe-{os.getpid()}-{port}"
    compose = _write_probe_compose(
        tmp_path, project, image, command, port, with_publish_network=True
    )
    try:
        up = _docker("compose", "-f", str(compose), "up", "-d", check=False, timeout=600)
        assert up.returncode == 0, up.stderr

        container = _probe_container_id(compose, project)

        def report() -> str:
            return _container_ports_report(container)

        # Definitive reachability: our own marker must come back from the served file, so
        # this cannot pass on a container that serves nothing.
        served, detail = _http_marker_present("127.0.0.1", port)
        assert served, (
            "the published port never served this container's content on host loopback; "
            f"last observation: {detail}{report()}"
        )

        # A real host mapping exists, with a concrete HostIp and HostPort.
        published = _docker("port", container, check=False)
        assert published.stdout.strip(), (
            f"docker port is empty: no host mapping was created.  {report()}"
        )
        assert f"127.0.0.1:{port}" in published.stdout, f"{published.stdout!r}{report()}"

        mappings = _published_host_ips(container)
        assert mappings, f"NetworkSettings.Ports has no real mapping (null){report()}"
        for host_ip, host_port, container_port in mappings:
            assert host_ip == "127.0.0.1", f"{mappings}{report()}"
            assert host_port == str(port), f"{mappings}{report()}"
            assert container_port == "8080/tcp", f"{mappings}{report()}"

        # And nothing was published on a public interface.
        assert "0.0.0.0" not in published.stdout, published.stdout
        assert not any(
            host_ip in {"0.0.0.0", "::", "[::]"} for host_ip, _, _ in mappings
        ), mappings

        # Both networks are attached. Compose prefixes project-scoped networks with the
        # project name, so match on the declared suffix rather than guessing the prefix.
        inspect_settings = yaml.safe_load(
            _docker(
                "inspect",
                container,
                "--format",
                "{{json .NetworkSettings.Networks}}",
            ).stdout
        )
        keys = list(inspect_settings)
        internal_keys = [k for k in keys if k.endswith("-internal")]
        publish_keys = [k for k in keys if k.endswith("-publish")]
        assert internal_keys, f"{keys}{report()}"
        assert publish_keys, f"{keys}{report()}"

        # The mechanism itself, observed: the internal bridge supplies no gateway, while
        # the ordinary publish bridge does. That gateway is the forwarding path Docker
        # needs in order to install the host mapping, which is why the internal-only
        # topology produced no mapping at all.
        internal_settings = inspect_settings[internal_keys[0]]
        publish_settings = inspect_settings[publish_keys[0]]
        assert not internal_settings.get("Gateway"), f"{internal_settings}{report()}"
        assert publish_settings.get("Gateway"), f"{publish_settings}{report()}"
    finally:
        _docker("compose", "-f", str(compose), "down", "-v", "--remove-orphans", check=False)


@requires_docker_compose
def test_runtime_publish_bridge_supplies_a_default_route(tmp_path: Path):
    """Measured: the residual is real, and it is stated rather than denied.

    The publish bridge is deliberately not `internal`, so Docker installs a gateway and a
    default route even with masquerade disabled. That is the residual capability the
    README documents. Reading the route table from inside the container turns that
    documented caveat into a measured fact, and it is the strongest egress claim this
    design can support:

    * masquerade is disabled (configuration, `test_7b_...`);
    * a default route still exists and points at this bridge's gateway (measured here).

    No test asserts that outbound traffic is denied, because nothing denies it.
    """
    probe = _resolve_probe_image()
    if probe is None:
        pytest.skip("no usable Docker image available for the default-route probe")
    image, command = probe

    port = _free_loopback_port()
    project = f"opip-bc4e4-route-{os.getpid()}-{port}"
    compose = _write_probe_compose(
        tmp_path, project, image, command, port, with_publish_network=True
    )
    try:
        up = _docker("compose", "-f", str(compose), "up", "-d", check=False, timeout=600)
        assert up.returncode == 0, up.stderr
        container = _probe_container_id(compose, project)

        _, gateway = _network_facts(container, "-publish")
        assert gateway, f"the publish bridge reported no gateway on {container}"

        iface, route_gateway = _default_route_iface_and_gateway(container)
        assert iface, "the default route has no interface"
        # The default route points at the bridge's own gateway, which is what Docker
        # installs for a non-internal network.
        assert route_gateway == gateway, (
            f"default route gateway {route_gateway!r} does not match the publish bridge "
            f"gateway {gateway!r}"
        )
    finally:
        _docker("compose", "-f", str(compose), "down", "-v", "--remove-orphans", check=False)


@requires_docker_compose
def test_runtime_internal_only_control_case(tmp_path: Path):
    """Control: an internal-only topology must not silently appear reachable.

    Some Docker engines suppress the host mapping for internal-only networks (the
    production defect); others install it anyway. This test therefore asserts the *safety*
    property unconditionally - nothing is exposed publicly - and only asserts the
    suppression itself when the engine exhibits it, skipping otherwise. CI must not depend
    on an engine-specific behaviour, but it also must not pretend the control passed a
    stronger claim than it did.
    """
    probe = _resolve_probe_image()
    if probe is None:
        pytest.skip("no usable Docker image available for the runtime publish probe")
    image, command = probe

    port = _free_loopback_port()
    project = f"opip-bc4e4-ctrl-{os.getpid()}-{port}"
    compose = _write_probe_compose(
        tmp_path, project, image, command, port, with_publish_network=False
    )
    try:
        up = _docker("compose", "-f", str(compose), "up", "-d", check=False, timeout=600)
        assert up.returncode == 0, up.stderr
        container = _probe_container_id(compose, project)
        time.sleep(3)

        mappings = _published_host_ips(container)
        published = _docker("port", container, check=False).stdout.strip()

        # Unconditional safety: never a public bind, whatever the engine does.
        assert not any(
            host_ip in {"0.0.0.0", "::", "[::]"} for host_ip, _, _ in mappings
        ), mappings
        assert "0.0.0.0" not in published, published

        if not mappings:
            # The engine exhibits the production defect: record it as such.
            assert published == "", published
            return
        pytest.skip(
            "this Docker engine installs the host mapping for internal-only networks, "
            "so the production suppression cannot be reproduced here; the fix's own "
            "positive test remains authoritative"
        )
    finally:
        _docker("compose", "-f", str(compose), "down", "-v", "--remove-orphans", check=False)


@requires_docker_compose
def test_rendered_cockpit_config_exposes_the_publish_network_as_non_internal(
    tmp_path: Path,
):
    """`docker compose config` on the REAL artifact: topology survives interpolation."""
    render_root = tmp_path / "render"
    render_root.mkdir()
    service_env = render_root / "opip-cockpit.env"
    service_env.write_text("OPIP_COCKPIT_SECRET=test-secret\n", encoding="utf-8")
    rewritten, substitutions = re.subn(
        r"^(\s*- )/[^\n]*\.env$",
        rf"\g<1>{service_env}",
        COCKPIT_TEXT,
        flags=re.MULTILINE,
    )
    assert substitutions == 1, substitutions
    staged = render_root / "docker-compose.cockpit.yml"
    staged.write_text(rewritten, encoding="utf-8")
    interpolation = render_root / "interpolation.env"
    interpolation.write_text("OPIP_COCKPIT_HTTP_PORT=8000\n", encoding="utf-8")

    environment = dict(os.environ)
    for name in ANALYTICS_PLANE_VARS + COCKPIT_VARS + ("OPIP_DEPLOYED_SHA",):
        environment.pop(name, None)

    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(interpolation),
            "-f",
            str(staged),
            "config",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    rendered = yaml.safe_load(result.stdout)

    networks = rendered["networks"]
    assert networks[ANALYTICS_NETWORK]["internal"] is True
    assert not networks[PUBLISH_NETWORK].get("internal"), networks[PUBLISH_NETWORK]
    assert networks[PUBLISH_NETWORK]["driver"] == "bridge"

    service = rendered["services"]["opip-cockpit"]
    attached = set(service["networks"])
    assert attached == {ANALYTICS_NETWORK, PUBLISH_NETWORK}, attached
    # The port mapping interpolated to an explicit loopback bind. `config` emits either
    # the canonical long form or the short string depending on the Compose version, so
    # both are accepted; only interpolation is under test here.
    mapping = service["ports"][0]
    if isinstance(mapping, dict):
        host_ip = str(mapping.get("host_ip", ""))
        published = str(mapping.get("published", ""))
        target = str(mapping.get("target", ""))
    else:
        host_ip, published, target = str(mapping).split(":")
    assert host_ip == "127.0.0.1", mapping
    assert published == "8000", mapping
    assert target == "8000", mapping
