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
  unreadable, structurally invalid, SHA-mismatched or stale replicas;
* build and use the exact target Cockpit image;
* reject order-capable trading credentials and keep a distinct Cockpit secret;
* stay host-loopback only, never publicly bound;
* perform NO PostgreSQL work whatsoever - no postgres/shipper/grafana start, no
  migration/backfill/reconcile, no PostgreSQL timers, no PostgreSQL rollout evidence;
* leave the seven-day soak and every `reads-ready` gate exactly as they were;
* be idempotent, and never leave a false `COCKPIT_READY_*` marker after a failure.

Two kinds of proof are used. Structural assertions pin the control-flow guards that
make the "no PostgreSQL work" claim checkable. A bash harness additionally *executes*
the real bootstrap control flow with host-side effects stubbed, so the PostgreSQL-free
property is proven behaviourally rather than argued. The harness needs `bash`; CI
(ubuntu-latest) always has it, and this mirrors the existing root-only replica E2E
which is likewise executed explicitly in CI.
"""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

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

# Stages the workflow comment grammar must accept. Declared once so the three layers
# (workflow regex, gated runner, bootstrap) are asserted to agree.
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

# Effects that belong to the PostgreSQL plane and must be unreachable from
# `cockpit-ready`.
POSTGRES_EFFECTS = (
    "opip-postgres",
    "opip-shipper",
    "opip-grafana",
    "wait_for_postgres",
    "validate_postgres_tls_key",
    "validate_promotion_evidence",
    "admin_run",
    "systemctl",
    "/etc/systemd/system/",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_function(text: str, name: str) -> str:
    """Return a top-level bash function definition by name."""
    start = text.index(f"{name}() {{")
    end = text.index("\n}\n", start) + len("\n}\n")
    return text[start:end]


def _extract_guarded_block(text: str, guard: str) -> tuple[int, int, str]:
    """Return the extent of `if <guard>; then ... fi`, matched by nesting depth.

    Only multi-line `if ... fi` forms are counted. Every guard body in this script uses
    that form, and `elif` does not open a new block, so depth counting is sufficient and
    cannot silently truncate the block early.
    """
    lines = text.splitlines(keepends=True)
    start = None
    for index, line in enumerate(lines):
        if line.strip() == guard:
            start = index
            break
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


def _workflow_stage_grammar() -> re.Pattern[str]:
    """Translate the workflow's POSIX ERE command grammar into a Python pattern."""
    match = re.search(r"=~\s*(\^[^\n]*?\$)\s*\]\]", WORKFLOW)
    assert match, "could not locate the analytics command grammar in the workflow"
    posix = match.group(1)
    assert posix.startswith("^/deploy-analytics") and posix.endswith("$")
    translated = posix.replace("[[:space:]]", r"\s")
    return re.compile(translated)


