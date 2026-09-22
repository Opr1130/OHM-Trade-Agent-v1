"""PR-B/C-4E.1: isolate Cockpit Compose evaluation from the analytics plane.

The blocker this suite guards against
=====================================
The owner-gated `cockpit-ready` stage passed every workflow admission gate and then
failed on the analytics host with:

    error while interpolating services.opip-grafana.environment.GF_SECURITY_ADMIN_USER:
    required variable OPIP_GRAFANA_ADMIN_USER is missing a value:
    set in /etc/opip-data-platform.env

The shell control flow was already correct: `cockpit-ready` is dispatched before the
PostgreSQL/Grafana plane and executes none of it. But Docker Compose interpolates the
**entire file** it is given before selecting a single service, and
`docker compose -f docker-compose.yml build opip-cockpit` therefore parsed
`opip-grafana` and its mandatory `${OPIP_GRAFANA_ADMIN_USER:?}`. Shell-level isolation
cannot isolate Compose interpolation; only a separate Compose file can.

This is why part of this suite executes `docker compose config` instead of only
asserting on source text. The defect above passed structural tests in PR #257 precisely
because those tests never ran Compose.

What must hold
==============
* The Cockpit has one Compose surface and it renders with every PostgreSQL/Grafana
  variable absent.
* The shared analytics file keeps its strict `${...:?}` requirements, unchanged.
* Every Cockpit build/start/status operation uses the Cockpit-only surface; the
  PostgreSQL/Grafana stages keep the shared file.
* The Cockpit hardening, loopback exposure, exact-SHA image tag and read-only replica
  mount survive the move unchanged.
* `cockpit-ready` still publishes only `COCKPIT_READY_*`, `reads-ready` still publishes
  only `READS_READY_*`, and the seven-day soak is untouched.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO.parent

COCKPIT_COMPOSE = REPO / "deploy/analytics/docker-compose.cockpit.yml"
ANALYTICS_COMPOSE = REPO / "deploy/analytics/docker-compose.yml"
BOOTSTRAP = (REPO / "deploy/analytics/bootstrap-opip-data-platform.sh").read_text(
    encoding="utf-8"
)
COCKPIT_TEXT = COCKPIT_COMPOSE.read_text(encoding="utf-8")
ANALYTICS_TEXT = ANALYTICS_COMPOSE.read_text(encoding="utf-8")
README = (REPO / "deploy/analytics/README.md").read_text(encoding="utf-8")

TARGET_SHA = "a47c7756b62d08e56639056572c4a42a8106c9c1"
GENERATION_ID = "a" * 64

#: Variables the shared analytics plane requires and the Cockpit must not need.
ANALYTICS_PLANE_VARS = (
    "OPIP_GRAFANA_ADMIN_USER",
    "OPIP_GRAFANA_ADMIN_PASSWORD",
    "OPIP_GRAFANA_DB_PASSWORD",
    "OPIP_POSTGRES_ADMIN_PASSWORD",
    "OPIP_SHIPPER_PASSWORD",
    "OPIP_ANALYTICS_ADMIN_DATABASE_URL",
    "OPIP_ANALYTICS_DATABASE_URL",
)

#: Additional non-secret analytics settings the Cockpit surface must not need.
ANALYTICS_PLANE_EXTRAS = (
    "OPIP_GRAFANA_DB_NAME",
    "OPIP_GRAFANA_DB_USER",
    "OPIP_GRAFANA_DB_SSLMODE",
    "OPIP_GRAFANA_ROOT_URL",
    "OPIP_GRAFANA_DOMAIN",
    "OPIP_POSTGRES_BIND_ADDRESS",
    "OPIP_POSTGRES_ADMIN_USER",
    "OPIP_POSTGRES_DB",
    "OPIP_DEPLOYED_SHA",
)

#: Services that must never appear in the Cockpit-only configuration.
ANALYTICS_PLANE_SERVICES = (
    "opip-postgres",
    "opip-shipper",
    "opip-grafana",
    "opip-data-admin",
)

COCKPIT_VARS = {
    "OPIP_COCKPIT_SECRET",
    "OPIP_COCKPIT_BIND_ADDRESS",
    "OPIP_COCKPIT_HOST_PORT",
    "OPIP_COCKPIT_HTTP_PORT",
}

#: Analytics-plane variables the shared Compose file enforces itself with `${VAR:?}`.
COMPOSE_MANDATORY_VARS = (
    "OPIP_POSTGRES_ADMIN_PASSWORD",
    "OPIP_GRAFANA_ADMIN_USER",
    "OPIP_GRAFANA_ADMIN_PASSWORD",
    "OPIP_GRAFANA_DB_PASSWORD",
)

#: Analytics-plane variables validated by bootstrap rather than by Compose interpolation.
BOOTSTRAP_VALIDATED_VARS = (
    "OPIP_SHIPPER_PASSWORD",
    "OPIP_ANALYTICS_ADMIN_DATABASE_URL",
    "OPIP_ANALYTICS_DATABASE_URL",
)


def _code_only(text: str) -> str:
    """Drop comment lines.

    The Cockpit Compose file's header explains this defect and quotes the failing
    variable, and several bootstrap comments mention READS_READY_* to say it is NOT
    touched. Comments are documentation, so assertions about what a file *requires* or
    *writes* must not read them as configuration.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _extract_function(text: str, name: str) -> str:
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _extract_guarded_block(text: str, guard: str) -> str:
    """Return `if <guard>; then ... fi`, matched by nesting depth."""
    lines = text.splitlines()
    start = next(
        (index for index, line in enumerate(lines) if line.strip() == guard), None
    )
    assert start is not None, f"guard not found: {guard}"
    depth = 0
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if re.match(r"^if\b", stripped):
            depth += 1
        if stripped == "fi" or stripped.startswith("fi ") or stripped.startswith("fi;"):
            depth -= 1
            if depth == 0:
                return "\n".join(lines[start : index + 1])
    raise AssertionError(f"unterminated guard: {guard}")


