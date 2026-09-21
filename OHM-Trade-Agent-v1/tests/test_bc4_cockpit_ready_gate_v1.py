"""PR-B/C-4E: `cockpit-ready` decouples the replica Cockpit from historical reads.

The problem this guards against: the read-only B/C-4 Cockpit reads only the verified
canonical replica and needs no PostgreSQL historical read, yet it could previously only
be started by the `reads-ready` stage - which correctly waits on a seven-day shipper
soak. An otherwise valid operational Cockpit was therefore gated on PostgreSQL
historical readiness it does not depend on.

The fix adds an owner-gated `cockpit-ready` stage for the replica-backed Cockpit. It
must:

* be admitted only through the existing owner/issue-#64/exact-main/exact-SHA-CI path;
* write its own `COCKPIT_READY_*` evidence and never `READS_READY_*`;
* verify the installed replica with the EXISTING verifier, failing closed on missing,
  unreadable, structurally invalid, SHA-mismatched or stale replicas - and, because the
  replica root is a repository of `generations/<id>` selected by a `current` pointer,
  verify the *resolved generation*, never the parent directory;
* build and use the exact target Cockpit image, and wait for health before proving
  reachability;
* reject order-capable trading credentials and keep a distinct Cockpit secret;
* stay host-loopback only, never publicly bound;
* perform NO PostgreSQL work and depend on no PostgreSQL/Grafana setting - enforced by
  dispatching the Cockpit stage BEFORE the PostgreSQL/Grafana plane rather than by
  scattered guards, so a Cockpit deployment cannot require unrelated PostgreSQL
  configuration or mutate PostgreSQL host state;
* leave the seven-day soak, every `reads-ready` gate, and `reads-ready`'s independence
  from the replica plane exactly as they were;
* be idempotent, commit its readiness record atomically, and never leave a false
  `COCKPIT_READY_*` marker after a failure.

Two kinds of proof are used. Structural assertions pin the dispatch and the shared
primitives. A bash harness additionally *executes* the real control flow with host-side
effects stubbed, so the isolation, the resolved-generation wiring and the start ordering
are proven behaviourally. The harness needs `bash`; CI (ubuntu-latest) always has it,
mirroring the existing root-only replica E2E which is likewise executed in CI.
"""

from __future__ import annotations

import ast
import os
import re
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO.parent

BOOTSTRAP = (REPO / "deploy/analytics/bootstrap-opip-data-platform.sh").read_text(
    encoding="utf-8"
)
RUNNER = (REPO / "deploy/analytics/run-gated-stage.sh").read_text(encoding="utf-8")
WORKFLOW = (ROOT / ".github/workflows/deploy-analytics.yml").read_text(encoding="utf-8")
COMPOSE_TEXT = (REPO / "deploy/analytics/docker-compose.yml").read_text(encoding="utf-8")
ENV_EXAMPLE = (REPO / "deploy/analytics/env.example").read_text(encoding="utf-8")
README = (REPO / "deploy/analytics/README.md").read_text(encoding="utf-8")

TARGET_SHA = "5a71bd49afe120280755b6c1eb4bebdda2de1766"
OTHER_SHA = "0123456789abcdef0123456789abcdef01234567"
GENERATION_ID = "a" * 64

WORKFLOW_STAGES = (
    "prepare",
    "activate",
    "empty",
    "backup",
    "restore-drill",
    "offhost-verified",
    "rollback-verified",
    "backfill",
    "shipper",
    "reads-ready",
    "cockpit-ready",
)
BOOTSTRAP_STAGES = ("empty", "backfill", "shipper", "reads-ready", "cockpit-ready")

REPLICA_PARENT = "/var/lib/opip-learning/canonical-replica"
REPLICA_CONTAINER_ROOT = "/app/canonical-replica"

# Effects belonging to the PostgreSQL/Grafana plane. `cockpit-ready` must reach none of
# them, and must not require any of their settings.
POSTGRES_PLANE_ONLY = (
    "require_uri_unreserved_password",
    "require_grafana_verify_full",
    "require_analytics_verify_full_dsn",
    "write_grafana_env_file",
    "validate_postgres_tls_key",
    "opip-postgres",
    "opip-shipper",
    "opip-grafana",
    "wait_for_postgres",
    "validate_promotion_evidence",
    "admin_run",
    "systemctl",
    "/etc/systemd/system/",
    "pg_hba.conf",
    "STATE_ROOT/postgres",
    "OPIP_PRODUCTION_PRIVATE_CIDR",
    "1800 * 1024",
    "write_state ",
)

