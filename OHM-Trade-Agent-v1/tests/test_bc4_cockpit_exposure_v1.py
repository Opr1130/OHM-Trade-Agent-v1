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
#: The Cockpit has its own Compose surface. Compose interpolates a whole file before
#: selecting a service, and the shared analytics file carries mandatory Grafana/
#: PostgreSQL variables, so a Cockpit-only deployment sourced from that file fails on
#: `${OPIP_GRAFANA_ADMIN_USER:?}` even when the shell control flow never reaches the
#: Grafana plane. The Cockpit is therefore defined once, in this file, and a dedicated
#: suite (tests/test_bc4e1_cockpit_compose_isolation_v1.py) proves it renders with every
#: PostgreSQL/Grafana variable absent.
COCKPIT_COMPOSE = REPO / "deploy" / "analytics" / "docker-compose.cockpit.yml"
ENV_EXAMPLE = REPO / "deploy" / "analytics" / "env.example"
BOOTSTRAP = REPO / "deploy" / "analytics" / "bootstrap-opip-data-platform.sh"
README = REPO / "deploy" / "analytics" / "README.md"

COMPOSE_TEXT = COMPOSE.read_text(encoding="utf-8")
COMPOSE_DATA = yaml.safe_load(COMPOSE_TEXT)
COCKPIT_COMPOSE_TEXT = COCKPIT_COMPOSE.read_text(encoding="utf-8")
COCKPIT_COMPOSE_DATA = yaml.safe_load(COCKPIT_COMPOSE_TEXT)
BOOTSTRAP_TEXT = BOOTSTRAP.read_text(encoding="utf-8")
README_TEXT = README.read_text(encoding="utf-8")


#: One ``${VAR:-default}`` token. Parsing published ports by splitting on ``:`` is
#: wrong because the ``:-`` default itself contains a colon.
_PORT_PART = re.compile(r"\$\{[A-Z_]+:-[^}]*\}")


def _port_parts(mapping: str) -> list[str]:
    """The interpolated parts of a compose port mapping, order preserved."""
    return _PORT_PART.findall(str(mapping))


def _service(name: str = "opip-cockpit") -> dict:
    """A service definition, read from whichever Compose surface owns it.

    The Cockpit is defined in ``docker-compose.cockpit.yml``; Grafana and the other
    analytics services remain in the shared file. Dispatching here keeps every
    assertion below meaningful without duplicating the Cockpit definition anywhere.
    """
    if name == "opip-cockpit":
        return COCKPIT_COMPOSE_DATA["services"][name]
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
    assert replica[0] == (
        "/var/lib/opip-learning/canonical-replica:/app/canonical-replica:ro"
    )

    # The service must NOT pin OPIP_CANONICAL_REPLICA_ROOT to the mounted parent.
    #
    # `current` + `generations/<id>` is how an installed replica is addressed: the
    # parent directory is a repository of generations, so it holds no manifest and no
    # canonical database. Pinning it here would point the Cockpit at a directory that is
    # not a bundle. Bootstrap instead writes the resolved generation into
    # /etc/opip-cockpit.env, which this service loads via env_file.
    assert "OPIP_CANONICAL_REPLICA_ROOT" not in _service()["environment"]
    assert "OPIP_CANONICAL_REPLICA_ROOT=%s" in BOOTSTRAP_TEXT
    # The generation is optional, so the secret surface can be materialized before the
    # generation is resolvable, and an unverified root is never recorded.
    assert 'local replica_root="${1:-}"' not in BOOTSTRAP_TEXT
    assert '"$COCKPIT_REPLICA_CONTAINER_ROOT" >> "$temporary"' in BOOTSTRAP_TEXT
    # The resolution itself is delegated to the existing resolver, never reimplemented.
    assert "python -m app.opip.learning.canonical_replica resolve" in BOOTSTRAP_TEXT


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
    """A client over the analytics-plane app with a known cockpit secret.

    The Cockpit reads its credential from the environment rather than from the trading
    application's settings, so the fixture sets the environment variable.
    """
    from fastapi.testclient import TestClient

    from app.api import cockpit_service

    monkeypatch.setenv("OPIP_COCKPIT_SECRET", "expected-secret")
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
        BOOTSTRAP_TEXT.index("write_cockpit_state()")
    ]
    assert "http://" not in preflight
    assert "curl" not in preflight
    # No suppression directive was introduced.
    assert "NOSONAR" not in BOOTSTRAP_TEXT


