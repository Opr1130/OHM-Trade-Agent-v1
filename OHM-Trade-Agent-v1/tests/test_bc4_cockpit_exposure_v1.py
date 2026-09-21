"""B/C-4 Cockpit operator-exposure regression tests (ARB reachability blocker).

The blocker this suite guards against: the Cockpit container was bound to
*container* loopback and its port was never published, while the analytics network
is ``internal: true``. The container's own healthcheck therefore passed while nothing
on the host - and so no operator, and no host TLS reverse proxy - could reach it.

A passing container healthcheck is not operator reachability, and these tests keep
that distinction enforced. Requirements A-J from the ARB review are covered in order.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = REPO / "deploy" / "analytics" / "docker-compose.yml"
ENV_EXAMPLE = REPO / "deploy" / "analytics" / "env.example"
BOOTSTRAP = REPO / "deploy" / "analytics" / "bootstrap-opip-data-platform.sh"
README = REPO / "deploy" / "analytics" / "README.md"

COMPOSE_TEXT = COMPOSE.read_text(encoding="utf-8")
COMPOSE_DATA = yaml.safe_load(COMPOSE_TEXT)
BOOTSTRAP_TEXT = BOOTSTRAP.read_text(encoding="utf-8")
README_TEXT = README.read_text(encoding="utf-8")


#: One ``${VAR:-default}`` token. Parsing published ports by splitting on ``:`` is
#: wrong because the ``:-`` default itself contains a colon.
_PORT_PART = re.compile(r"\$\{[A-Z_]+:-[^}]*\}")


def _port_parts(mapping: str) -> list[str]:
    """The interpolated parts of a compose port mapping, order preserved."""
    return _PORT_PART.findall(str(mapping))


def _service(name: str = "opip-cockpit") -> dict:
    return COMPOSE_DATA["services"][name]


# ---------------------------------------------------------------------------
# A. Uvicorn binds the CONTAINER interface, not container loopback
# ---------------------------------------------------------------------------


def test_a_cockpit_uvicorn_binds_container_interface_not_container_loopback():
    """Container loopback is unreachable while the analytics network is internal.

    Binding ``127.0.0.1`` inside the container makes the service invisible to the
    host publish and therefore to the reverse proxy, even though the container
    healthcheck would still pass.
    """
    command = _service()["command"]
    assert command[0] == "uvicorn"
    assert command[1] == "app.api.cockpit_service:app"

    host_index = command.index("--host")
    bind = command[host_index + 1]
    assert bind == "0.0.0.0", f"cockpit uvicorn binds {bind!r}, not the container interface"
    assert "127.0.0.1" not in bind


def test_a_cockpit_port_is_parameterised_and_consistent():
    """The listen port and the published container port must not drift apart."""
    command = _service()["command"]
    port = command[command.index("--port") + 1]
    assert "OPIP_COCKPIT_HTTP_PORT" in port

    # The container-side of the publish must use the same variable.
    parts = _port_parts(_service()["ports"][0])
    assert len(parts) == 3, parts
    assert "OPIP_COCKPIT_HTTP_PORT" in parts[2], parts
    assert "OPIP_COCKPIT_HTTP_PORT" in _service()["environment"], _service()["environment"]


# ---------------------------------------------------------------------------
# B. Published ONLY through host loopback
# ---------------------------------------------------------------------------


def test_b_cockpit_port_is_published_to_host_loopback_only():
    """Exposure must mirror Grafana: loopback publish, TLS proxy in front."""
    mappings = _service()["ports"]
    assert len(mappings) == 1, mappings

    parts = _port_parts(mappings[0])
    assert len(parts) == 3, parts
    # host bind, host port, container port
    assert "OPIP_COCKPIT_BIND_ADDRESS:-127.0.0.1" in parts[0], parts
    assert "OPIP_COCKPIT_HOST_PORT" in parts[1], parts


def test_b_cockpit_bind_default_is_loopback_so_unset_cannot_widen_exposure():
    """An unset bind variable must fail closed to loopback, not to all interfaces."""
    parts = _port_parts(_service()["ports"][0])
    # The `:-` default is what protects an unset/empty variable.
    assert parts[0] == "${OPIP_COCKPIT_BIND_ADDRESS:-127.0.0.1}", parts
    assert "0.0.0.0" not in parts[0], parts


def test_b_cockpit_exposure_matches_the_grafana_pattern():
    """Reuse the existing exposure model rather than inventing another."""
    for name in ("opip-grafana", "opip-cockpit"):
        parts = _port_parts(_service(name)["ports"][0])
        assert len(parts) == 3, (name, parts)
        assert parts[0].endswith(":-127.0.0.1}"), (name, parts)


# ---------------------------------------------------------------------------
# C. No public 0.0.0.0 host binding anywhere
# ---------------------------------------------------------------------------


def test_c_no_service_publishes_a_port_on_all_interfaces():
    """Every published port must be host-scoped; none may bind 0.0.0.0."""
    offenders: list[str] = []
    for name, service in COMPOSE_DATA["services"].items():
        for mapping in service.get("ports", []) or []:
            parts = _port_parts(str(mapping))
            host_side = parts[0] if parts else str(mapping).split(":")[0]
            if "0.0.0.0" in host_side or host_side.strip() == "::":
                offenders.append(f"{name}: {mapping}")
    assert not offenders, f"public host bindings found: {offenders}"
    # And the literal anchor is absent from the file entirely.
    assert "0.0.0.0:" not in COMPOSE_TEXT


def test_c_cockpit_bind_address_env_example_is_loopback():
    assert "OPIP_COCKPIT_BIND_ADDRESS=127.0.0.1" in ENV_EXAMPLE.read_text(
        encoding="utf-8"
    )


def test_c_env_example_never_sets_a_public_cockpit_bind():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    for line in text.splitlines():
        if "OPIP_COCKPIT_BIND_ADDRESS" in line and not line.strip().startswith("#"):
            assert line.strip().endswith("127.0.0.1"), line


# ---------------------------------------------------------------------------
# D. Production trading app still mounts no cockpit router
# ---------------------------------------------------------------------------


def test_d_trading_app_still_has_no_cockpit_router():
    source = (REPO / "app" / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "app.api.cockpit" not in modules

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            names.update(
                getattr(argument, "id", "") for argument in node.args if argument
            )
    assert "cockpit_router" not in names


# ---------------------------------------------------------------------------
# E. Analytics service exposes only cockpit/read routes
# ---------------------------------------------------------------------------


def test_e_cockpit_service_exposes_only_cockpit_paths():
    from app.api import cockpit_service

    paths = set(cockpit_service.app.openapi().get("paths", {}) or {})
    assert paths == {
        "/cockpit",
        "/api/cockpit/overview",
        "/api/cockpit/trades",
        "/api/cockpit/trades/{paper_trade_id}",
    }, paths


# ---------------------------------------------------------------------------
# F. Cockpit still mounts the canonical replica read-only
# ---------------------------------------------------------------------------


def test_f_replica_is_mounted_read_only_at_the_expected_path():
    volumes = _service()["volumes"]
    replica = [v for v in volumes if "canonical-replica" in v]
    assert replica, volumes
    assert replica[0].endswith(":ro"), replica[0]
    assert "/app/canonical-replica" in replica[0]
    assert (
        _service()["environment"]["OPIP_CANONICAL_REPLICA_ROOT"]
        == "/app/canonical-replica"
    )


def test_f_cockpit_still_reads_through_the_lock_free_read_only_reader(tmp_path):
    from app.opip.canonical.writer import CanonicalWriter

    store = pathlib.Path(tmp_path) / "canonical.sqlite3"
    writer = CanonicalWriter(store)
    writer.close()

    reader = CanonicalWriter.for_reads(store)
    try:
        assert reader.is_read_only is True
        assert reader._store_lock is None  # noqa: SLF001 - lock-free by design
        with pytest.raises(Exception):
            reader._conn.execute("CREATE TABLE nope (a)")  # noqa: SLF001
    finally:
        reader.close()


# ---------------------------------------------------------------------------
# G. Authentication still required  /  H. Non-GET rejected
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch):
    """A client over the analytics-plane app with a known operator secret."""
    from fastapi.testclient import TestClient

    from app.api import cockpit, cockpit_service

    class _Settings:
        webhook_secret = "expected-secret"

    monkeypatch.setattr(cockpit, "get_settings", lambda: _Settings())
    return TestClient(cockpit_service.app)


def test_g_cockpit_api_requires_the_operator_secret(client):
    """An unauthenticated read must be rejected, not answered."""
    response = client.get("/api/cockpit/overview")
    assert response.status_code == 401


def test_g_cockpit_api_rejects_a_wrong_secret(client):
    response = client.get(
        "/api/cockpit/overview", headers={"x-webhook-secret": "wrong"}
    )
    assert response.status_code == 401


def test_g_cockpit_api_answers_with_the_correct_secret(client):
    """With the secret the route must serve, not 401.

    The replica is absent in this test environment, so the honest answer is an
    explicitly unavailable payload - which is exactly the fail-closed behaviour we
    want: reachable and authenticated, but never claiming data it cannot read.
    """
    response = client.get(
        "/api/cockpit/overview",
        headers={"x-webhook-secret": "expected-secret"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["trust"]["is_healthy"] is False
    assert payload["details"]


def test_h_cockpit_api_rejects_non_get_methods(client):
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)(
            "/api/cockpit/overview",
            headers={"x-webhook-secret": "expected-secret"},
        )
        assert response.status_code in {404, 405}, f"{method} returned {response.status_code}"


def test_h_cockpit_page_is_served_without_the_secret():
    """The page shell is public; the data behind it is not."""
    import tempfile

    from fastapi.testclient import TestClient

    from app.api import cockpit_service

    with TestClient(cockpit_service.app) as test_client:
        response = test_client.get("/cockpit")
    assert response.status_code == 200
    assert "PAPER MODE" in response.text


# ---------------------------------------------------------------------------
# I. Preflight distinguishes container health from host reachability
# ---------------------------------------------------------------------------


def test_i_bootstrap_proves_host_loopback_reachability_not_just_container_health():
    """Container health alone must never be treated as operator reachability."""
    assert "cockpit_preflight" in BOOTSTRAP_TEXT
    # Container health is checked...
    assert ".State.Health" in BOOTSTRAP_TEXT
    # ...and the host publish is proven separately at the socket layer.
    assert "ss -ltn" in BOOTSTRAP_TEXT
    assert "host loopback" in BOOTSTRAP_TEXT.lower()
    # The proof must not be taken at the container level.
    assert "does not imply operator reachability" in BOOTSTRAP_TEXT


def test_i_preflight_refuses_a_non_loopback_bind():
    """The raw HTTP service must fail closed if pointed at a public address."""
    assert "OPIP_COCKPIT_BIND_ADDRESS must be host loopback" in BOOTSTRAP_TEXT
    assert "case \"$bind\" in" in BOOTSTRAP_TEXT


def test_i_preflight_refuses_a_publicly_bound_port_at_runtime():
    """The no-public-bind rule must be enforced on the host, not only in compose."""
    assert "0\\\\.0\\\\.0\\\\.0" in BOOTSTRAP_TEXT or "0\\.0\\.0\\.0" in BOOTSTRAP_TEXT
    assert "bound on a public interface" in BOOTSTRAP_TEXT


def test_i_reachability_proof_uses_no_clear_text_protocol_literal():
    """The proof is socket-level, so it introduces no clear-text-protocol use.

    A first version issued an HTTP request to host loopback, which SonarCloud flagged
    as ``shell:S5332`` (clear-text protocol). The loopback hop is genuinely safe
    because TLS terminates at the host reverse proxy, but rather than suppress the
    finding or obscure the scheme, the reachability proof is taken where the actual
    contract lives: the published socket. Application behaviour is proven by the test
    suite, which drives the real ASGI app.
    """
    preflight = BOOTSTRAP_TEXT[
        BOOTSTRAP_TEXT.index("cockpit_preflight()") :
        BOOTSTRAP_TEXT.index("total_kb=")
    ]
    assert "http://" not in preflight
    assert "curl" not in preflight
    # No suppression directive was introduced.
    assert "NOSONAR" not in BOOTSTRAP_TEXT


def test_i_app_behaviour_is_proven_by_the_test_suite():
    """The split must be explicit: the preflight states it does not test the app."""
    assert "cockpit_app_behaviour=verified_by_test_suite" in BOOTSTRAP_TEXT
    # And the suite really does drive the app over the read-only surface.
    suite = pathlib.Path(__file__).read_text(encoding="utf-8")
    assert 'client.get("/api/cockpit/overview")' in suite


def test_i_bootstrap_declares_the_cockpit_exposure_contract():
    """The preflight must state its own exposure assumptions to the operator."""
    assert "cockpit_exposure=host-loopback" in BOOTSTRAP_TEXT
    assert "cockpit_requires_tls_reverse_proxy=true" in BOOTSTRAP_TEXT


def test_i_preflight_runs_before_the_cockpit_is_declared_ready():
    """Ready state must not be written before reachability is proven."""
    preflight_at = BOOTSTRAP_TEXT.index("cockpit_preflight\n")
    ready_at = BOOTSTRAP_TEXT.index("COCKPIT_READY_AT_UTC")
    assert preflight_at < ready_at


def test_i_preflight_is_invoked_from_the_reads_ready_stage():
    ready_stage = BOOTSTRAP_TEXT[
        BOOTSTRAP_TEXT.index('require_stage SHIPPER_STARTED_AT_UTC') :
    ]
    assert "cockpit_preflight" in ready_stage
    assert "compose up -d opip-cockpit" in ready_stage


def test_i_bootstrap_syntax_is_validated_in_ci():
    """The preflight is bash, so CI must syntax-check the file that carries it.

    Added to the existing validation step rather than a new job, and asserted here so
    the guarantee cannot silently regress.
    """
    workflow = (
        REPO.parent / ".github" / "workflows" / "pytest.yml"
    ).read_text(encoding="utf-8")
    assert "bash -n deploy/analytics/bootstrap-opip-data-platform.sh" in workflow


def test_i_container_healthcheck_is_documented_as_insufficient():
    """The misleading-inference risk must be documented, not just coded around."""
    assert "container health" in README_TEXT.lower()
    assert "not" in README_TEXT.lower()
    # And the bootstrap itself explains it.
    assert "does not imply operator reachability" in BOOTSTRAP_TEXT


# ---------------------------------------------------------------------------
# J. No new infrastructure, dependency or trading authority
# ---------------------------------------------------------------------------


def test_j_no_new_dependency_or_service_platform_is_introduced():
    """Reuse the existing image and replica bridge; add no droplet or database."""
    service = _service()
    # The same image the other analytics services run.
    assert service["image"] == "opip-data-platform:${OPIP_DEPLOYED_SHA:-local}"
    # The same network the other analytics services use.
    assert list(service["networks"]) == ["opip-analytics"]
    # The same cockpit-only entry point already reviewed.
    assert "app.api.cockpit_service:app" in service["command"]
    assert "app.main:app" not in " ".join(service["command"])


def test_j_requirements_are_unchanged_by_this_change():
    """Exposure must not require a new server or proxy dependency.

    Parsed rather than substring-matched: ``uvicorn[standard]`` is already declared
    and legitimately contains the text ``uvicorn[``. What must be absent is any
    *proxy platform* - the whole point is to reuse the host's existing TLS proxy.
    """
    for name in ("requirements.txt", "requirements-dev.txt", "pyproject.toml"):
        text = (REPO / name).read_text(encoding="utf-8").lower()
        for banned in ("nginx", "caddy", "traefik", "gunicorn", "hypercorn"):
            assert banned not in text, f"{name} introduces proxy/server {banned}"

    # The application server itself is already a declared dependency.
    assert "uvicorn" in (REPO / "requirements.txt").read_text(encoding="utf-8").lower()


def test_j_cockpit_service_holds_no_trading_or_exchange_authority():
    """Checked via the AST, so prose explaining the boundary is not mistaken for it."""
    module = (REPO / "app" / "api" / "cockpit_service.py").read_text(encoding="utf-8")
    tree = ast.parse(module)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    # The only application surface it may mount is the read-only cockpit router.
    assert imported == {"__future__", "fastapi", "app.api.cockpit"}, imported

    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for banned in (
        "include_router",
        "submit",
        "trigger_paper_protection_action",
        "admit_paper_opportunity",
        "add_job",
    ):
        if banned == "include_router":
            # Exactly one mount, of the cockpit router, is expected and asserted below.
            continue
        assert banned not in called, f"cockpit service calls {banned!r}"

    # Exactly one router is mounted, and it is the cockpit router.
    mounts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "include_router"
    ]
    assert len(mounts) == 1, mounts
    mounted = getattr(mounts[0].args[0], "id", None)
    assert mounted == "cockpit_router", mounted


def test_j_container_remains_hardened():
    service = _service()
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["mem_limit"] and service["pids_limit"] == 128
    # No credentials are injected beyond the sealed analytics env file.
    assert set(service["environment"]) == {
        "OPIP_CANONICAL_REPLICA_ROOT",
        "OPIP_COCKPIT_HTTP_PORT",
        "PYTHONDONTWRITEBYTECODE",
    }


def test_j_readme_documents_the_required_proxy_routes_without_inventing_a_proxy():
    """Routes are documented for the host-managed proxy; no second proxy is added."""
    for path in (
        "/cockpit",
        "/api/cockpit/overview",
        "/api/cockpit/trades",
        "/api/cockpit/trades/*",
    ):
        assert path in README_TEXT, f"README omits required route {path}"
    assert "TLS" in README_TEXT
    # Explicitly refused exposure.
    assert "do **not** expose" in README_TEXT or "do not expose" in README_TEXT