def _redirect_env_files(text: str, target: Path) -> tuple[str, int]:
    """Point every ``env_file`` entry at ``target``, returning the rewritten count.

    ``env_file`` targets are absolute host paths that do not exist off the analytics
    host, and Compose requires them to exist. The target path has no bearing on the
    property under test - whether the file's own ``${...:?}`` interpolation succeeds -
    so these tests render a copy. The caller asserts only those lines changed.
    """
    pattern = re.compile(r"^(\s*- )/[^\n]*\.env$", flags=re.MULTILINE)
    return pattern.subn(rf"\g<1>{target}", text)


def _disagreeing_line_count(before: str, after: str) -> int:
    assert len(before.splitlines()) == len(after.splitlines())
    return sum(
        1
        for old, new in zip(before.splitlines(), after.splitlines())
        if old != new
    )


def _published_mappings(service: dict) -> list[tuple[str, str, str]]:
    """(host_ip, published, target) per port, accepting short OR long syntax.

    `docker compose config` emits the canonical long form (``host_ip``/``published``/
    ``target``) on recent Compose versions and the short ``host:published:target`` string
    on others. Normalizing both keeps these tests about interpolation rather than about
    which Compose version the runner has.
    """
    mappings: list[tuple[str, str, str]] = []
    for entry in service.get("ports", []):
        if isinstance(entry, dict):
            mappings.append(
                (
                    str(entry.get("host_ip", "")),
                    str(entry.get("published", "")),
                    str(entry.get("target", "")),
                )
            )
            continue
        parts = str(entry).split(":")
        if len(parts) == 3:
            mappings.append((parts[0], parts[1], parts[2]))
        elif len(parts) == 2:
            mappings.append(("", parts[0], parts[1]))
        else:
            mappings.append(("", "", parts[0]))
    return mappings


def _bind_mounts(service: dict) -> list[tuple[str, str, bool]]:
    """(source, target, read_only) per mount, accepting short OR long syntax."""
    mounts: list[tuple[str, str, bool]] = []
    for entry in service.get("volumes", []):
        if isinstance(entry, dict):
            mounts.append(
                (
                    str(entry.get("source", "")),
                    str(entry.get("target", "")),
                    bool(entry.get("read_only", False)),
                )
            )
            continue
        parts = str(entry).split(":")
        options = parts[2].split(",") if len(parts) > 2 else []
        mounts.append(
            (parts[0], parts[1] if len(parts) > 1 else "", "ro" in options)
        )
    return mounts


def _docker_compose_config(
    compose_path: Path, env_file: Path
) -> subprocess.CompletedProcess[str]:
    """Run `docker compose config` with the analytics-plane variables provably absent.

    The Cockpit variables are removed too. Otherwise a value exported by the CI or
    developer environment would override the interpolation env file, and the
    "every variable unset" cases below would silently validate a host-provided value
    instead of the Compose defaults they exist to prove.
    """
    environment = dict(os.environ)
    for name in ANALYTICS_PLANE_VARS + ANALYTICS_PLANE_EXTRAS + tuple(COCKPIT_VARS):
        environment.pop(name, None)
    environment.pop("OPIP_DEPLOYED_SHA", None)

    return subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "-f",
            str(compose_path),
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


def _render(tmp_path: Path, text: str) -> tuple[Path, Path]:
    """Stage a renderable copy of a Compose file plus a minimal interpolation env file."""
    render_root = tmp_path / "render"
    render_root.mkdir(parents=True, exist_ok=True)
    env_target = render_root / "service.env"
    env_target.write_text("OPIP_COCKPIT_SECRET=test-secret\n", encoding="utf-8")
    rewritten, substitutions = _redirect_env_files(text, env_target)
    assert substitutions >= 1, "no env_file entry was found to redirect"
    assert _disagreeing_line_count(text, rewritten) == substitutions
    staged = render_root / "compose.yml"
    staged.write_text(rewritten, encoding="utf-8")
    return staged, env_target


requires_docker_compose = pytest.mark.skipif(
    shutil.which("docker") is None,
    reason=(
        "executing `docker compose config` is the point of these tests: the defect they "
        "guard against was invisible to source-text assertions. Docker is present on the "
        "ubuntu-latest runner used by CI, where they execute"
    ),
)


# ---------------------------------------------------------------------------
# 1. The Cockpit-only surface is minimal and renders with the analytics plane absent
# ---------------------------------------------------------------------------