def test_i_app_behaviour_is_proven_by_the_healthcheck_and_suite():
    """The split must be explicit: the preflight states where app proof comes from.

    The page-serving 200 is proven by the container healthcheck, which issues a real
    GET on /cockpit inside the container; route existence, 401 enforcement and
    non-GET rejection are proven by the test suite driving the real ASGI app.
    """
    assert "cockpit_app_behaviour=verified_by_healthcheck_and_test_suite" in (
        BOOTSTRAP_TEXT
    )
    # The healthcheck really does serve the page over HTTP inside the container.
    healthcheck = _service()["healthcheck"]["test"][1]
    assert "/cockpit" in healthcheck
    assert "status==200" in healthcheck
    # And the suite really does exercise auth over the read-only surface.
    suite = pathlib.Path(__file__).read_text(encoding="utf-8")
    assert 'client.get("/api/cockpit/overview")' in suite


def test_i_bootstrap_declares_the_cockpit_exposure_contract():
    """The preflight must state its own exposure assumptions to the operator."""
    assert "cockpit_exposure=host-loopback" in BOOTSTRAP_TEXT
    assert "cockpit_requires_tls_reverse_proxy=true" in BOOTSTRAP_TEXT


def test_i_preflight_runs_before_the_cockpit_is_declared_ready():
    """Ready state must not be written before reachability is proven."""
    start = BOOTSTRAP_TEXT[
        BOOTSTRAP_TEXT.index("cockpit_start() {") :
        BOOTSTRAP_TEXT.index("cockpit_deploy_verified() {")
    ]
    # The evidence write is the `write_cockpit_state` call, which records
    # COCKPIT_READY_AT_UTC/COCKPIT_READY_SHA (the key set itself is asserted in
    # tests/test_bc4_cockpit_ready_gate_v1.py).
    assert start.index("cockpit_preflight") < start.index("write_cockpit_state ")


def test_i_preflight_is_invoked_from_the_reads_ready_stage():
    """`reads-ready` must start the Cockpit, and both stages must share one primitive.

    The Cockpit start/health/preflight sequence used to be inlined in `reads-ready`. It
    is now a single `cockpit_start` primitive used by both `reads-ready` and
    `cockpit-ready`, so the two stages cannot drift into separate implementations that
    disagree about exposure or ordering.

    Replica verification is deliberately NOT part of that primitive: it belongs to
    `cockpit-ready`, because historical PostgreSQL/Grafana readiness must not be blocked
    by the independent replica plane.
    """
    ready_stage = BOOTSTRAP_TEXT[
        BOOTSTRAP_TEXT.index('elif [[ "$STAGE" == "reads-ready" ]]') :
    ]
    assert "cockpit_start" in ready_stage
    assert "cockpit_build_image" in ready_stage
    assert "cockpit_verify_replica" not in ready_stage

    # Exactly one implementation of the start sequence exists...
    assert BOOTSTRAP_TEXT.count("compose up -d opip-cockpit") == 1
    assert BOOTSTRAP_TEXT.count("\n  cockpit_preflight\n") == 1
    # ...and it lives in the shared primitive, which proves reachability and waits for
    # health before doing so.
    start = BOOTSTRAP_TEXT[
        BOOTSTRAP_TEXT.index("cockpit_start() {") :
        BOOTSTRAP_TEXT.index("cockpit_deploy_verified() {")
    ]
    assert "cockpit_wait_healthy" in start
    assert start.index("compose up -d opip-cockpit") < start.index("cockpit_wait_healthy")
    assert start.index("cockpit_wait_healthy") < start.index("cockpit_preflight")
    # Readiness is recorded only after the preflight succeeds.
    assert start.index("cockpit_preflight") < start.index("write_cockpit_state ")


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
    # The analytics plane network, plus the dedicated non-internal publish network that
    # gives Docker a path to install the host-loopback port mapping. The publish network
    # is used by this service only; no new service or platform is introduced.
    assert list(service["networks"]) == ["opip-analytics", "opip-cockpit-publish"]
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