def _top_level_code(text: str) -> str:
    """Return only top-level statements: function bodies and comments removed.

    Assertions about what a stage *does* must not be confused by a helper function
    that merely defines the effect, nor by prose describing it.
    """
    kept: list[str] = []
    in_function = False
    for line in text.splitlines():
        if not in_function and re.match(r"^[a-z_][a-z0-9_]*\(\)\s*\{", line):
            in_function = True
            continue
        if in_function:
            if line.startswith("}"):
                in_function = False
            continue
        if line.lstrip().startswith("#"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _strip_comments(text: str) -> str:
    """Return a bash fragment with comment lines removed.

    Used where an assertion is about what the code *does*, so that prose explaining a
    deliberate omission cannot be mistaken for the omission itself.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _shell_stage_vocabulary(script: str) -> tuple[str, ...]:
    """Extract the explicit stage list from a `case "$STAGE" in` arm."""
    match = re.search(r'case "\$STAGE" in\n\s*([a-z|_-]+)\)', script)
    assert match, "could not locate the stage case arm"
    return tuple(match.group(1).split("|"))


def _cockpit_ready_branch() -> str:
    """Return exactly the `elif [[ "$STAGE" == "$COCKPIT_STAGE" ]]` branch body.

    Bounded at the next top-level statement so the trailing PostgreSQL-evidence guard
    is not mistaken for part of the branch.
    """
    start = BOOTSTRAP.index('elif [[ "$STAGE" == "$COCKPIT_STAGE" ]]')
    end = BOOTSTRAP.index('if [[ "$STAGE" != "$COCKPIT_STAGE" ]]; then', start)
    return BOOTSTRAP[start:end]


def _compose_service(service: str) -> dict:  # type: ignore[type-arg]
    import yaml

    compose = yaml.safe_load(COMPOSE_TEXT)
    return compose["services"][service]


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
    assert tuple(_workflow_stage_grammar_stage_list()) == WORKFLOW_STAGES
    assert tuple(_shell_stage_vocabulary(RUNNER)) == WORKFLOW_STAGES
    assert tuple(_shell_stage_vocabulary(BOOTSTRAP)) == BOOTSTRAP_STAGES


def _workflow_stage_grammar_stage_list() -> list[str]:
    match = re.search(r"=~\s*\^/deploy-analytics[^\n]+?\(([a-z|-]+)\)\[\[:space:\]\]\*\$", WORKFLOW)
    assert match, "could not locate the stage alternation in the workflow grammar"
    return match.group(1).split("|")


def test_only_the_owner_issue_64_pathway_can_deploy_cockpit_ready():
    """The new stage is reachable only through the existing owner-gated pathway."""
    job_condition = WORKFLOW[WORKFLOW.index("if: >-") : WORKFLOW.index("runs-on:")]
    assert "github.event.issue.number == 64" in job_condition
    assert "github.event.comment.user.login == github.repository_owner" in job_condition
    assert "github.event.comment.author_association == 'OWNER'" in job_condition
    assert "startsWith(github.event.comment.body, '/deploy-analytics ')" in job_condition
    # There is exactly one job, one environment, and no per-stage escape hatch.
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
    # The gates run before checkout and before the remote stage, for every stage.
    order = WORKFLOW.index("Require target to equal current main")
    assert order < WORKFLOW.index("Check out exact approved release")
    assert order < WORKFLOW.index("Run exactly one gated analytics stage")
    assert "if:" not in WORKFLOW[
        WORKFLOW.index("Require target to equal current main") :
        WORKFLOW.index("Require successful exact-SHA CI")
    ]


# ---------------------------------------------------------------------------
# 5-8, 17-21: the cockpit-ready path performs no PostgreSQL work
# ---------------------------------------------------------------------------


def test_cockpit_ready_is_excluded_from_every_postgres_only_preflight():
    """The PostgreSQL preflight block must be guarded by the cockpit-ready exclusion."""
    _, _, guarded = _extract_guarded_block(
        BOOTSTRAP, 'if [[ "$STAGE" != "$COCKPIT_STAGE" ]]; then'
    )
    for effect in (
        "validate_postgres_tls_key",
        "build opip-shipper",
        "compose up -d opip-postgres",
        "wait_for_postgres",
        "validate_promotion_evidence",
    ):
        assert effect in guarded, f"{effect} must be guarded"


def test_cockpit_ready_writes_no_postgres_evidence_and_enables_no_timers():
    """Rollout evidence, systemd units and PostgreSQL timers are all guarded."""
    blocks = []
    remaining = 0
    for _ in range(2):
        _start, end, guarded = _extract_guarded_block(
            BOOTSTRAP[
                remaining:
            ],
            'if [[ "$STAGE" != "$COCKPIT_STAGE" ]]; then',
        )
        blocks.append(guarded)
        remaining += end

    trailing = blocks[1]
    assert 'write_state DEPLOYED_SHA "$TARGET_SHA"' in trailing
    assert "systemctl enable --now opip-postgres-backup.timer" in trailing
    assert "systemctl enable --now opip-data-platform-maintenance.timer" in trailing
    assert "/etc/systemd/system/$unit" in trailing
    assert "opip-data-platform-maintenance.sh" in trailing
    assert "opip-postgres-backup.sh" in trailing
    assert "opip-postgres-restore-drill.sh" in trailing


def test_no_postgres_effect_exists_outside_the_guarded_cockpit_ready_exclusions():
    """Everything cockpit-ready executes must be free of PostgreSQL effects.

    The guards cover the shared prelude; the stage-specific branches are only reachable
    for their own stage. So the two regions cockpit-ready can actually execute are the
    always-run prelude and its own branch, and neither may contain a PostgreSQL effect.
    """
    prelude = _top_level_code(
        BOOTSTRAP[
            BOOTSTRAP.index('case "$STAGE" in') : BOOTSTRAP.index(
                'export OPIP_DEPLOYED_SHA="$TARGET_SHA"'
            )
        ]
    )
    for effect in (
        "compose up",
        "docker compose",
        "admin_run",
        "systemctl",
        "write_state ",
        "/etc/systemd/system/",
    ):
        assert effect not in prelude, f"{effect} runs unconditionally in the prelude"

    cockpit_branch = _cockpit_ready_branch()
    for effect in POSTGRES_EFFECTS:
        assert effect not in cockpit_branch, f"{effect} is reachable from cockpit-ready"

    # The PostgreSQL start and the shipper start each have exactly one call site, and
    # neither is in a region cockpit-ready can reach.
    assert BOOTSTRAP.count("compose up -d opip-postgres") == 1
    assert BOOTSTRAP.count("compose up -d opip-shipper") == 1
    start = BOOTSTRAP.index("compose up -d opip-shipper")
    shipper_branch = (
        BOOTSTRAP.index('elif [[ "$STAGE" == "shipper" ]]') <= start
        <= BOOTSTRAP.index('elif [[ "$STAGE" == "reads-ready" ]]')
    )
    assert shipper_branch, "the shipper start must live in the shipper branch"


def test_cockpit_ready_never_writes_reads_ready_evidence():
    """`READS_READY_*` is historical PostgreSQL evidence and stays in reads-ready only."""
    assert BOOTSTRAP.count("READS_READY_AT_UTC") == 1
    assert BOOTSTRAP.count("READS_READY_SHA") == 1
    reads_ready_branch = BOOTSTRAP[
        BOOTSTRAP.index('elif [[ "$STAGE" == "reads-ready" ]]') :
        BOOTSTRAP.index('elif [[ "$STAGE" == "$COCKPIT_STAGE" ]]')
    ]
    assert "READS_READY_AT_UTC" in reads_ready_branch
    assert "READS_READY_SHA" in reads_ready_branch

    cockpit_branch = _cockpit_ready_branch()
    assert "READS_READY" not in cockpit_branch
    # ...and the primitive it calls grants no historical readiness in any spelling.
    cockpit_deploy = _extract_function(BOOTSTRAP, "cockpit_deploy")
    assert "historical_analytics_ready=false" in cockpit_deploy


def test_cockpit_evidence_has_its_own_file_so_rollout_env_is_untouched():
    """Cockpit readiness must not be recorded in the PostgreSQL rollout evidence."""
    assert 'STATE_FILE="$STATE_ROOT/rollout.env"' in BOOTSTRAP
    assert 'COCKPIT_STATE_FILE="$STATE_ROOT/cockpit-ready.env"' in BOOTSTRAP
    write_cockpit_state = _extract_function(BOOTSTRAP, "write_cockpit_state")
    assert 'mktemp "$COCKPIT_STATE_FILE.XXXXXX"' in write_cockpit_state
    assert 'mv -f -- "$temporary" "$COCKPIT_STATE_FILE"' in write_cockpit_state
    # It must never touch the PostgreSQL rollout evidence file.
    assert "$STATE_FILE" not in write_cockpit_state
    # The Cockpit key set is exactly the two documented keys.
    cockpit_deploy = _extract_function(BOOTSTRAP, "cockpit_deploy")
    assert "COCKPIT_READY_AT_UTC" in cockpit_deploy
    assert "COCKPIT_READY_SHA" in cockpit_deploy
    assert "READS_READY" not in cockpit_deploy


def test_reads_ready_keeps_the_seven_day_soak_and_all_historical_gates():
    """The historical path must be unchanged: soak, gates, then READS_READY_*."""
    branch = BOOTSTRAP[
        BOOTSTRAP.index('elif [[ "$STAGE" == "reads-ready" ]]') :
        BOOTSTRAP.index('elif [[ "$STAGE" == "$COCKPIT_STAGE" ]]')
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
    # Evidence is written only after every gate above it.
    assert branch.index("7 * 86400") < branch.index("READS_READY_AT_UTC")
    assert branch.index("health --require-ready") < branch.index("READS_READY_AT_UTC")


def test_reads_ready_and_cockpit_ready_share_one_cockpit_primitive():
    """Both stages must call the shared primitive, so they cannot drift apart."""
    assert BOOTSTRAP.count("compose up -d opip-cockpit") == 1
    assert BOOTSTRAP.count("\n  cockpit_preflight\n") == 1
    reads_ready_branch = BOOTSTRAP[
        BOOTSTRAP.index('elif [[ "$STAGE" == "reads-ready" ]]') :
        BOOTSTRAP.index('elif [[ "$STAGE" == "$COCKPIT_STAGE" ]]')
    ]
    cockpit_branch = _cockpit_ready_branch()
    assert "cockpit_deploy" in reads_ready_branch
    assert "cockpit_deploy" in cockpit_branch
    # No duplicated inline start sequence survives anywhere.
    assert BOOTSTRAP.count("cockpit_verify_replica\n") == 1


# ---------------------------------------------------------------------------
# 9-13: credentials, exposure and the exact image
# ---------------------------------------------------------------------------


def test_cockpit_secret_stays_distinct_from_the_trading_secret():
    """The Cockpit gets its own read-only secret, never the trading operator secret."""
    assert "OPIP_COCKPIT_SECRET" in ENV_EXAMPLE
    assert "OPIP_COCKPIT_SECRET" in BOOTSTRAP
    assert 'COCKPIT_ENV_FILE="/etc/opip-cockpit.env"' in BOOTSTRAP
    # The trading operator secret must never be installed for the Cockpit.
    assert "WEBHOOK_SECRET=/etc/opip-cockpit.env" not in BOOTSTRAP
    assert "OPIP_COCKPIT_SECRET=$WEBHOOK_SECRET" not in BOOTSTRAP
    guard = _extract_function(BOOTSTRAP, "guard_no_trading_credentials")
    for key in ("WEBHOOK_SECRET", "KRAKEN_API_KEY", "KRAKEN_API_SECRET", "TELEGRAM_BOT_TOKEN"):
        assert key in guard


def test_cockpit_is_published_only_on_host_loopback():
    """Raw Cockpit HTTP must remain loopback-only; a public bind is refused."""
    ports = _compose_service("opip-cockpit")["ports"]
    assert len(ports) == 1
    published = str(ports[0])
    assert published.startswith("${OPIP_COCKPIT_BIND_ADDRESS:-127.0.0.1}:")
    assert "0.0.0.0" not in published
    assert "[::]" not in published
    assert "OPIP_COCKPIT_BIND_ADDRESS=127.0.0.1" in ENV_EXAMPLE
    # The runtime preflight refuses anything that is not loopback, and refuses a
    # publicly bound port even if the configuration claims loopback.
    preflight = _extract_function(BOOTSTRAP, "cockpit_preflight")
    assert "OPIP_COCKPIT_BIND_ADDRESS must be host loopback" in preflight
    assert "bound on a public interface" in preflight


def test_cockpit_uses_the_exact_target_image():
    """A stale image from an earlier rollout must not be able to satisfy this stage."""
    assert "opip-data-platform:${OPIP_DEPLOYED_SHA:-local}" in COMPOSE_TEXT
    deploy = _extract_function(BOOTSTRAP, "cockpit_deploy")
    # The release SHA is exported before the build, so the tag is the target SHA.
    assert 'export OPIP_DEPLOYED_SHA="$TARGET_SHA"' in deploy
    assert deploy.index('export OPIP_DEPLOYED_SHA="$TARGET_SHA"') < deploy.index(
        "build opip-cockpit"
    )
    assert "docker compose -f \"$COMPOSE\" build opip-cockpit" in deploy
    # Building the Cockpit must not build or start the PostgreSQL services.
    assert "opip-shipper" not in deploy
    assert "opip-postgres" not in deploy


def test_replica_probe_container_is_offline_read_only_and_target_pinned():
    """The probe must be the existing verifier, isolated and release-pinned."""
    verify = _extract_function(BOOTSTRAP, "cockpit_verify_replica")
    assert 'local image="opip-data-platform:${TARGET_SHA}"' in verify
    assert "--network none" in verify
    assert "--read-only" in verify
    assert "--cap-drop ALL" in verify
    assert "--security-opt no-new-privileges:true" in verify
    # The replica is mounted read-only, so verification cannot mutate evidence.
    assert '-v "$host_root:$container_root:ro"' in verify
    # The existing verifier CLI is reused rather than reimplemented.
    assert "python -m app.opip.learning.canonical_replica verify" in verify
    assert "--release-sha \"$TARGET_SHA\"" in verify
    # The freshness bound is the existing contract default and cannot be widened here:
    # the flag is absent from the code itself (the prose explaining that is a comment).
    code = _strip_comments(verify)
    assert "--max-age-seconds" not in code
    # It must fail the stage closed rather than continue to start the container.
    assert "exit 69" in verify


def test_replica_probe_paths_match_the_compose_mount():
    """The probe and the container must read the same replica, or readiness is a lie."""
    assert 'COCKPIT_REPLICA_HOST_ROOT="/var/lib/opip-learning/canonical-replica"' in BOOTSTRAP
    assert 'COCKPIT_REPLICA_CONTAINER_ROOT="/app/canonical-replica"' in BOOTSTRAP
    volumes = _compose_service("opip-cockpit")["volumes"]
    assert volumes == [
        "/var/lib/opip-learning/canonical-replica:/app/canonical-replica:ro"
    ]


def test_cockpit_remains_read_only_and_get_only():
    """The new stage must not widen the Cockpit's read-only authority."""
    service = _compose_service("opip-cockpit")
    assert service["read_only"] is True
    assert "ALL" in service["cap_drop"]
    assert "no-new-privileges:true" in service["security_opt"]
    # No trading, order, Telegram or exchange credential is present at all.
    for forbidden in ("KRAKEN", "TELEGRAM", "WEBHOOK_SECRET"):
        assert forbidden not in COMPOSE_TEXT
    # The Cockpit receives only its own filtered env file and three non-secret
    # settings; no database, dashboard or trading credential reaches it.
    assert service["env_file"] == ["/etc/opip-cockpit.env"]
    assert set(service["environment"]) == {
        "OPIP_CANONICAL_REPLICA_ROOT",
        "OPIP_COCKPIT_HTTP_PORT",
        "PYTHONDONTWRITEBYTECODE",
    }
    # The trading app must not import or mount the Cockpit router. It mentions the
    # deliberate omission in a comment, so imports are checked structurally.
    import ast

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
# 24: documented distinction between the two readiness planes
# ---------------------------------------------------------------------------


def test_readme_documents_the_two_independent_readiness_planes():
    assert "cockpit-ready" in README
    for phrase in (
        "Two independent readiness planes",
        "Not involved",
        "seven-day",
        "loopback",
        "required for external access",
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


# ---------------------------------------------------------------------------
# 25: no new infrastructure, and CI still syntax-checks the shell it changed
# ---------------------------------------------------------------------------


def test_no_new_infrastructure_or_dependency_is_introduced():
    """The change must reuse the existing plane: no new service, proxy or database."""
    compose = __import__("yaml").safe_load(COMPOSE_TEXT)
    assert sorted(compose["services"]) == [
        "opip-cockpit",
        "opip-data-admin",
        "opip-grafana",
        "opip-postgres",
        "opip-shipper",
    ]
    # No second proxy, scheduler or datastore was added by this change.
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

    docker() {
      _log "docker $*"
      case "${1:-}" in
        inspect) printf 'healthy\\n' ;;
        port) printf '0.0.0.0:8000\\n-> 127.0.0.1:8000\\n' ;;
      esac
      if [[ "${OPIP_TEST_DOCKER_RUN_RC:-0}" != "0" && "${1:-}" == "run" ]]; then
        return "${OPIP_TEST_DOCKER_RUN_RC}"
      fi
      return 0
    }
    compose() { _log "compose $*"; return 0; }
    admin_run() { _log "admin_run $*"; return 0; }
    systemctl() { _log "systemctl $*"; return 0; }
    install() { _log "install $*"; return 0; }
    chown() { return 0; }
    ss() {
      printf 'State Recv-Q Send-Q Local Address:Port Peer Address:Port\\n'
      printf 'LISTEN 0 4096 127.0.0.1:8000 0.0.0.0:*\\n'
    }
    wait_for_postgres() { _log "wait_for_postgres"; return 0; }
    validate_postgres_tls_key() { _log "validate_postgres_tls_key"; return 0; }
    validate_promotion_evidence() { _log "validate_promotion_evidence"; return 0; }
    require_stage() { _log "require_stage $1"; return 0; }
    """
)


class _Harness:
    """Runs the real bootstrap control flow for one stage with host effects stubbed."""

    def __init__(self, tmp_path: Path, stage: str, sha: str = TARGET_SHA) -> None:
        self.tmp_path = tmp_path
        self.stage = stage
        self.sha = sha
        self.state_root = tmp_path / "state"
        self.state_root.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_root / "rollout.env"
        self.cockpit_state_file = self.state_root / "cockpit-ready.env"
        self.log = tmp_path / "calls.log"
        self.log.write_text("", encoding="utf-8")
        self.replica_root = tmp_path / "canonical-replica"
        self.replica_root.mkdir(exist_ok=True)
        self.compose_file = tmp_path / "docker-compose.yml"
        self.compose_file.write_text("services: {}\n", encoding="utf-8")

    def seed_state(self, **values: str) -> None:
        body = "".join(f"{key}={value}\n" for key, value in values.items())
        self.state_file.write_text(body, encoding="utf-8")

    def build_script(self) -> str:
        """Assemble the harness: real control flow, stubbed host-side effects."""
        script = textwrap.dedent(
            f"""
            TARGET_SHA={shlex.quote(self.sha)}
            STAGE={shlex.quote(self.stage)}
            COMPOSE={shlex.quote(str(self.compose_file))}
            APP_ROOT={shlex.quote(str(self.tmp_path / "app"))}
            STATE_ROOT={shlex.quote(str(self.state_root))}
            STATE_FILE={shlex.quote(str(self.state_file))}
            COCKPIT_STATE_FILE={shlex.quote(str(self.cockpit_state_file))}
            COCKPIT_REPLICA_HOST_ROOT={shlex.quote(str(self.replica_root))}
            COCKPIT_REPLICA_CONTAINER_ROOT=/app/canonical-replica
            COCKPIT_STAGE=cockpit-ready
            now_epoch="$(date -u +%s)"
            """
        )
        for name in (
            "write_state",
            "write_cockpit_state",
            "cockpit_verify_replica",
            "cockpit_deploy",
            "cockpit_preflight",
        ):
            script += "\n" + _extract_function(BOOTSTRAP, name)

        anchor = 'export OPIP_DEPLOYED_SHA="$TARGET_SHA"'
        # `cockpit_deploy` also pins the release SHA for its own build, so the main
        # flow is the LAST occurrence. Asserted explicitly rather than assumed, so the
        # harness can never silently slice a helper function instead of the main flow.
        start = BOOTSTRAP.rindex(anchor)
        assert start > BOOTSTRAP.index("cockpit_deploy() {"), (
            "the harness must slice the main stage flow, not a helper definition"
        )
        region = BOOTSTRAP[start:]
        # The harness must exercise the main flow only: taking the analytics-plane lock
        # or writing outside the temporary directory would be a side effect of testing.
        assert "flock" not in region
        assert "exec 8>" not in region
        return script + "\n" + _STUB_PREAMBLE + "\n" + region

    def run(
        self, *, create_replica: bool = True, docker_run_rc: str = "0"
    ) -> subprocess.CompletedProcess[str]:
        if not create_replica:
            self.replica_root.rmdir()

        harness = self.tmp_path / "harness.sh"
        harness.write_text(self.build_script(), encoding="utf-8")

        import os

        environment = dict(os.environ)
        environment["OPIP_TEST_LOG"] = str(self.log)
        environment["OPIP_TEST_DOCKER_RUN_RC"] = docker_run_rc
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

    This runs everywhere, including hosts without bash, so harness assembly is verified
    locally rather than only where the behavioural suite can execute.
    """
    for stage in ("cockpit-ready", "reads-ready"):
        script = _Harness(tmp_path / stage, stage).build_script()
        assert "cockpit_deploy() {" in script
        assert "cockpit_verify_replica() {" in script
        assert "write_cockpit_state() {" in script
        assert "cockpit_preflight() {" in script
        assert 'export OPIP_DEPLOYED_SHA="$TARGET_SHA"' in script
        # The real stage routing is present: both guards plus both branches.
        assert script.count('if [[ "$STAGE" != "$COCKPIT_STAGE" ]]; then') == 2
        assert 'elif [[ "$STAGE" == "$COCKPIT_STAGE" ]]' in script
        assert 'elif [[ "$STAGE" == "reads-ready" ]]' in script
        # Host-side effects are stubbed and the analytics-plane lock is never taken.
        assert "OPIP_TEST_LOG" in script
        assert "flock" not in script
        assert "exec 8>" not in script
        assert "docker() {" in script
        assert "systemctl() {" in script