def test_cockpit_compose_exists_and_defines_only_the_cockpit():
    """The Cockpit has exactly one Compose surface, and it is minimal."""
    data = yaml.safe_load(COCKPIT_TEXT)
    assert sorted(data["services"]) == ["opip-cockpit"]
    for service in ANALYTICS_PLANE_SERVICES:
        assert service not in data["services"]


def test_cockpit_compose_declares_no_analytics_plane_variable():
    """Only Cockpit variables may be required, and none from the analytics plane.

    An analytics-plane requirement here would reintroduce exactly the coupling this file
    exists to remove: Compose renders the whole file, so any mandatory Grafana or
    PostgreSQL variable makes the Cockpit undeployable without that plane.
    """
    code = _code_only(COCKPIT_TEXT)
    required = set(re.findall(r"\$\{([A-Z_]+):\?", code))
    assert required <= COCKPIT_VARS, required
    for name in ANALYTICS_PLANE_VARS:
        assert name not in code, f"{name} must not appear in the Cockpit surface"
    # Every variable the Cockpit surface interpolates is a Cockpit variable or the
    # release SHA that pins the image tag.
    referenced = set(re.findall(r"\$\{([A-Z_]+)", code))
    assert referenced <= COCKPIT_VARS | {"OPIP_DEPLOYED_SHA"}, referenced


@requires_docker_compose
def test_cockpit_compose_renders_with_no_analytics_plane_variables(tmp_path: Path):
    """Executed proof: interpolation succeeds with the analytics plane absent.

    This is the assertion that would have caught the production failure.
    """
    staged, env_target = _render(tmp_path, COCKPIT_TEXT)
    env_file = tmp_path / "render" / "interpolation.env"
    env_file.write_text("OPIP_COCKPIT_HTTP_PORT=8000\n", encoding="utf-8")
    assert env_target.exists()

    result = _docker_compose_config(staged, env_file)
    assert result.returncode == 0, (
        "the Cockpit surface must render with every analytics-plane variable absent; "
        f"stderr:\n{result.stderr}"
    )

    rendered = yaml.safe_load(result.stdout)
    assert sorted(rendered["services"]) == ["opip-cockpit"]
    # 6. No analytics-plane service is reachable from this configuration.
    for service in ANALYTICS_PLANE_SERVICES:
        assert service not in rendered["services"], service
        assert service not in result.stdout, service


@requires_docker_compose
def test_rendered_cockpit_keeps_loopback_exposure_and_exact_sha_image(tmp_path: Path):
    """The interpolated result, not the source text, keeps the safety contracts.

    Which fields are asserted here versus in
    ``test_cockpit_hardening_survives_the_move_unchanged`` is deliberate: this test covers
    what interpolation could actually break (the image tag, the published mapping from the
    `:-127.0.0.1` default, the mount), while the exact hardening values are pinned on the
    source, where they are not subject to Compose's output normalization.
    """
    staged, _ = _render(tmp_path, COCKPIT_TEXT)
    env_file = tmp_path / "render" / "interpolation.env"
    env_file.write_text(
        f"OPIP_COCKPIT_HTTP_PORT=8000\nOPIP_DEPLOYED_SHA={TARGET_SHA}\n",
        encoding="utf-8",
    )

    result = _docker_compose_config(staged, env_file)
    assert result.returncode == 0, result.stderr
    service = yaml.safe_load(result.stdout)["services"]["opip-cockpit"]

    # 9. Exact-SHA image tag, interpolated from the release variable.
    assert service["image"] == f"opip-data-platform:{TARGET_SHA}"
    # 8. Host loopback publish, container port interpolated from the Cockpit variable.
    mappings = _published_mappings(service)
    assert mappings == [("127.0.0.1", "8000", "8000")], mappings
    # 10. The read-only replica mount survives interpolation.
    assert _bind_mounts(service) == [
        (
            "/var/lib/opip-learning/canonical-replica",
            "/app/canonical-replica",
            True,
        )
    ], _bind_mounts(service)
    # 7. Read-only flag and dropped capabilities are unaffected by interpolation.
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]


@requires_docker_compose
def test_rendered_cockpit_publish_cannot_be_widened_by_an_unset_bind(tmp_path: Path):
    """With every Cockpit variable unset, interpolation must still yield host loopback."""
    staged, _ = _render(tmp_path, COCKPIT_TEXT)
    env_file = tmp_path / "render" / "interpolation.env"
    env_file.write_text("", encoding="utf-8")

    result = _docker_compose_config(staged, env_file)
    assert result.returncode == 0, result.stderr
    mappings = _published_mappings(
        yaml.safe_load(result.stdout)["services"]["opip-cockpit"]
    )
    assert mappings == [("127.0.0.1", "8000", "8000")], mappings
    for host_ip, _published, _target in mappings:
        for wildcard in ("0.0.0.0", "[::]", "*", ""):
            assert host_ip != wildcard, mappings


# ---------------------------------------------------------------------------
# 2. The shared analytics file keeps its strict requirements
# ---------------------------------------------------------------------------