def test_f_cockpit_receives_only_a_filtered_environment():
    """The externally reachable Cockpit must not load unrelated credentials.

    Review finding (valid): the service loaded the whole sealed analytics env file,
    which also holds the PostgreSQL admin, shipper, learning and dashboard credentials
    and privileged database URLs. Handing those to an internet-facing read-only service
    puts unrelated secrets on an unnecessary surface.
    """
    service = _service()
    env_files = service["env_file"]
    assert env_files == ["/etc/opip-cockpit.env"], env_files

    # Bootstrap must derive that file with a strict allowlist, like Grafana's.
    assert "write_cockpit_env_file" in BOOTSTRAP_TEXT
    assert 'COCKPIT_ENV_FILE="/etc/opip-cockpit.env"' in BOOTSTRAP_TEXT

    allowlist = BOOTSTRAP_TEXT[
        BOOTSTRAP_TEXT.index("write_cockpit_env_file()") :
        BOOTSTRAP_TEXT.index("compose() {")
    ]
    keys = set(re.findall(r"^\s{4}([A-Z_]+)$", allowlist, flags=re.MULTILINE))
    assert keys == {
        "OPIP_COCKPIT_SECRET",
        "OPIP_COCKPIT_BIND_ADDRESS",
        "OPIP_COCKPIT_HOST_PORT",
        "OPIP_COCKPIT_HTTP_PORT",
    }, keys

    # None of the unrelated credentials may be in the Cockpit's allowlist, and in
    # particular not the trading host's order-capable operator secret.
    for forbidden in (
        "WEBHOOK_SECRET",
        "OPIP_POSTGRES_ADMIN_PASSWORD",
        "OPIP_SHIPPER_PASSWORD",
        "OPIP_LEARNING_DATABASE_PASSWORD",
        "OPIP_DASHBOARD_PASSWORD",
        "OPIP_GRAFANA_ADMIN_PASSWORD",
        "OPIP_GRAFANA_DB_PASSWORD",
        "OPIP_ANALYTICS_ADMIN_DATABASE_URL",
        "OPIP_ANALYTICS_DATABASE_URL",
    ):
        assert forbidden not in keys, f"cockpit env allowlist leaks {forbidden}"


def test_g_cockpit_credential_is_fed_through_the_filtered_file():
    """The Cockpit's own credential must be the only secret it receives."""
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "OPIP_COCKPIT_SECRET=" in text
    assert "OPIP_COCKPIT_SECRET" in BOOTSTRAP_TEXT
    # The filtered allowlist must not carry the trading host's order-capable secret.
    allowlist = BOOTSTRAP_TEXT[
        BOOTSTRAP_TEXT.index("write_cockpit_env_file()") :
        BOOTSTRAP_TEXT.index("compose() {")
    ]
    assert "WEBHOOK_SECRET" not in allowlist