# The subset the bootstrap itself performs in the PostgreSQL plane. Grafana is started
# by the operator, not by this script, so it is only ever a must-not-reach marker.
POSTGRES_PLANE_PRESENT = tuple(
    marker for marker in POSTGRES_PLANE_ONLY if marker != "opip-grafana"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_function(text: str, name: str) -> str:
    """Return a top-level bash function definition by name."""
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _extract_block(text: str, guard: str) -> tuple[int, int, str]:
    """Return the extent of `if <guard>; then ... fi`, matched by nesting depth.

    Every guard body here uses the multi-line `if ... fi` form, and `elif` does not open
    a block, so depth counting is sufficient and cannot truncate the block early.
    """
    lines = text.splitlines(keepends=True)
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
                end = index + 1
                return (
                    sum(len(line) for line in lines[:start]),
                    sum(len(line) for line in lines[:end]),
                    "".join(lines[start:end]),
                )
    raise AssertionError(f"unterminated guard: {guard}")


DISPATCH_GUARD = 'if [[ "$STAGE" == "$COCKPIT_STAGE" ]]; then'


def _dispatch() -> str:
    """The Cockpit-only dispatch block: everything `cockpit-ready` executes."""
    return _extract_block(BOOTSTRAP, DISPATCH_GUARD)[2]


def _postgres_plane() -> str:
    """Everything after the Cockpit dispatch: the PostgreSQL/Grafana plane."""
    return BOOTSTRAP[_extract_block(BOOTSTRAP, DISPATCH_GUARD)[1] :]


def _state_file(path: Path) -> dict[str, str]:
    """Parse a `KEY=value` evidence file written with `printf '%s=%q'`."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            tokens = shlex.split(line)
        except ValueError:
            continue
        for token in tokens:
            key, separator, value = token.partition("=")
            if separator:
                values[key] = value
    return values


def _strip_comments(text: str) -> str:
    """Drop comment lines, so prose about an omission is not read as the omission."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _workflow_stage_grammar() -> re.Pattern[str]:
    """Translate the workflow's POSIX ERE command grammar into a Python pattern."""
    match = re.search(r"=~\s*(\^[^\n]*?\$)\s*\]\]", WORKFLOW)
    assert match, "could not locate the analytics command grammar in the workflow"
    posix = match.group(1)
    assert posix.startswith("^/deploy-analytics") and posix.endswith("$")
    return re.compile(posix.replace("[[:space:]]", r"\s"))


def _workflow_stage_list() -> list[str]:
    match = re.search(
        r"=~\s*\^/deploy-analytics[^\n]+?\(([a-z|-]+)\)\[\[:space:\]\]\*\$", WORKFLOW
    )
    assert match, "could not locate the stage alternation in the workflow grammar"
    return match.group(1).split("|")


def _shell_stage_vocabulary(script: str) -> tuple[str, ...]:
    match = re.search(r'case "\$STAGE" in\n\s*([a-z|_-]+)\)', script)
    assert match, "could not locate the stage case arm"
    return tuple(match.group(1).split("|"))


def _cockpit_service() -> dict:  # type: ignore[type-arg]
    return yaml.safe_load(COMPOSE_TEXT)["services"]["opip-cockpit"]


# ---------------------------------------------------------------------------
# 1-4: command grammar and admission path
# ---------------------------------------------------------------------------


def test_workflow_grammar_accepts_cockpit_ready():
    """The owner-gated command grammar must accept the new stage."""
    grammar = _workflow_stage_grammar()
    assert grammar.match(f"/deploy-analytics {TARGET_SHA} cockpit-ready")
    # Whitespace handling is unchanged from the existing grammar.
    assert grammar.match(f"/deploy-analytics   {TARGET_SHA}   cockpit-ready")


def test_malformed_commands_remain_rejected():
    """Adding a stage must not loosen the grammar in any other way."""
    grammar = _workflow_stage_grammar()
    rejected = [
        f"/deploy-analytics {TARGET_SHA} cockpit-read",  # not a stage
        f"/deploy-analytics {TARGET_SHA} cockpit",  # prefix only
        f"/deploy-analytics {TARGET_SHA} cockpit-ready-",  # suffix only
        f"/deploy-analytics {TARGET_SHA} cockpit-ready extra",  # trailing token
        "/deploy-analytics cockpit-ready",  # no sha
        f"/deploy-analytics {TARGET_SHA} cockpit-ready\n/deploy-analytics x",  # newline
        f"/deploy-analytics {TARGET_SHA.upper()} cockpit-ready",  # uppercase sha
        f"/deploy-analytics {TARGET_SHA[:-1]} cockpit-ready",  # short sha
        f"/deploy-analytics {TARGET_SHA}0 cockpit-ready",  # long sha
        f"x/deploy-analytics {TARGET_SHA} cockpit-ready",  # prefix junk
        f"/deploy-analyics {TARGET_SHA} cockpit-ready",  # typo
        f"/deploy-analytics {TARGET_SHA}",  # no stage
    ]
    for command in rejected:
        assert not grammar.match(command), f"should be rejected: {command!r}"


def test_stage_vocabulary_agrees_across_workflow_runner_and_bootstrap():
    """All three layers must name exactly the same stages, so none can drift."""
    assert tuple(_workflow_stage_list()) == WORKFLOW_STAGES
    assert tuple(_shell_stage_vocabulary(RUNNER)) == WORKFLOW_STAGES
    assert tuple(_shell_stage_vocabulary(BOOTSTRAP)) == BOOTSTRAP_STAGES


def test_only_the_owner_issue_64_pathway_can_deploy_cockpit_ready():
    """The new stage is reachable only through the existing owner-gated pathway."""
    job_condition = WORKFLOW[WORKFLOW.index("if: >-") : WORKFLOW.index("runs-on:")]
    assert "github.event.issue.number == 64" in job_condition
    assert "github.event.comment.user.login == github.repository_owner" in job_condition
    assert "github.event.comment.author_association == 'OWNER'" in job_condition
    assert "startsWith(github.event.comment.body, '/deploy-analytics ')" in job_condition
    assert WORKFLOW.count("if: >-") == 1
    assert "environment: analytics-production" in WORKFLOW
    assert "workflow_dispatch" not in WORKFLOW
    assert "pull_request_target" not in WORKFLOW


def test_cockpit_ready_requires_exact_current_main_and_exact_sha_ci():
    """No stage, including cockpit-ready, may skip the exact-SHA admission gates."""
    assert "Require target to equal current main" in WORKFLOW
    assert 'test "$TARGET_SHA" = "$MAIN_SHA"' in WORKFLOW
    assert "Require successful exact-SHA CI" in WORKFLOW
    assert "--commit \"$TARGET_SHA\"" in WORKFLOW
    assert 'test "$RESULT" = $\'completed\\tsuccess\'' in WORKFLOW
    order = WORKFLOW.index("Require target to equal current main")
    assert order < WORKFLOW.index("Check out exact approved release")
    assert order < WORKFLOW.index("Run exactly one gated analytics stage")
    assert "if:" not in WORKFLOW[
        WORKFLOW.index("Require target to equal current main") :
        WORKFLOW.index("Require successful exact-SHA CI")
    ]


# ---------------------------------------------------------------------------
# 2, 17-21: the cockpit-ready path performs no PostgreSQL work and depends on no
# PostgreSQL/Grafana setting
# ---------------------------------------------------------------------------


def test_cockpit_dispatch_runs_before_the_whole_postgres_plane():
    """Isolation is structural: the Cockpit stage returns before PostgreSQL is reached."""
    dispatch = _dispatch()
    dispatch_lines = [line.strip() for line in dispatch.rstrip().splitlines()]
    assert dispatch_lines[-1] == "fi"
    assert dispatch_lines[-2] == "exit 0"
    # The dispatch must precede every PostgreSQL/Grafana precondition and side effect.
    plane = _postgres_plane()
    for effect in POSTGRES_PLANE_ONLY:
        assert effect not in dispatch, f"cockpit-ready reaches {effect}"
    for effect in POSTGRES_PLANE_PRESENT:
        assert effect in plane, f"{effect} must live in the PostgreSQL plane"


def test_cockpit_ready_requires_no_postgresql_or_grafana_setting():
    """A Cockpit deployment must not fail on unrelated PostgreSQL configuration."""
    plane = _postgres_plane()
    # The PostgreSQL/Grafana validations are unconditional from here on, so they are
    # reached only by the PostgreSQL stages.
    for validation in (
        "require_uri_unreserved_password OPIP_POSTGRES_ADMIN_PASSWORD",
        "require_uri_unreserved_password OPIP_SHIPPER_PASSWORD",
        "require_grafana_verify_full",
        "require_analytics_verify_full_dsn OPIP_ANALYTICS_ADMIN_DATABASE_URL",
        "require_analytics_verify_full_dsn OPIP_ANALYTICS_DATABASE_URL",
        "write_grafana_env_file",
    ):
        assert validation in plane
    # The capacity floor is a PostgreSQL precondition, not a Cockpit one.
    assert plane.index("1800 * 1024") > plane.index("analytics_host_lock")
    assert "1800 * 1024" not in _dispatch()


def test_cockpit_ready_writes_no_postgres_rollout_evidence():
    """`DEPLOYED_SHA` and `READS_READY_*` stay exclusive to the PostgreSQL plane."""
    assert BOOTSTRAP.count('write_state DEPLOYED_SHA "$TARGET_SHA"') == 1
    assert BOOTSTRAP.count("READS_READY_AT_UTC") == 1
    assert BOOTSTRAP.count("READS_READY_SHA") == 1
    plane = _postgres_plane()
    assert 'write_state DEPLOYED_SHA "$TARGET_SHA"' in plane
    assert "READS_READY_AT_UTC" in plane
    assert "READS_READY_SHA" in plane
    assert "READS_READY" not in _dispatch()
    assert "write_state " not in _dispatch()


def test_cockpit_evidence_has_its_own_file_and_is_committed_atomically():
    """Cockpit readiness must not touch rollout.env, and must be written as one record."""
    assert 'STATE_FILE="$STATE_ROOT/rollout.env"' in BOOTSTRAP
    assert 'COCKPIT_STATE_FILE="$STATE_ROOT/cockpit-ready.env"' in BOOTSTRAP

    write_cockpit_state = _extract_function(BOOTSTRAP, "write_cockpit_state")
    assert "$STATE_FILE" not in write_cockpit_state
    assert 'mktemp "$COCKPIT_STATE_FILE.XXXXXX"' in write_cockpit_state
    assert 'mv -f -- "$temporary" "$COCKPIT_STATE_FILE"' in write_cockpit_state
    # Both keys are written into the temporary file and committed by a single rename, so
    # an interruption can never leave a new timestamp paired with an old release.
    assert write_cockpit_state.count(' > "$temporary"') == 1
    assert write_cockpit_state.count('>> "$temporary"') == 1
    assert write_cockpit_state.count("mv -f") == 1
    assert "COCKPIT_READY_AT_UTC" in write_cockpit_state
    assert "COCKPIT_READY_SHA" in write_cockpit_state

    # The readiness record is committed exactly once, with both values.
    cockpit_start = _extract_function(BOOTSTRAP, "cockpit_start")
    assert cockpit_start.count("write_cockpit_state ") == 1
    assert "write_cockpit_state \"$TARGET_SHA\"" in cockpit_start


def test_reads_ready_keeps_the_seven_day_soak_and_all_historical_gates():
    """The historical path must be unchanged: soak, gates, then READS_READY_*."""
    branch = BOOTSTRAP[
        BOOTSTRAP.index('elif [[ "$STAGE" == "reads-ready" ]]') :
    ]
    assert "7 * 86400" in branch
    assert "shipper must soak for seven days" in branch
    for gate in (
        "migrations migrate",
        "migrations sync-required-streams",
        "reconcile",
        "health --require-ready",
    ):
        assert gate in branch
    assert branch.index("7 * 86400") < branch.index("READS_READY_AT_UTC")
    assert branch.index("health --require-ready") < branch.index("READS_READY_AT_UTC")
    # The soak evidence itself is still recorded by the shipper stage.
    assert 'write_state SHIPPER_STARTED_AT_UTC' in BOOTSTRAP


def test_reads_ready_does_not_depend_on_the_replica_plane():
    """Historical readiness must not be blocked by an independent Cockpit dependency."""
    branch = BOOTSTRAP[BOOTSTRAP.index('elif [[ "$STAGE" == "reads-ready" ]]') :]
    assert "cockpit_verify_replica" not in branch
    assert "cockpit_deploy_verified" not in branch
    # ...but it does start the Cockpit, through the shared start primitive.
    assert "cockpit_start" in branch
    assert "cockpit_build_image" in branch

    # Replica verification belongs to the Cockpit stage only, and precedes the start.
    verified_flow = _extract_function(BOOTSTRAP, "cockpit_deploy_verified")
    code = _strip_comments(verified_flow)
    assert "cockpit_verify_replica" in code
    assert code.index("cockpit_verify_replica") < code.index("cockpit_start")


def test_both_stages_share_one_cockpit_start_primitive():
    """One implementation of start/wait/preflight/evidence, used by both stages."""
    assert BOOTSTRAP.count("compose up -d opip-cockpit") == 1
    assert BOOTSTRAP.count("\n  cockpit_preflight\n") == 1
    assert BOOTSTRAP.count("write_cockpit_env_file \"$replica_root\"") == 1
    cockpit_start = _extract_function(BOOTSTRAP, "cockpit_start")
    assert "cockpit_wait_healthy" in cockpit_start
    assert "cockpit_preflight" in cockpit_start
    # Start ordering: env, up, healthy, reachability proof, then readiness.
    assert cockpit_start.index("write_cockpit_env_file") < cockpit_start.index(
        "compose up -d opip-cockpit"
    )
    assert cockpit_start.index("compose up -d opip-cockpit") < cockpit_start.index(
        "cockpit_wait_healthy"
    )
    assert cockpit_start.index("cockpit_wait_healthy") < cockpit_start.index(
        "cockpit_preflight"
    )
    assert cockpit_start.index("cockpit_preflight") < cockpit_start.index(
        "write_cockpit_state "
    )


# ---------------------------------------------------------------------------
# 5-8, 12: replica resolution, verification and the exact image
# ---------------------------------------------------------------------------


def test_replica_verification_targets_the_resolved_generation_not_the_parent():
    """The replica root is a repository of generations, not a bundle.

    Installed bundles live under ``generations/<id>`` and are selected by a plain-text
    ``current`` pointer, so the parent holds no manifest and no canonical database.
    Verifying or reading the parent would look for a bundle where none exists.
    """
    resolve = _extract_function(BOOTSTRAP, "cockpit_replica_root")
    # Resolution is delegated to the existing resolver rather than reimplemented.
    assert "python -m app.opip.learning.canonical_replica resolve" in resolve
    assert '--host-root "$COCKPIT_REPLICA_CONTAINER_ROOT"' in resolve
    # The parent repository is never an acceptable resolution result.
    assert '"$resolved" != "$COCKPIT_REPLICA_CONTAINER_ROOT"' in resolve

    verify = _extract_function(BOOTSTRAP, "cockpit_verify_replica")
    assert 'resolved="$(cockpit_replica_root)"' in verify
    # The verifier is pointed at the resolved generation, never the parent root.
    assert '--root "$resolved"' in verify
    assert "--root \"$COCKPIT_REPLICA_CONTAINER_ROOT\"" not in verify
    # A replica that cannot be resolved fails closed before any container start.
    assert "exit 69" in verify


def test_cockpit_reads_the_resolved_generation_through_its_env_file():
    """The Cockpit process must be told which generation to read."""
    write_env = _extract_function(BOOTSTRAP, "write_cockpit_env_file")
    assert 'local replica_root="${1:-$COCKPIT_REPLICA_CONTAINER_ROOT}"' in write_env
    assert "OPIP_CANONICAL_REPLICA_ROOT=%s" in write_env
    # The compose service must not pin the parent over the derived value.
    environment = _cockpit_service()["environment"]
    assert "OPIP_CANONICAL_REPLICA_ROOT" not in environment
    assert environment == {
        "OPIP_COCKPIT_HTTP_PORT": "${OPIP_COCKPIT_HTTP_PORT:-8000}",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def test_replica_probe_container_is_offline_read_only_and_target_pinned():
    """The probe must be the existing verifier, isolated and release-pinned."""
    verify = _extract_function(BOOTSTRAP, "cockpit_verify_replica")
    resolve = _extract_function(BOOTSTRAP, "cockpit_replica_root")
    for probe in (verify, resolve):
        assert "--network none" in probe
        assert "--read-only" in probe
        assert "--cap-drop ALL" in probe
        assert "--security-opt no-new-privileges:true" in probe
        assert "opip-data-platform:${TARGET_SHA}" in probe
        # The replica is mounted read-only, so probing cannot mutate evidence.
        assert '-v "$COCKPIT_REPLICA_PARENT_ROOT:$COCKPIT_REPLICA_CONTAINER_ROOT:ro"' in probe
    assert "python -m app.opip.learning.canonical_replica verify" in verify
    assert '--release-sha "$TARGET_SHA"' in verify
    # The freshness bound is the existing contract default and cannot be widened here:
    # the flag is absent from the code itself (prose explaining that is a comment).
    assert "--max-age-seconds" not in _strip_comments(verify)
    assert "exit 69" in verify


def test_replica_paths_match_the_compose_mount():
    """The probe and the container must read the same replica, or readiness is a lie."""
    assert f'COCKPIT_REPLICA_PARENT_ROOT="{REPLICA_PARENT}"' in BOOTSTRAP
    assert f'COCKPIT_REPLICA_CONTAINER_ROOT="{REPLICA_CONTAINER_ROOT}"' in BOOTSTRAP
    assert _cockpit_service()["volumes"] == [
        f"{REPLICA_PARENT}:{REPLICA_CONTAINER_ROOT}:ro"
    ]


def test_cockpit_waits_for_health_before_proving_reachability():
    """`compose up -d` returns while the container is still starting."""
    wait = _extract_function(BOOTSTRAP, "cockpit_wait_healthy")
    assert ".State.Health" in wait
    assert "== \"healthy\"" in wait
    # The wait is bounded, and it fails closed with diagnostics.
    assert "seq 1 36" in wait
    assert "sleep 5" in wait
    assert "docker logs" in wait
    assert "exit 69" in wait


def test_cockpit_uses_the_exact_target_image():
    """A stale image from an earlier rollout must not be able to satisfy this stage."""
    assert "opip-data-platform:${OPIP_DEPLOYED_SHA:-local}" in COMPOSE_TEXT
    build = _extract_function(BOOTSTRAP, "cockpit_build_image")
    assert 'export OPIP_DEPLOYED_SHA="$TARGET_SHA"' in build
    assert build.index('export OPIP_DEPLOYED_SHA="$TARGET_SHA"') < build.index(
        "build opip-cockpit"
    )
    assert "docker compose -f \"$COMPOSE\" build opip-cockpit" in build
    assert "opip-shipper" not in build
    assert "opip-postgres" not in build


# ---------------------------------------------------------------------------
# 9-11: credentials and exposure
# ---------------------------------------------------------------------------


def test_cockpit_secret_stays_distinct_from_the_trading_secret():
    """The Cockpit gets its own read-only secret, never the trading operator secret."""
    assert "OPIP_COCKPIT_SECRET" in ENV_EXAMPLE
    assert "OPIP_COCKPIT_SECRET" in BOOTSTRAP
    assert 'COCKPIT_ENV_FILE="/etc/opip-cockpit.env"' in BOOTSTRAP
    assert "WEBHOOK_SECRET=/etc/opip-cockpit.env" not in BOOTSTRAP
    assert "OPIP_COCKPIT_SECRET=$WEBHOOK_SECRET" not in BOOTSTRAP
    guard = _extract_function(BOOTSTRAP, "guard_no_trading_credentials")
    for key in (
        "WEBHOOK_SECRET",
        "KRAKEN_API_KEY",
        "KRAKEN_API_SECRET",
        "TELEGRAM_BOT_TOKEN",
    ):
        assert key in guard
    # The credential boundary applies to the Cockpit stage too: it is enforced in the
    # shared prelude, before the dispatch.
    assert BOOTSTRAP.index("guard_no_trading_credentials\n") < BOOTSTRAP.index(
        DISPATCH_GUARD
    )
    # Only the Cockpit's own keys plus the derived replica root reach the container.
    write_env = _extract_function(BOOTSTRAP, "write_cockpit_env_file")
    assert "OPIP_COCKPIT_BIND_ADDRESS" in write_env
    assert "OPIP_COCKPIT_HOST_PORT" in write_env
    assert "OPIP_COCKPIT_HTTP_PORT" in write_env
    assert "WEBHOOK_SECRET" not in write_env
    assert "KRAKEN" not in write_env
    assert "TELEGRAM" not in write_env


def test_cockpit_is_published_only_on_host_loopback():
    """Raw Cockpit HTTP must remain loopback-only; a public bind is refused."""
    ports = _cockpit_service()["ports"]
    assert len(ports) == 1
    published = str(ports[0])
    assert published.startswith("${OPIP_COCKPIT_BIND_ADDRESS:-127.0.0.1}:")
    assert "0.0.0.0" not in published
    assert "[::]" not in published
    assert "OPIP_COCKPIT_BIND_ADDRESS=127.0.0.1" in ENV_EXAMPLE
    preflight = _extract_function(BOOTSTRAP, "cockpit_preflight")
    assert "OPIP_COCKPIT_BIND_ADDRESS must be host loopback" in preflight
    assert "bound on a public interface" in preflight


def test_cockpit_remains_read_only_and_get_only():
    """The new stage must not widen the Cockpit's read-only authority."""
    service = _cockpit_service()
    assert service["read_only"] is True
    assert "ALL" in service["cap_drop"]
    assert "no-new-privileges:true" in service["security_opt"]
    for forbidden in ("KRAKEN", "TELEGRAM", "WEBHOOK_SECRET"):
        assert forbidden not in COMPOSE_TEXT
    assert service["env_file"] == ["/etc/opip-cockpit.env"]
    # The trading app must not import or mount the Cockpit router. It mentions the
    # deliberate omission in a comment, so imports are checked structurally.
    tree = ast.parse((REPO / "app/main.py").read_text(encoding="utf-8"))
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not any("cockpit" in str(module) for module in imported)


# ---------------------------------------------------------------------------
# 24-25: documentation and no new infrastructure
# ---------------------------------------------------------------------------


def test_readme_documents_the_two_independent_readiness_planes():
    assert "cockpit-ready" in README
    for phrase in (
        "Two independent readiness planes",
        "Not involved",
        "seven-day",
        "loopback",
        "required for external access",
        "generations/<id>",
    ):
        assert phrase.lower() in README.lower(), f"README must document: {phrase}"


def test_readme_states_the_required_reverse_proxy_routes():
    for route in (
        "/cockpit",
        "/api/cockpit/overview",
        "/api/cockpit/trades",
        "/api/cockpit/trades/*",
    ):
        assert route in README
    assert "do **not** expose `OPIP_COCKPIT_HOST_PORT` to the" in README


def test_no_new_infrastructure_or_dependency_is_introduced():
    """The change must reuse the existing plane: no new service, proxy or database."""
    compose = yaml.safe_load(COMPOSE_TEXT)
    assert sorted(compose["services"]) == [
        "opip-cockpit",
        "opip-data-admin",
        "opip-grafana",
        "opip-postgres",
        "opip-shipper",
    ]
    for forbidden in ("nginx", "traefik", "caddy", "redis", "mongo"):
        assert forbidden not in COMPOSE_TEXT.lower()


def test_ci_still_syntax_checks_both_changed_shell_scripts():
    workflow = (ROOT / ".github/workflows/pytest.yml").read_text(encoding="utf-8")
    assert "bash -n deploy/analytics/bootstrap-opip-data-platform.sh" in workflow
    assert "bash -n deploy/analytics/run-gated-stage.sh" in workflow


# ---------------------------------------------------------------------------
# Behavioural harness: execute the real control flow with host effects stubbed
# ---------------------------------------------------------------------------

_STUB_PREAMBLE = textwrap.dedent(
    """
    set -euo pipefail

    _log() { printf '%s\\n' "$*" >> "$OPIP_TEST_LOG"; }

    # Never really sleep: the bounded health wait is exercised without its wall clock.
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
    compose() { _log "compose $*"; return 0; }
    ss() {
      printf 'State Recv-Q Send-Q Local Address:Port Peer Address:Port\\n'
      printf 'LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:*\\n'
    }
    install() { _log "install $*"; return 0; }
    chown() { return 0; }

    # PostgreSQL/Grafana plane: recorded, never executed. Being able to assert that
    # none of these were called is the point of the harness.
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

# The Cockpit stage executes the dispatch; the PostgreSQL stages fall through it into the
# plane below. Slicing at the dispatch therefore exercises the real routing for both.
_EXTRACTED_FUNCTIONS = (
    "write_state",
    "write_cockpit_state",
    "write_cockpit_env_file",
    "cockpit_preflight",
    "cockpit_build_image",
    "cockpit_replica_root",
    "cockpit_verify_replica",
    "cockpit_wait_healthy",
    "cockpit_start",
    "cockpit_deploy_verified",
)


class _Harness:
    """Runs the real bootstrap control flow for one stage with host effects stubbed."""

    def __init__(self, tmp_path: Path, stage: str, sha: str = TARGET_SHA) -> None:
        self.tmp_path = tmp_path
        self.stage = stage
        self.sha = sha
        self.state_root = tmp_path / "state"
        self.state_root.mkdir(parents=True, exist_ok=True)
        # The real script creates these with `install -d`, which the harness stubs.
        (self.state_root / "config").mkdir(exist_ok=True)
        self.state_file = self.state_root / "rollout.env"
        self.cockpit_state_file = self.state_root / "cockpit-ready.env"
        self.cockpit_env_file = tmp_path / "opip-cockpit.env"
        self.sealed_env_file = tmp_path / "opip-data-platform.env"
        self.sealed_env_file.write_text(
            "OPIP_COCKPIT_SECRET=test-secret\n"
            "OPIP_COCKPIT_BIND_ADDRESS=127.0.0.1\n"
            "OPIP_COCKPIT_HOST_PORT=8000\n"
            "OPIP_COCKPIT_HTTP_PORT=8000\n",
            encoding="utf-8",
        )
        self.replica_parent = tmp_path / "canonical-replica"
        (self.replica_parent / "generations" / GENERATION_ID).mkdir(parents=True)
        (self.replica_parent / "current").write_text(GENERATION_ID, encoding="utf-8")
        self.compose_file = tmp_path / "docker-compose.yml"
        self.compose_file.write_text("services: {}\n", encoding="utf-8")
        self.log = tmp_path / "calls.log"
        self.log.write_text("", encoding="utf-8")

    def seed_state(self, **values: str) -> None:
        self.state_file.write_text(
            "".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8"
        )

    def build_script(self) -> str:
        """Assemble the harness: real control flow, stubbed host-side effects."""
        script = textwrap.dedent(
            f"""
            TARGET_SHA={shlex.quote(self.sha)}
            STAGE={shlex.quote(self.stage)}
            COMPOSE={shlex.quote(str(self.compose_file))}
            APP_ROOT={shlex.quote(str(self.tmp_path / "app"))}
            ENV_FILE={shlex.quote(str(self.sealed_env_file))}
            COCKPIT_ENV_FILE={shlex.quote(str(self.cockpit_env_file))}
            STATE_ROOT={shlex.quote(str(self.state_root))}
            STATE_FILE={shlex.quote(str(self.state_file))}
            COCKPIT_STATE_FILE={shlex.quote(str(self.cockpit_state_file))}
            COCKPIT_REPLICA_PARENT_ROOT={shlex.quote(str(self.replica_parent))}
            COCKPIT_REPLICA_CONTAINER_ROOT={REPLICA_CONTAINER_ROOT}
            COCKPIT_STAGE=cockpit-ready
            OPIP_PRODUCTION_PRIVATE_CIDR=10.116.0.2/32
            now_epoch="$(date -u +%s)"
            """
        )
        for name in _EXTRACTED_FUNCTIONS:
            script += "\n" + _extract_function(BOOTSTRAP, name)

        start = BOOTSTRAP.index(DISPATCH_GUARD)
        region = BOOTSTRAP[start:]
        # The harness must exercise the control flow only: taking the analytics-plane
        # lock or driving git would be a real side effect of testing, so both are stubbed
        # and their bodies must not be embedded.
        assert "flock" not in region
        assert "exec 8>" not in region
        assert "git -C" not in region
        assert "\ncompose ps\n" in region
        return script + "\n" + _STUB_PREAMBLE + "\n" + region

    def run(
        self,
        *,
        verify_rc: str = "0",
        resolve_rc: str = "0",
        health: str = "healthy",
    ) -> subprocess.CompletedProcess[str]:
        harness = self.tmp_path / "harness.sh"
        harness.write_text(self.build_script(), encoding="utf-8")

        environment = dict(os.environ)
        environment["OPIP_TEST_LOG"] = str(self.log)
        environment["OPIP_TEST_VERIFY_RC"] = verify_rc
        environment["OPIP_TEST_RESOLVE_RC"] = resolve_rc
        environment["OPIP_TEST_HEALTH"] = health
        environment["OPIP_TEST_GENERATION"] = GENERATION_ID
        return subprocess.run(
            ["bash", str(harness)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            check=False,
        )

    def calls(self) -> str:
        return self.log.read_text(encoding="utf-8")


requires_bash = pytest.mark.skipif(
    shutil.which("bash") is None,
    reason=(
        "the behavioural harness executes the real bootstrap control flow under bash; "
        "CI (ubuntu-latest) always provides bash and runs this test, mirroring the "
        "root-only replica E2E which is likewise executed explicitly in CI"
    ),
)


def test_harness_embeds_the_real_control_flow(tmp_path: Path):
    """The harness must embed the real script rather than a paraphrase of it.

    Runs everywhere, including hosts without bash, so harness assembly is verified
    locally rather than only where the behavioural suite can execute.
    """
    for stage in ("cockpit-ready", "reads-ready"):
        script = _Harness(tmp_path / stage, stage).build_script()
        for name in _EXTRACTED_FUNCTIONS:
            assert f"{name}() {{" in script
        assert DISPATCH_GUARD in script
        assert 'elif [[ "$STAGE" == "reads-ready" ]]' in script
        assert "OPIP_TEST_LOG" in script
        # Host-side effects are stubbed and nothing external is touched.
        assert "docker() {" in script
        assert "systemctl() {" in script
        assert "flock" not in script
        assert "git -C" not in script


@requires_bash
class TestCockpitReadyBehaviour:
    """Behavioural proof of isolation, wiring and ordering."""

    def test_cockpit_ready_reaches_no_postgresql_plane_effect(self, tmp_path: Path):
        harness = _Harness(tmp_path, "cockpit-ready")
        result = harness.run()
        assert result.returncode == 0, result.stderr
        calls = harness.calls()

        # It did the Cockpit work.
        assert "build opip-cockpit" in calls
        assert "canonical_replica resolve" in calls
        assert "canonical_replica verify" in calls
        assert "compose up -d opip-cockpit" in calls
        assert "compose ps" in calls
        # And none of the PostgreSQL/Grafana plane ran or was even required.
        for effect in POSTGRES_PLANE_ONLY:
            assert effect not in calls, f"{effect} ran during cockpit-ready"
        assert "opip-postgres" not in calls
        assert "opip-shipper" not in calls
        assert "opip-grafana" not in calls

    def test_cockpit_ready_reads_the_resolved_generation(self, tmp_path: Path):
        """The Cockpit must be pointed at the generation, not the parent repository."""
        harness = _Harness(tmp_path, "cockpit-ready")
        assert harness.run().returncode == 0
        cockpit_env = _state_file(harness.cockpit_env_file)
        assert (
            cockpit_env["OPIP_CANONICAL_REPLICA_ROOT"]
            == f"{REPLICA_CONTAINER_ROOT}/generations/{GENERATION_ID}"
        )
        # The verifier was pointed at the same generation.
        assert (
            f"canonical_replica verify --root {REPLICA_CONTAINER_ROOT}/generations/"
            f"{GENERATION_ID}"
        ) in harness.calls()

    def test_cockpit_ready_writes_only_cockpit_evidence(self, tmp_path: Path):
        harness = _Harness(tmp_path, "cockpit-ready")
        assert harness.run().returncode == 0
        # The PostgreSQL rollout evidence file is untouched.
        assert _state_file(harness.state_file) == {}
        cockpit = _state_file(harness.cockpit_state_file)
        assert cockpit["COCKPIT_READY_SHA"] == TARGET_SHA
        assert "COCKPIT_READY_AT_UTC" in cockpit
        assert set(cockpit) == {"COCKPIT_READY_AT_UTC", "COCKPIT_READY_SHA"}

    def test_cockpit_ready_fails_closed_when_no_generation_is_committed(
        self, tmp_path: Path
    ):
        harness = _Harness(tmp_path, "cockpit-ready")
        (harness.replica_parent / "current").unlink()
        result = harness.run(resolve_rc="1")
        assert result.returncode != 0
        assert "no committed canonical replica generation" in result.stderr
        # Fail closed before starting the container, and leave no readiness marker.
        assert "compose up -d opip-cockpit" not in harness.calls()
        assert _state_file(harness.cockpit_state_file) == {}

    def test_cockpit_ready_fails_closed_when_replica_verification_fails(
        self, tmp_path: Path
    ):
        harness = _Harness(tmp_path, "cockpit-ready")
        result = harness.run(verify_rc="78")
        assert result.returncode != 0
        assert "did not verify" in result.stderr
        # Verification precedes any container start.
        assert "compose up -d opip-cockpit" not in harness.calls()
        assert _state_file(harness.cockpit_state_file) == {}

    def test_cockpit_ready_fails_closed_when_the_container_never_gets_healthy(
        self, tmp_path: Path
    ):
        harness = _Harness(tmp_path, "cockpit-ready")
        result = harness.run(health="starting")
        assert result.returncode != 0
        assert "did not become healthy" in result.stderr
        # A container that never becomes healthy must not be declared ready.
        assert _state_file(harness.cockpit_state_file) == {}

    def test_health_wait_precedes_the_reachability_proof(self, tmp_path: Path):
        harness = _Harness(tmp_path, "cockpit-ready")
        assert harness.run().returncode == 0
        calls = harness.calls()
        assert calls.index("compose up -d opip-cockpit") < calls.index("inspect")
        assert calls.index("inspect") < calls.index("docker port")

    def test_failed_attempt_never_leaves_a_false_marker(self, tmp_path: Path):
        first = _Harness(tmp_path / "a", "cockpit-ready", sha=TARGET_SHA)
        assert first.run().returncode == 0
        assert _state_file(first.cockpit_state_file)["COCKPIT_READY_SHA"] == TARGET_SHA

        second = _Harness(tmp_path / "b", "cockpit-ready", sha=OTHER_SHA)
        second.cockpit_state_file.write_bytes(first.cockpit_state_file.read_bytes())
        assert second.run(verify_rc="78").returncode != 0
        recorded = _state_file(second.cockpit_state_file)
        assert recorded.get("COCKPIT_READY_SHA") != OTHER_SHA
        assert recorded.get("COCKPIT_READY_SHA") == TARGET_SHA

    def test_repeated_cockpit_ready_is_idempotent(self, tmp_path: Path):
        harness = _Harness(tmp_path, "cockpit-ready")
        assert harness.run().returncode == 0
        first = _state_file(harness.cockpit_state_file)
        assert harness.run().returncode == 0
        second = _state_file(harness.cockpit_state_file)

        # The timestamp is second-resolution, so equality is asserted on the recorded
        # release and on key cardinality rather than on the wall-clock value.
        assert second["COCKPIT_READY_SHA"] == first["COCKPIT_READY_SHA"] == TARGET_SHA
        assert set(second) == {"COCKPIT_READY_AT_UTC", "COCKPIT_READY_SHA"}
        raw = harness.cockpit_state_file.read_text(encoding="utf-8")
        assert raw.count("COCKPIT_READY_SHA=") == 1
        assert raw.count("COCKPIT_READY_AT_UTC=") == 1

    def test_cockpit_ready_refreshes_its_sha_without_touching_rollout_evidence(
        self, tmp_path: Path
    ):
        harness = _Harness(tmp_path, "cockpit-ready")
        assert harness.run().returncode == 0
        assert _state_file(harness.state_file) == {}

        newer = _Harness(tmp_path, "cockpit-ready", sha=OTHER_SHA)
        assert newer.run().returncode == 0
        assert _state_file(harness.cockpit_state_file)["COCKPIT_READY_SHA"] == OTHER_SHA
        assert _state_file(harness.state_file) == {}

    def test_reads_ready_still_grants_historical_reads_and_does_not_verify_replica(
        self, tmp_path: Path
    ):
        harness = _Harness(tmp_path, "reads-ready")
        soak_start = subprocess.run(
            ["date", "-u", "-d", "8 days ago", "+%Y-%m-%dT%H:%M:%SZ"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        harness.seed_state(SHIPPER_STARTED_AT_UTC=soak_start)
        result = harness.run()
        assert result.returncode == 0, result.stderr
        calls = harness.calls()

        # Every historical gate ran, and the PostgreSQL services were started.
        assert "compose up -d opip-postgres" in calls
        assert "wait_for_postgres" in calls
        assert "admin_run python -m app.opip.data_platform.health --require-ready" in calls
        assert "systemctl enable --now opip-postgres-backup.timer" in calls
        assert "systemctl enable --now opip-data-platform-maintenance.timer" in calls
        assert "write_grafana_env_file" in calls
        assert "require_grafana_verify_full" in calls

        # Historical evidence is written, and the Cockpit still started.
        state = _state_file(harness.state_file)
        assert state["READS_READY_SHA"] == TARGET_SHA
        assert state["DEPLOYED_SHA"] == TARGET_SHA
        assert "COCKPIT_READY" not in harness.state_file.read_text(encoding="utf-8")
        assert _state_file(harness.cockpit_state_file)["COCKPIT_READY_SHA"] == TARGET_SHA

        # Crucially, historical readiness did not depend on the replica plane: the
        # generation was resolved for the mount, but no replica verification ran.
        assert "canonical_replica resolve" in calls
        assert "canonical_replica verify" not in calls

    def test_reads_ready_still_refuses_a_short_soak(self, tmp_path: Path):
        harness = _Harness(tmp_path, "reads-ready")
        recent = subprocess.run(
            ["date", "-u", "-d", "1 day ago", "+%Y-%m-%dT%H:%M:%SZ"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        harness.seed_state(SHIPPER_STARTED_AT_UTC=recent)
        result = harness.run()
        assert result.returncode != 0
        assert "seven days" in result.stderr
        state = _state_file(harness.state_file)
        assert "READS_READY_AT_UTC" not in state
        assert "READS_READY_SHA" not in state
        assert "DEPLOYED_SHA" not in state
        assert _state_file(harness.cockpit_state_file) == {}
        assert "compose up -d opip-cockpit" not in harness.calls()