def test_analytics_compose_retains_its_mandatory_variables():
    """The fix must not have weakened the historical analytics plane."""
    for name in COMPOSE_MANDATORY_VARS:
        assert f"${{{name}:?" in ANALYTICS_TEXT, f"{name} lost its fail-closed guard"
    assert "${OPIP_GRAFANA_ADMIN_USER:?set in /etc/opip-data-platform.env}" in (
        ANALYTICS_TEXT
    )
    assert "${OPIP_POSTGRES_ADMIN_PASSWORD:?set in /etc/opip-data-platform.env}" in (
        ANALYTICS_TEXT
    )
    # The remaining analytics-plane variables are enforced by bootstrap, and that
    # enforcement must also be untouched.
    for name in BOOTSTRAP_VALIDATED_VARS:
        assert name in BOOTSTRAP, name
    assert "require_uri_unreserved_password OPIP_SHIPPER_PASSWORD" in BOOTSTRAP
    assert "require_analytics_verify_full_dsn OPIP_ANALYTICS_DATABASE_URL" in BOOTSTRAP
    assert "require_analytics_verify_full_dsn OPIP_ANALYTICS_ADMIN_DATABASE_URL" in (
        BOOTSTRAP
    )


@requires_docker_compose
def test_analytics_compose_still_refuses_to_render_without_grafana_settings(
    tmp_path: Path,
):
    """Executed negative control: the shared file still requires the Grafana settings.

    Without this, the positive test above could pass simply because Compose stopped
    enforcing `${...:?}` at all.
    """
    staged, _ = _render(tmp_path, ANALYTICS_TEXT)
    env_file = tmp_path / "render" / "interpolation.env"
    env_file.write_text("", encoding="utf-8")

    result = _docker_compose_config(staged, env_file)
    assert result.returncode != 0, (
        "the shared analytics file must still refuse to render without the "
        "PostgreSQL/Grafana settings"
    )
    combined = result.stderr + result.stdout
    assert any(name in combined for name in COMPOSE_MANDATORY_VARS), combined


# ---------------------------------------------------------------------------
# 3-5. Every Cockpit operation uses the Cockpit-only surface
# ---------------------------------------------------------------------------


def test_bootstrap_uses_the_cockpit_compose_surface_for_cockpit_operations():
    """Build, start and status must not interpolate the shared analytics file."""
    assert (
        'COCKPIT_COMPOSE="$APP_ROOT/deploy/analytics/docker-compose.cockpit.yml"'
        in BOOTSTRAP
    )
    helper = _extract_function(BOOTSTRAP, "cockpit_compose")
    assert 'docker compose --env-file "$ENV_FILE" -f "$COCKPIT_COMPOSE" "$@"' in helper

    # 3. Build.
    assert "cockpit_compose build opip-cockpit" in _extract_function(
        BOOTSTRAP, "cockpit_build_image"
    )
    # 4. Start.
    assert "cockpit_compose up -d opip-cockpit" in _extract_function(
        BOOTSTRAP, "cockpit_start"
    )
    # 5. Status, in the cockpit-ready dispatch.
    dispatch = BOOTSTRAP[
        BOOTSTRAP.index('if [[ "$STAGE" == "$COCKPIT_STAGE" ]]; then') :
        BOOTSTRAP.index("# PostgreSQL / Grafana plane")
    ]
    assert "cockpit_compose ps" in dispatch

    # No Cockpit operation may name the shared analytics file. The only place
    # `up -d opip-cockpit` may appear is behind the `cockpit_compose` helper.
    assert 'docker compose -f "$COMPOSE" build opip-cockpit' not in BOOTSTRAP
    for match in re.finditer(r"compose up -d opip-cockpit", BOOTSTRAP):
        prefix = BOOTSTRAP[: match.start()][-len("cockpit_") :]
        assert prefix == "cockpit_", BOOTSTRAP[max(0, match.start() - 40) : match.end()]
    assert BOOTSTRAP.count('-f "$COCKPIT_COMPOSE"') == 1


def test_historical_stages_keep_using_the_shared_analytics_surface():
    """The PostgreSQL/Grafana plane is untouched by the Cockpit split."""
    plane = BOOTSTRAP[BOOTSTRAP.index("# PostgreSQL / Grafana plane") :]
    for fragment in (
        "compose pull opip-postgres",
        "compose config --images",
        "compose up -d opip-postgres",
        "compose exec -T opip-postgres",
        "compose --profile admin run --rm opip-data-admin",
        "compose up -d opip-shipper",
    ):
        assert fragment in plane, fragment
    assert "COCKPIT_COMPOSE" not in plane
    # The only Cockpit-surface use in this plane is the status report for the Cockpit that
    # `reads-ready` starts; no PostgreSQL operation may go through it.
    assert re.findall(r"cockpit_compose \S+", plane) == ["cockpit_compose ps"]


def test_cockpit_service_is_not_declared_in_the_shared_analytics_file():
    """A second copy would be dead configuration that could drift from the hardening."""
    data = yaml.safe_load(ANALYTICS_TEXT)
    assert "opip-cockpit" not in data["services"]
    assert sorted(data["services"]) == [
        "opip-data-admin",
        "opip-grafana",
        "opip-postgres",
        "opip-shipper",
    ]
    # The removal is explained where the service used to be, so the reason is discoverable
    # from the analytics file itself.
    assert "docker-compose.cockpit.yml" in ANALYTICS_TEXT
    assert "interpolat" in ANALYTICS_TEXT.lower()