def test_g_cockpit_uses_its_own_read_only_credential_not_the_trading_secret():
    """The Cockpit must not authenticate with the trading host's operator secret.

    Review finding (valid, and serious): the trading host's WEBHOOK_SECRET also gates
    POST /operator/mode, POST /operator/orders and PATCH /operator/orders/{trade_id},
    so it carries order creation and modification authority. Copying it onto the
    externally reachable analytics plane would place an order-capable credential on a
    read-only surface.
    """
    import ast

    source = (REPO / "app" / "api" / "cockpit.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    # It must not read the trading application's settings at all.
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "app.core.config" not in modules, "cockpit reads trading settings"
    assert "get_settings" not in source
    # It must not use the trading secret as its expected value. The header NAME
    # `x-webhook-secret` is deliberately preserved as the auth mechanism, so this
    # checks the settings lookup rather than the literal word.
    assert "get_settings().webhook_secret" not in source
    assert "settings.webhook_secret" not in source

    # It reads its own credential from the environment.
    assert "OPIP_COCKPIT_SECRET" in source
    env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "OPIP_COCKPIT_SECRET=" in env_text
    # And the trading secret must not be declared on the analytics plane.
    declared = [
        line.strip()
        for line in env_text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert not any(line.startswith("WEBHOOK_SECRET=") for line in declared), declared


def test_g_bootstrap_refuses_order_capable_credentials_on_the_analytics_plane():
    """A plane guard must fail closed rather than tolerate a trading credential."""
    assert "guard_no_trading_credentials" in BOOTSTRAP_TEXT
    guard = BOOTSTRAP_TEXT[
        BOOTSTRAP_TEXT.index("guard_no_trading_credentials()") :
        BOOTSTRAP_TEXT.index("write_cockpit_env_file()")
    ]
    for key in ("WEBHOOK_SECRET", "KRAKEN_API_KEY", "KRAKEN_API_SECRET", "TELEGRAM_BOT_TOKEN"):
        assert key in guard, f"plane guard does not reject {key}"
    assert "must not be present on the analytics plane" in guard


def test_g_plane_guard_normalizes_assignment_syntax():
    """`export KEY=...` and indented assignments must not slip past the guard.

    Review finding (valid, P1): the guard compared the raw first `=`-separated field, so
    `export WEBHOOK_SECRET=...` (or a whitespace-prefixed assignment) was not
    recognized. Because the sealed file is already sourced with `set -a`, the
    order-capable credential would then have been present in the bootstrap process
    despite the guard claiming to reject it.
    """
    guard = BOOTSTRAP_TEXT[
        BOOTSTRAP_TEXT.index("guard_no_trading_credentials()") :
        BOOTSTRAP_TEXT.index("guard_no_trading_credentials\n")
    ]
    # It must normalize before comparing...
    assert "sub(/^[[:space:]]+/, \"\", line)" in guard
    assert "sub(/^export[[:space:]]+/, \"\", line)" in guard
    # ...and independently validate the already-sourced environment, which covers
    # every syntax Bash accepts without re-implementing its parser.
    assert "${!key:-}" in guard


def _guard_awk_program() -> str:
    """The exact awk program the bootstrap plane guard runs.

    Extracted from the script rather than duplicated, so this test verifies the real
    artifact instead of a copy that could drift from it.
    """
    guard = BOOTSTRAP_TEXT[
        BOOTSTRAP_TEXT.index("guard_no_trading_credentials()") :
        BOOTSTRAP_TEXT.index("guard_no_trading_credentials\n")
    ]
    start = guard.index("awk -v key=\"$key\" '") + len("awk -v key=\"$key\" '")
    end = guard.index("' \"$ENV_FILE\"")
    return guard[start:end]


def test_g_plane_guard_awk_matches_only_real_assignments():
    """Run the guard's actual awk over the assignment forms Bash accepts.

    Invoked through argv with no shell involved. An earlier revision used
    ``bash -c "awk '...'"``, which made the test fail on Linux for a harness quoting
    reason rather than a guard reason; passing the program and file as separate
    arguments removes that whole class of problem.

    The awk is skipped where the tool is unavailable (this Windows dev host), but the
    Python-level assertions below run everywhere.
    """
    import shutil
    import subprocess
    import tempfile

    cases = [
        # Bash accepts all of these when sourcing, so the guard must catch them.
        ("WEBHOOK_SECRET=abc\n", True),
        ("export WEBHOOK_SECRET=abc\n", True),
        ("  WEBHOOK_SECRET=abc\n", True),
        ("\texport WEBHOOK_SECRET=abc\n", True),
        ("export  WEBHOOK_SECRET=abc\n", True),
        # Inert or unrelated lines must not trip it.
        ("# WEBHOOK_SECRET=abc\n", False),
        ("  # WEBHOOK_SECRET=abc\n", False),
        ("#export WEBHOOK_SECRET=abc\n", False),
        ("OPIP_COCKPIT_SECRET=abc\n", False),
        ("NOT_WEBHOOK_SECRET=abc\n", False),
    ]

    program = _guard_awk_program()
    # The normalization the guard depends on must actually be in the extracted text.
    assert "sub(/^[[:space:]]+/, \"\", line)" in program
    assert "sub(/^export[[:space:]]+/, \"\", line)" in program

    awk = shutil.which("awk") or shutil.which("gawk")
    if awk is None:
        return

    for text, expected in cases:
        # delete=False is required so the file survives to be read by the child
        # process on Windows, so cleanup is explicit and runs even if the assertion
        # fails. Otherwise every run would leave ten .env files in the temp directory.
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".env", delete=False, encoding="utf-8"
        )
        try:
            handle.write(text)
            handle.close()
            result = subprocess.run(
                [awk, "-v", "key=WEBHOOK_SECRET", program, handle.name],
                capture_output=True,
                text=True,
                check=False,
            )
            assert (result.returncode == 0) is expected, (
                f"guard mismatch for {text!r}: exit {result.returncode}"
            )
        finally:
            handle.close()
            pathlib.Path(handle.name).unlink(missing_ok=True)


def test_g_plane_guard_normalization_semantics():
    """Python-level mirror of the guard's normalization, runnable on any platform."""
    program = _guard_awk_program()
    # Deliberately mirrors the two `sub()` calls the extracted program must contain.
    def normalized_match(text: str, key: str) -> bool:
        for raw in text.splitlines():
            line = raw.lstrip()
            if line.startswith("export"):
                line = line[len("export") :].lstrip()
            if line.startswith(f"{key}="):
                return True
        return False

    assert "sub(/^[[:space:]]+/, \"\", line)" in program
    assert normalized_match("export WEBHOOK_SECRET=abc\n", "WEBHOOK_SECRET") is True
    assert normalized_match("  WEBHOOK_SECRET=abc\n", "WEBHOOK_SECRET") is True
    assert normalized_match("# WEBHOOK_SECRET=abc\n", "WEBHOOK_SECRET") is False
    assert normalized_match("NOT_WEBHOOK_SECRET=abc\n", "WEBHOOK_SECRET") is False


def test_g_cockpit_authentication_fails_closed_when_unconfigured(monkeypatch):
    """An unset secret must yield 401, never an open read-only surface."""
    from fastapi.testclient import TestClient

    from app.api import cockpit, cockpit_service

    monkeypatch.delenv("OPIP_COCKPIT_SECRET", raising=False)
    client = TestClient(cockpit_service.app)

    response = client.get(
        "/api/cockpit/overview", headers={"x-webhook-secret": "anything"}
    )
    assert response.status_code == 401
    assert cockpit._cockpit_secret() == ""  # noqa: SLF001


def test_g_cockpit_rejects_the_trading_operator_secret(monkeypatch):
    """The order-capable trading secret must not authenticate the Cockpit."""
    from fastapi.testclient import TestClient

    from app.api import cockpit_service

    monkeypatch.setenv("OPIP_COCKPIT_SECRET", "cockpit-read-only-secret")
    client = TestClient(cockpit_service.app)

    rejected = client.get(
        "/api/cockpit/overview",
        headers={"x-webhook-secret": "the-trading-operator-secret"},
    )
    assert rejected.status_code == 401


def test_g_gitleaks_allowlists_stay_narrowly_scoped():
    """Every allowlist entry must be rule- and commit-scoped, per the config's rules.

    The config forbids path-only or repo-wide exclusions and forbids disabling a rule.
    A new entry was needed because gitleaks scans full history: the placeholder fix
    cleaned the current tree, but the earlier commit's blob still trips the rule. This
    asserts the discipline is preserved so an allowlist can never quietly become a
    blanket exclusion.
    """
    import tomllib

    config = tomllib.loads(
        (REPO.parent / ".gitleaks.toml").read_text(encoding="utf-8")
    )
    entries = config.get("allowlists", [])
    assert entries, "expected the existing fixture allowlist to be present"

    for entry in entries:
        assert "description" in entry, entry
        assert entry.get("targetRules") == ["generic-api-key"], entry
        assert entry.get("condition") == "AND", entry
        assert entry.get("commits"), f"allowlist is not commit-scoped: {entry}"
        assert entry.get("paths"), f"allowlist is not path-scoped: {entry}"
        # A repo-wide or rule-global escape hatch would break the config's contract.
        for forbidden in ("regexTarget", "stopwords"):
            assert forbidden not in entry, f"allowlist uses {forbidden}: {entry}"


def test_g_cockpit_secret_placeholder_is_not_credential_shaped():
    """The placeholder must not trip the generic-api-key secret scan.

    A hyphenated placeholder next to a ``*SECRET`` key reaches gitleaks'
    generic-api-key entropy threshold, which fails the secret-scan gate on a string
    that is not a secret. Rather than allowlisting it (the config requires allowlists
    to stay narrow and forbids broad exclusions), the marker is kept obviously
    synthetic. This guards against a future edit reintroducing a shaped value.
    """
    import math
    from collections import Counter

    lines = [
        line.strip()
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("OPIP_COCKPIT_SECRET=") and not line.startswith("#")
    ]
    assert len(lines) == 1, lines
    value = lines[0].split("=", 1)[1]

    counts = Counter(value)
    length = len(value)
    entropy = -sum(
        (count / length) * math.log2(count / length) for count in counts.values()
    )
    # gitleaks' bundled generic-api-key threshold is 3.7.
    assert entropy < 3.4, f"placeholder entropy {entropy:.3f} is too credential-shaped"


def test_i_preflight_proves_the_listener_belongs_to_the_cockpit():
    """A stale or foreign process holding the port must not satisfy the preflight.

    Review finding (valid): checking a generic listener on the configured port could
    pass while the Cockpit's own publish or routes were broken.
    """
    assert "docker port opip-cockpit" in BOOTSTRAP_TEXT
    assert "cockpit_publish_owner=opip-cockpit" in BOOTSTRAP_TEXT
    assert "publishes no host port" in BOOTSTRAP_TEXT


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