@requires_bash
class TestCockpitReadyBehaviour:
    """Behavioural proof that the new stage performs no PostgreSQL work."""

    def test_cockpit_ready_starts_only_the_cockpit(self, tmp_path: Path):
        harness = _Harness(tmp_path, "cockpit-ready")
        result = harness.run()
        assert result.returncode == 0, result.stderr
        calls = harness.calls()

        # The exact target image is built and the replica probe runs.
        assert "build opip-cockpit" in calls
        assert "docker run" in calls
        # The service is started and the preflight proved reachability.
        assert "compose up -d opip-cockpit" in calls
        # No PostgreSQL effect of any kind occurred.
        for effect in POSTGRES_EFFECTS:
            assert effect not in calls, f"{effect} ran during cockpit-ready"
        assert "opip-postgres" not in calls
        assert "opip-shipper" not in calls
        assert "opip-grafana" not in calls

    def test_cockpit_ready_writes_only_cockpit_evidence(self, tmp_path: Path):
        harness = _Harness(tmp_path, "cockpit-ready")
        assert harness.run().returncode == 0
        # The PostgreSQL rollout evidence file is untouched.
        assert _state_file(harness.state_file) == {}
        # Cockpit evidence is recorded in its own file, with the exact target SHA.
        cockpit = _state_file(harness.cockpit_state_file)
        assert cockpit["COCKPIT_READY_SHA"] == TARGET_SHA
        assert "COCKPIT_READY_AT_UTC" in cockpit
        assert "READS_READY_SHA" not in cockpit
        assert "READS_READY_AT_UTC" not in cockpit
        assert "DEPLOYED_SHA" not in cockpit

    def test_cockpit_ready_fails_closed_when_the_replica_is_missing(self, tmp_path: Path):
        harness = _Harness(tmp_path, "cockpit-ready")
        result = harness.run(create_replica=False)
        assert result.returncode != 0
        assert "canonical replica root is absent" in result.stderr
        # It must fail before starting the container, and leave no readiness marker.
        assert "compose up -d opip-cockpit" not in harness.calls()
        assert "docker run" not in harness.calls()
        assert _state_file(harness.cockpit_state_file) == {}

    def test_cockpit_ready_fails_closed_when_replica_verification_fails(self, tmp_path: Path):
        harness = _Harness(tmp_path, "cockpit-ready")
        result = harness.run(docker_run_rc="78")
        assert result.returncode != 0
        assert "did not verify" in result.stderr
        # A failed verification must not start the container nor record readiness.
        assert "compose up -d opip-cockpit" not in harness.calls()
        assert _state_file(harness.cockpit_state_file) == {}

    def test_failed_attempt_never_leaves_a_false_marker(self, tmp_path: Path):
        # A genuine earlier success for one release is recorded...
        first = _Harness(tmp_path / "a", "cockpit-ready", sha=TARGET_SHA)
        assert first.run().returncode == 0
        assert _state_file(first.cockpit_state_file)["COCKPIT_READY_SHA"] == TARGET_SHA

        # ...and a later failed attempt for a different release must not claim it.
        second = _Harness(tmp_path / "b", "cockpit-ready", sha=OTHER_SHA)
        second.cockpit_state_file.write_bytes(first.cockpit_state_file.read_bytes())
        assert second.run(docker_run_rc="78").returncode != 0
        recorded = _state_file(second.cockpit_state_file)
        assert recorded.get("COCKPIT_READY_SHA") != OTHER_SHA
        assert recorded.get("COCKPIT_READY_SHA") == TARGET_SHA

    def test_repeated_cockpit_ready_is_idempotent(self, tmp_path: Path):
        harness = _Harness(tmp_path, "cockpit-ready")
        assert harness.run().returncode == 0
        first = _state_file(harness.cockpit_state_file)
        assert harness.run().returncode == 0
        second = _state_file(harness.cockpit_state_file)

        # Re-running must converge on the same evidence rather than accumulate it. The
        # timestamp is second-resolution, so equality is asserted on the recorded
        # release and on key cardinality rather than on the wall-clock value.
        assert second["COCKPIT_READY_SHA"] == first["COCKPIT_READY_SHA"] == TARGET_SHA
        assert "COCKPIT_READY_AT_UTC" in second
        assert set(second) == {"COCKPIT_READY_AT_UTC", "COCKPIT_READY_SHA"}
        raw = harness.cockpit_state_file.read_text(encoding="utf-8")
        assert raw.count("COCKPIT_READY_SHA=") == 1
        assert raw.count("COCKPIT_READY_AT_UTC=") == 1

    def test_cockpit_ready_refreshes_its_sha_without_touching_rollout_evidence(self, tmp_path: Path):
        harness = _Harness(tmp_path, "cockpit-ready")
        assert harness.run().returncode == 0
        assert _state_file(harness.state_file) == {}

        # Re-running for a newer release updates only the Cockpit evidence.
        newer = _Harness(tmp_path, "cockpit-ready", sha=OTHER_SHA)
        assert newer.run().returncode == 0
        assert _state_file(harness.cockpit_state_file)["COCKPIT_READY_SHA"] == OTHER_SHA
        assert _state_file(harness.state_file) == {}

    def test_reads_ready_still_grants_historical_reads_after_the_soak(self, tmp_path: Path):
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
        # Historical evidence is written, and the Cockpit still started too.
        state = _state_file(harness.state_file)
        assert state["READS_READY_SHA"] == TARGET_SHA
        assert state["DEPLOYED_SHA"] == TARGET_SHA
        # Cockpit readiness lives in the Cockpit file, not the rollout evidence.
        assert "COCKPIT_READY" not in harness.state_file.read_text(encoding="utf-8")
        assert _state_file(harness.cockpit_state_file)["COCKPIT_READY_SHA"] == TARGET_SHA

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
        # Neither readiness plane may be granted: no evidence was added, and the
        # Cockpit was never started.
        state = _state_file(harness.state_file)
        assert "READS_READY_AT_UTC" not in state
        assert "READS_READY_SHA" not in state
        assert "DEPLOYED_SHA" not in state
        assert _state_file(harness.cockpit_state_file) == {}
        assert "compose up -d opip-cockpit" not in harness.calls()