def test_cockpit_and_analytics_share_one_project_and_network_definition():
    """`name:` and the network must match, or the Cockpit lands in another network.

    A different project name would make Compose create a second network with the same
    private subnet, which fails outright, and would detach the Cockpit from the internal
    analytics network.
    """
    cockpit = yaml.safe_load(COCKPIT_TEXT)
    analytics = yaml.safe_load(ANALYTICS_TEXT)
    assert cockpit["name"] == analytics["name"] == "opip-data-platform"
    assert (
        cockpit["networks"]["opip-analytics"]
        == analytics["networks"]["opip-analytics"]
    )
    assert cockpit["networks"]["opip-analytics"]["internal"] is True


def test_cockpit_hardening_survives_the_move_unchanged():
    """Pin the security-critical settings that moved between Compose files.

    Silent loss of any of these is what the move could have caused.
    """
    service = yaml.safe_load(COCKPIT_TEXT)["services"]["opip-cockpit"]
    assert service["image"] == "opip-data-platform:${OPIP_DEPLOYED_SHA:-local}"
    assert service["container_name"] == "opip-cockpit"
    assert service["build"]["context"] == "../.."
    assert service["command"] == [
        "uvicorn",
        "app.api.cockpit_service:app",
        "--host",
        "0.0.0.0",
        "--port",
        "${OPIP_COCKPIT_HTTP_PORT:-8000}",
    ]
    assert service["ports"] == [
        "${OPIP_COCKPIT_BIND_ADDRESS:-127.0.0.1}:"
        "${OPIP_COCKPIT_HOST_PORT:-8000}:${OPIP_COCKPIT_HTTP_PORT:-8000}"
    ]
    assert service["volumes"] == [
        "/var/lib/opip-learning/canonical-replica:/app/canonical-replica:ro"
    ]
    # Two networks: the internal analytics plane, plus the dedicated non-internal
    # publish network Docker needs in order to install the host-loopback mapping.
    assert list(service["networks"]) == ["opip-analytics", "opip-cockpit-publish"]
    assert service["read_only"] is True
    assert service["tmpfs"] == ["/tmp:rw,noexec,nosuid,size=32m"]
    assert service["mem_limit"] == "256m"
    assert service["memswap_limit"] == "256m"
    assert service["cpus"] == "0.25"
    assert service["pids_limit"] == 128
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["env_file"] == ["/etc/opip-cockpit.env"]
    assert service["environment"] == {
        "OPIP_COCKPIT_HTTP_PORT": "${OPIP_COCKPIT_HTTP_PORT:-8000}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    assert service["healthcheck"]["interval"] == "30s"
    assert service["healthcheck"]["retries"] == 5
    assert service["healthcheck"]["start_period"] == "20s"
    assert "/cockpit" in service["healthcheck"]["test"][1]
    # No trading or exchange credential is *configured*. Comments explain that these are
    # absent, so this inspects the configuration rather than the prose.
    assert "WEBHOOK_SECRET" not in _code_only(COCKPIT_TEXT)
    assert "KRAKEN" not in _code_only(COCKPIT_TEXT)
    assert "TELEGRAM" not in COCKPIT_TEXT


# ---------------------------------------------------------------------------
# Evidence and semantics unchanged
# ---------------------------------------------------------------------------


def test_cockpit_ready_and_reads_ready_publish_disjoint_evidence():
    """`cockpit-ready` writes COCKPIT_READY_* only; `reads-ready` never does."""
    state_writer = _extract_function(BOOTSTRAP, "write_cockpit_state")
    assert "COCKPIT_READY_AT_UTC" in state_writer
    assert "COCKPIT_READY_SHA" in state_writer
    assert "READS_READY" not in _code_only(state_writer)
    # Exactly one call site, in the shared start primitive.
    assert BOOTSTRAP.count("write_cockpit_state ") == 1
    assert "write_cockpit_state" in _extract_function(BOOTSTRAP, "cockpit_start")

    reads_ready = BOOTSTRAP[BOOTSTRAP.index('elif [[ "$STAGE" == "reads-ready" ]]') :]
    assert "READS_READY_AT_UTC" in reads_ready
    assert "READS_READY_SHA" in reads_ready
    assert "COCKPIT_READY" not in _code_only(reads_ready)
    # It goes through the shared start primitive unverified, so it publishes no
    # COCKPIT_READY_* evidence (see the B/C-4E suite for the full contract).
    assert "cockpit_start " in reads_ready
    assert "unverified" in reads_ready


def test_reads_ready_seven_day_soak_and_gates_are_unchanged():
    """No isolation change may touch the historical readiness contract."""
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
    assert reads_ready.index("7 * 86400") < reads_ready.index("READS_READY_AT_UTC")


def test_cockpit_ready_still_dispatches_before_the_postgresql_plane():
    """The shell-level isolation from B/C-4E must not regress while fixing Compose."""
    guard = 'if [[ "$STAGE" == "$COCKPIT_STAGE" ]]; then'
    assert BOOTSTRAP.index(guard) < BOOTSTRAP.index("# PostgreSQL / Grafana plane")
    dispatch = _extract_guarded_block(BOOTSTRAP, guard)
    lines = [line.strip() for line in dispatch.rstrip().splitlines()]
    assert lines[-1] == "fi"
    assert lines[-2] == "exit 0"
    for effect in (
        "opip-postgres",
        "opip-shipper",
        "validate_postgres_tls_key",
        "admin_run",
    ):
        assert effect not in dispatch, effect


def test_reads_ready_reports_the_cockpit_from_its_own_surface():
    """The shared-surface `compose ps` cannot show a service it does not own."""
    reads_ready = BOOTSTRAP[BOOTSTRAP.index('elif [[ "$STAGE" == "reads-ready" ]]') :]
    assert "cockpit_compose ps" in reads_ready
    # The status is reported after the Cockpit has been started.
    assert reads_ready.index("cockpit_start ") < reads_ready.index("cockpit_compose ps")


def test_readme_documents_the_cockpit_compose_surface():
    """The split must be discoverable, including for manual operator use."""
    assert "docker-compose.cockpit.yml" in README
    assert "interpolat" in README.lower()
    assert "docker compose" in README.lower()


def test_ci_syntax_checks_the_changed_shell_and_renders_compose():
    """Shell syntax and Compose rendering are both gated in CI."""
    workflow = (ROOT / ".github/workflows/pytest.yml").read_text(encoding="utf-8")
    assert "bash -n deploy/analytics/bootstrap-opip-data-platform.sh" in workflow
    assert "docker compose config -q" in workflow


# ---------------------------------------------------------------------------
# 15. Behavioural: no analytics-plane setting is needed, and no side effect occurs
# ---------------------------------------------------------------------------

_STUB_PREAMBLE = textwrap.dedent(
    """
    set -euo pipefail

    _log() { printf '%s\\n' "$*" >> "$OPIP_TEST_LOG"; }

    sleep() { :; }

    docker() {
      _log "docker $*"
      case "$*" in
        *canonical_replica*resolve*)
          if [[ "${OPIP_TEST_RESOLVE_RC:-0}" != "0" ]]; then
            return "${OPIP_TEST_RESOLVE_RC}"
          fi
          printf '%s/generations/%s\\n' "$COCKPIT_REPLICA_CONTAINER_ROOT" "$OPIP_TEST_GENERATION" ;;
        *canonical_replica*verify*)
          return "${OPIP_TEST_VERIFY_RC:-0}" ;;
      esac
      case "${1:-}" in
        inspect) printf '%s\\n' "${OPIP_TEST_HEALTH:-healthy}" ;;
        port) printf '0.0.0.0:8000\\n-> 127.0.0.1:8000\\n' ;;
      esac
      return 0
    }
    ss() {
      printf 'State Recv-Q Send-Q Local Address:Port Peer Address:Port\\n'
      printf 'LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:*\\n'
    }
    install() { _log "install $*"; return 0; }
    chown() { return 0; }

    analytics_host_lock() { _log "analytics_host_lock"; return 0; }
    sync_release_checkout() { _log "sync_release_checkout"; return 0; }
    guard_no_trading_credentials() { _log "guard_no_trading_credentials"; return 0; }
    require_uri_unreserved_password() { _log "require_uri_unreserved_password $1"; return 0; }
    require_grafana_verify_full() { _log "require_grafana_verify_full"; return 0; }
    require_analytics_verify_full_dsn() { _log "require_analytics_verify_full_dsn $1"; return 0; }
    write_grafana_env_file() { _log "write_grafana_env_file"; return 0; }
    validate_postgres_tls_key() { _log "validate_postgres_tls_key"; return 0; }
    validate_promotion_evidence() { _log "validate_promotion_evidence"; return 0; }
    wait_for_postgres() { _log "wait_for_postgres"; return 0; }
    admin_run() { _log "admin_run $*"; return 0; }
    systemctl() { _log "systemctl $*"; return 0; }
    require_stage() { _log "require_stage $1"; return 0; }
    """
)

_EXTRACTED = (
    "write_state",
    "write_cockpit_state",
    "write_cockpit_env_file",
    "compose",
    "cockpit_compose",
    "cockpit_preflight",
    "cockpit_build_image",
    "cockpit_replica_root",
    "cockpit_verify_replica",
    "cockpit_wait_healthy",
    "cockpit_start",
    "cockpit_deploy_verified",
)

_STUBBED = (
    "analytics_host_lock",
    "sync_release_checkout",
    "guard_no_trading_credentials",
    "require_uri_unreserved_password",
    "require_grafana_verify_full",
    "require_analytics_verify_full_dsn",
    "write_grafana_env_file",
    "validate_postgres_tls_key",
    "validate_promotion_evidence",
    "wait_for_postgres",
    "admin_run",
    "systemctl",
    "require_stage",
)


def _remove_function(text: str, name: str) -> str:
    marker = f"{name}() {{"
    if marker not in text:
        return text
    return text.replace(_extract_function(text, name), "")


class _Harness:
    """Runs the real bootstrap control flow with host effects stubbed."""

    def __init__(self, tmp_path: Path, stage: str) -> None:
        self.tmp_path = tmp_path
        self.stage = stage
        self.state_root = tmp_path / "state"
        (self.state_root / "config").mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_root / "rollout.env"
        self.cockpit_state_file = self.state_root / "cockpit-ready.env"
        self.cockpit_env_file = tmp_path / "opip-cockpit.env"
        self.compose_file = tmp_path / "docker-compose.yml"
        self.compose_file.write_text("services: {}\n", encoding="utf-8")
        self.cockpit_compose_file = tmp_path / "docker-compose.cockpit.yml"
        self.cockpit_compose_file.write_text("services: {}\n", encoding="utf-8")
        self.session_env_file = tmp_path / "interpolation.env"
        # The sealed analytics env file is the source of the Cockpit's own settings, so it
        # must carry the secret as well as the three non-secret Cockpit values.
        self.session_env_file.write_text(
            "OPIP_COCKPIT_SECRET=test-secret\n"
            "OPIP_COCKPIT_BIND_ADDRESS=127.0.0.1\n"
            "OPIP_COCKPIT_HOST_PORT=8000\n"
            "OPIP_COCKPIT_HTTP_PORT=8000\n",
            encoding="utf-8",
        )
        self.replica_parent = tmp_path / "canonical-replica"
        (self.replica_parent / "generations" / GENERATION_ID).mkdir(
            parents=True, exist_ok=True
        )
        (self.replica_parent / "current").write_text(GENERATION_ID, encoding="utf-8")
        self.log = tmp_path / "calls.log"
        self.log.write_text("", encoding="utf-8")

    def seed_state(self, **values: str) -> None:
        self.state_file.write_text(
            "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8"
        )

    def build_script(self) -> str:
        script = textwrap.dedent(
            f"""
            TARGET_SHA={TARGET_SHA}
            STAGE={self.stage}
            COMPOSE={self.compose_file}
            COCKPIT_COMPOSE={self.cockpit_compose_file}
            APP_ROOT={self.tmp_path / "app"}
            ENV_FILE={self.session_env_file}
            COCKPIT_ENV_FILE={self.cockpit_env_file}
            STATE_ROOT={self.state_root}
            STATE_FILE={self.state_file}
            COCKPIT_STATE_FILE={self.cockpit_state_file}
            COCKPIT_REPLICA_PARENT_ROOT={self.replica_parent}
            COCKPIT_REPLICA_CONTAINER_ROOT=/app/canonical-replica
            COCKPIT_STAGE=cockpit-ready
            OPIP_PRODUCTION_PRIVATE_CIDR=10.116.0.2/32
            now_epoch="$(date -u +%s)"
            """
        )
        for name in _EXTRACTED:
            script += "\n" + _extract_function(BOOTSTRAP, name)

        region = BOOTSTRAP[BOOTSTRAP.index('if [[ "$STAGE" == "$COCKPIT_STAGE" ]]; then') :]
        for name in _EXTRACTED + _STUBBED:
            region = _remove_function(region, name)
        assert "flock" not in region
        assert "git -C" not in region
        return script + "\n" + _STUB_PREAMBLE + "\n" + region

    def child_environment(self) -> dict[str, str]:
        """The child environment: both planes' variables removed.

        Removing the Cockpit variables as well as the analytics ones means the sealed env
        file is their only source, so the harness cannot pass because a value happened to
        be exported by CI or the developer shell.
        """
        environment = dict(os.environ)
        for name in ANALYTICS_PLANE_VARS + ANALYTICS_PLANE_EXTRAS + tuple(COCKPIT_VARS):
            environment.pop(name, None)
        environment.pop("OPIP_DEPLOYED_SHA", None)
        environment["OPIP_TEST_LOG"] = str(self.log)
        environment["OPIP_TEST_GENERATION"] = GENERATION_ID
        return environment

    def run(self) -> subprocess.CompletedProcess[str]:
        harness = self.tmp_path / "harness.sh"
        harness.write_text(self.build_script(), encoding="utf-8")

        return subprocess.run(
            ["bash", str(harness)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=self.child_environment(),
            check=False,
        )

    def calls(self) -> str:
        return self.log.read_text(encoding="utf-8")


requires_bash = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason="the behavioural harness executes the real control flow under bash",
)


@requires_bash
def test_cockpit_ready_uses_the_cockpit_compose_surface_end_to_end(tmp_path: Path):
    """Executed proof that no Cockpit operation interpolates the shared analytics file.

    Run with every analytics-plane variable absent from the process environment, which is
    the condition that failed on the host.
    """
    harness = _Harness(tmp_path, "cockpit-ready")
    result = harness.run()
    assert result.returncode == 0, result.stderr
    calls = harness.calls()

    cockpit_surface = str(harness.cockpit_compose_file)
    analytics_surface = str(harness.compose_file)

    # Build, start and status all name the Cockpit-only file.
    for operation in ("build opip-cockpit", "up -d opip-cockpit", " ps"):
        matching = [line for line in calls.splitlines() if operation in line]
        assert matching, operation
        assert all(cockpit_surface in line for line in matching), matching

    # The shared analytics file is never named anywhere on this path.
    assert analytics_surface not in calls

    # No analytics-plane effect ran, and the verified marker is intact.
    for effect in ("opip-postgres", "opip-shipper", "opip-grafana", "admin_run"):
        assert effect not in calls, effect
    assert "cockpit_replica_verified=verified" in result.stdout


@requires_bash
def test_reads_ready_uses_each_compose_surface_for_its_own_plane(tmp_path: Path):
    """`reads-ready` keeps the shared surface for PostgreSQL and the Cockpit surface for
    the Cockpit, so each file is used only by its intended owner."""
    harness = _Harness(tmp_path, "reads-ready")
    harness.seed_state(SHIPPER_STARTED_AT_UTC="2020-01-01T00:00:00Z")
    result = harness.run()
    assert result.returncode == 0, result.stderr
    calls = harness.calls()

    cockpit_surface = str(harness.cockpit_compose_file)
    analytics_surface = str(harness.compose_file)

    cockpit_lines = [
        line
        for line in calls.splitlines()
        if "opip-cockpit" in line and "--env-file" in line
    ]
    assert cockpit_lines
    assert all(cockpit_surface in line for line in cockpit_lines), cockpit_lines

    postgres_lines = [
        line
        for line in calls.splitlines()
        if "opip-postgres" in line and "--env-file" in line
    ]
    assert postgres_lines
    assert all(analytics_surface in line for line in postgres_lines), postgres_lines

    # Historical evidence is written; Cockpit evidence is not (the replica was not
    # verified on this path).
    assert "READS_READY_SHA" in harness.state_file.read_text(encoding="utf-8")
    assert not harness.cockpit_state_file.exists()


def test_harness_sealed_env_file_carries_every_cockpit_setting(tmp_path: Path):
    """The harness's sealed env file must satisfy bootstrap's Cockpit allowlist.

    `write_cockpit_env_file` fails closed (exit 78) on a missing key, so a harness whose
    env file omits any Cockpit setting aborts before the behaviour under test. Deriving
    the keys from bootstrap keeps this honest as the allowlist evolves.
    """
    writer = _extract_function(BOOTSTRAP, "write_cockpit_env_file")
    keys_start = writer.index("local -a keys=(") + len("local -a keys=(")
    allowlist_body = writer[keys_start : writer.index(")", keys_start)]
    required = set(re.findall(r"^\s*(OPIP_[A-Z_]+)\s*$", allowlist_body, flags=re.M))
    assert required == COCKPIT_VARS, required

    seeded = _Harness(tmp_path, "cockpit-ready").session_env_file.read_text(
        encoding="utf-8"
    )
    seeded_keys = {
        line.split("=", 1)[0] for line in seeded.splitlines() if "=" in line
    }
    assert required <= seeded_keys, required - seeded_keys


def test_harness_defines_every_variable_its_code_paths_reference(tmp_path: Path):
    """The assembled harness must not reference a variable it never defines.

    Under `set -u` a missing constant aborts the harness at run time, and the harness is
    only executed where bash exists, so this static check runs everywhere and keeps that
    class of defect out of CI.
    """
    for stage in ("cockpit-ready", "reads-ready"):
        script = _Harness(tmp_path / stage, stage).build_script()
        assigned = set(re.findall(r"^[ \t]*([A-Z_][A-Z0-9_]*)=", script, flags=re.M))
        allowed = {
            "OPIP_TEST_LOG",
            "OPIP_TEST_GENERATION",
            "OPIP_TEST_RESOLVE_RC",
            "OPIP_TEST_VERIFY_RC",
            "OPIP_TEST_HEALTH",
            # Read from the state file at run time.
            "EMPTY_STARTED_AT_UTC",
            "EMPTY_DEPLOY_COUNT",
            "EMPTY_LAST_SHA",
            "SHIPPER_STARTED_AT_UTC",
            "SHIPPER_SHA",
            "DEPLOYED_SHA",
            # Evidence paths referenced by PostgreSQL stage branches that this harness
            # embeds but never executes.
            "OFFHOST_EVIDENCE",
            "RESTORE_EVIDENCE",
            "ROLLBACK_EVIDENCE",
            "POSTGRES_TLS_CA",
            "POSTGRES_TLS_CERT",
            "POSTGRES_TLS_KEY",
        }
        missing = []
        for match in re.finditer(r"\$\{?([A-Z_][A-Z0-9_]*)", script):
            if script[match.end() : match.end() + 1] == ":":
                continue
            name = match.group(1)
            if name not in assigned and name not in allowed:
                missing.append(name)
        assert not missing, f"undefined variables for {stage}: {sorted(set(missing))}"


def test_harness_env_does_not_inherit_cockpit_variables(tmp_path: Path):
    """The harness child environment must exclude both planes' variables.

    Tested through the builder `run()` uses, so the claim is about the environment the
    control flow actually sees rather than about the source text.
    """
    harness = _Harness(tmp_path, "cockpit-ready")
    child = harness.child_environment()
    for name in ANALYTICS_PLANE_VARS + tuple(COCKPIT_VARS) + ("OPIP_DEPLOYED_SHA",):
        assert name not in child, name
    # Only the harness's own bookkeeping is added.
    assert child["OPIP_TEST_LOG"] == str(harness.log)


def test_harness_embeds_the_real_control_flow(tmp_path: Path):
    """Assembly is verified everywhere, including hosts without bash."""
    script = _Harness(tmp_path, "cockpit-ready").build_script()
    assert "cockpit_compose() {" in script
    assert "COCKPIT_COMPOSE=" in script
    assert 'if [[ "$STAGE" == "$COCKPIT_STAGE" ]]; then' in script
    assert "docker() {" in script
    assert "flock" not in script
