"""Select the focused release tests for a changed-file set.

This module is CI orchestration, not application runtime. It is stdlib only, it
never imports application code, it never writes production state and it never
decides trading authority. It answers one question: "which existing test
modules must run for this change set, plus the small mandatory safety suite?".

Two selections exist:

``focused``
    Tests that reference a changed module or changed repository path, computed
    from the changed-file set of the exact commit. A docs-only or workflow-only
    change still runs the always-on CI contract floor, so a run can never be
    vacuous.

``safety``
    The mandatory safety suite (:data:`MANDATORY_SAFETY`). Every category and
    every node id is fixed in source; :mod:`tests.ci_mandatory_safety_receipt`
    refuses to certify a run in which any of them did not execute and pass.

The complete suite and the real-Docker OOM/fault-injection proofs are
manual-only and are declared in :data:`MANUAL_ONLY_TARGETS`; they are never
selected automatically.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
TESTS_DIR = APP_ROOT / "tests"

INCREMENT = "ATDD-CI-OPTIMIZATION-M01"

FOCUSED_MODE = "focused"
SAFETY_MODE = "safety"

# Always selected, whatever changed: the CI wiring contract itself.
ALWAYS_FOCUSED: tuple[str, ...] = ("tests/test_opip_ci_optimization_v1.py",)

# Manual-only evidence. Real-Docker OOM/fault-injection proofs run from
# `.github/workflows/pytest-full-manual.yml`, never automatically.
MANUAL_ONLY_TARGETS: tuple[str, ...] = (
    "tests/test_opip_scan_docker_fault_injection_v1.py",
)

# Hard floor, so an accidentally emptied suite fails closed instead of passing.
MIN_MANDATORY_NODE_IDS = 30

# The mandatory safety suite. These node ids are the protected financial and
# production surfaces: F11 protection, funded-trading authorization, financial
# calculations (fixed risk sizing and minimum R:R geometry), portfolio
# protection and configuration validation. Two further categories pin the
# release authorization gate and this CI contract itself, so the suite cannot
# silently shrink.
MANDATORY_SAFETY: dict[str, tuple[str, ...]] = {
    "f11_protection": (
        "tests/test_opip_f11_precutover_protection.py::test_ac_002_one_mutating_authority_and_read_only_evaluator",
        "tests/test_opip_f11_precutover_protection.py::test_ac_003_silent_holding_is_unsafe_and_suspends",
        "tests/test_opip_f11_precutover_protection.py::test_ac_003_missing_coverage_fails_closed",
        "tests/test_opip_f11_precutover_protection.py::test_ac_004_uncertainty_fails_closed",
        "tests/test_opip_f11_precutover_protection.py::test_ac_011_incident_health_required_for_healthy",
    ),
    "funded_authorization": (
        "tests/test_opip_paper_v2_activation_bc3.py::test_activation_gate_has_no_funded_or_exchange_surface",
        "tests/test_opip_r4_f8_cutover_readiness.py::test_ac_009_no_funded_or_exchange_credentials_added",
        "tests/test_release_provenance_reconcile_v1.py::test_truthy_funded_authority_key_is_refused",
        "tests/test_tradevisor_core_env_isolation_v1.py::test_funded_credential_names_match_the_python_authority",
    ),
    "financial_calculations": (
        "tests/test_trading_algorithm_adversarial_financials_v1.py::test_capital_allocation_rejects_non_finite_inputs",
        "tests/test_trading_algorithm_adversarial_financials_v1.py::test_economic_gate_rejects_non_finite_plan_geometry",
        "tests/test_trading_algorithm_adversarial_financials_v1.py::test_profit_ranking_does_not_award_nan_economic_move_full_credit",
        "tests/test_trading_algorithm_adversarial_financials_v1.py::test_capital_rotation_non_finite_candidate_inputs_cannot_pass_exceptional_gate",
        "tests/test_paper_trading_v1_core.py::test_capacity_guard_reserves_pending_and_open_capital",
        "tests/test_paper_trading_v1_core.py::test_invalid_long_geometry_is_rejected_before_paper_state_is_created",
    ),
    "portfolio_protection": (
        "tests/test_wave9_position_protection.py::test_registry_trade_absent_on_kraken_is_not_verified",
        "tests/test_wave9_position_protection.py::test_kraken_unavailable_never_becomes_absent",
        "tests/test_wave9_position_protection.py::test_unpriced_non_cash_holding_is_never_silently_dropped",
        "tests/test_wave9_position_protection.py::test_limited_history_still_enforces_stop",
        "tests/test_wave9_position_protection.py::test_mfe_giveback_promotes_hold_to_warning",
    ),
    "configuration_validation": (
        "tests/test_signal_quality_config_v1.py::test_misordered_liquidity_bands_are_rejected",
        "tests/test_signal_quality_config_v1.py::test_retention_shorter_than_the_continuity_window_is_rejected",
        "tests/test_strategy_policy_config.py::test_auto_trading_cannot_be_enabled_through_profile",
        "tests/test_technical_score_v2_config.py::test_production_authority_from_json_rejected",
    ),
    "release_authorization": (
        "tests/test_opip_release_pipeline_v1.py::test_ac_009_profile_approval_is_owner_only_exact_sha_and_gated",
        "tests/test_opip_ui_only_release_controller_v1.py::test_owner_gate_requires_exact_main_sha_and_green_ci",
        "tests/test_opip_feature_stack_gates_v1.py::test_ac_027_the_gate_set_is_unchanged_and_never_advisory",
    ),
    "ci_release_contract": (
        "tests/test_opip_ci_optimization_v1.py::test_ac_001_automatic_release_ci_runs_focused_selection_and_mandatory_safety",
        "tests/test_opip_ci_optimization_v1.py::test_ac_002_full_suite_and_docker_oom_proofs_are_manual_only",
        "tests/test_opip_ci_optimization_v1.py::test_ac_003_mandatory_safety_suite_covers_the_protected_surfaces",
        "tests/test_opip_ci_optimization_v1.py::test_ac_004_receipt_fails_closed_when_safety_tests_did_not_execute",
        "tests/test_opip_ci_optimization_v1.py::test_ac_005_exact_sha_deployment_gate_requires_focused_release_ci_evidence",
    ),
}
GENERIC_BASENAMES = frozenset(
    {"__init__.py", "conftest.py", "config.py", "models.py", "schemas.py", "utils.py"}
)

_MODULE_REF = re.compile(r"\bapp(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_FROM_IMPORT = re.compile(r"from\s+([A-Za-z_][\w.]*)\s+import\s+([^\n#]+)")
_REPO_PATH_REF = re.compile(r"(?:OHM-Trade-Agent-v1/|\.github/)[A-Za-z0-9._/-]+")
_APP_PATH_REF = re.compile(
    r"(?:app|deploy|docs|freqtrade|tests|tools|tradingview|control-center-v2)"
    r"/[A-Za-z0-9._/-]+\.[A-Za-z0-9]+"
)
_QUOTED_BASENAME = re.compile(r"[\"']([A-Za-z0-9._-]{5,})[\"']")


class SelectionError(RuntimeError):
    """A selection that cannot be proven; always fail closed."""


@dataclass(frozen=True)
class SelectionIndex:
    """Test-file index keyed by module, repository path and basename."""

    by_module: dict[str, frozenset[str]]
    by_path: dict[str, frozenset[str]]
    by_basename: dict[str, frozenset[str]]


def _relative(path: Path) -> str:
    return path.relative_to(APP_ROOT).as_posix()


def _iter_test_files() -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in TESTS_DIR.rglob("test_*.py")
            if "__pycache__" not in path.parts
        )
    )


def _module_tokens(text: str) -> set[str]:
    tokens = set(_MODULE_REF.findall(text))
    for match in _FROM_IMPORT.finditer(text):
        base = match.group(1)
        tokens.add(base)
        for name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", match.group(2)):
            tokens.add(f"{base}.{name}")
    return {token for token in tokens if token.startswith("app.")}


def _strip_punctuation(value: str) -> str:
    return value.rstrip(".,;:)]}\"'")


def _path_tokens(text: str) -> tuple[set[str], set[str]]:
    paths: set[str] = set()
    basenames: set[str] = set()
    references = list(_REPO_PATH_REF.finditer(text)) + list(_APP_PATH_REF.finditer(text))
    for match in references:
        candidate = _strip_punctuation(match.group(0))
        if not candidate or "/" not in candidate:
            continue
        paths.add(candidate)
        if candidate.startswith("OHM-Trade-Agent-v1/"):
            paths.add(candidate[len("OHM-Trade-Agent-v1/") :])
        basenames.add(candidate.rsplit("/", 1)[-1])
    for match in _QUOTED_BASENAME.finditer(text):
        candidate = match.group(1)
        if "/" in candidate:
            continue
        basenames.add(candidate)
    return paths, basenames


def _freeze(register: dict[str, set[str]]) -> dict[str, frozenset[str]]:
    return {key: frozenset(sorted(value)) for key, value in register.items()}


@lru_cache(maxsize=1)
def build_index() -> SelectionIndex:
    """Index every test module once per process."""

    by_module: dict[str, set[str]] = {}
    by_path: dict[str, set[str]] = {}
    by_basename: dict[str, set[str]] = {}
    for path in _iter_test_files():
        relative = _relative(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        for token in _module_tokens(text):
            by_module.setdefault(token, set()).add(relative)
        paths, basenames = _path_tokens(text)
        for token in paths:
            by_path.setdefault(token, set()).add(relative)
        for token in basenames:
            by_basename.setdefault(token, set()).add(relative)
    return SelectionIndex(
        by_module=_freeze(by_module),
        by_path=_freeze(by_path),
        by_basename=_freeze(by_basename),
    )




def _app_relative(changed: str) -> str:
    normalized = changed.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    prefix = "OHM-Trade-Agent-v1/"
    return normalized[len(prefix) :] if normalized.startswith(prefix) else normalized


def _module_name(app_relative: str) -> str | None:
    if not app_relative.endswith(".py"):
        return None
    parts = app_relative[: -len(".py")].split("/")
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or parts[0] != "app":
        return None
    return ".".join(parts)


def _basename_key(app_relative: str) -> str | None:
    name = app_relative.rsplit("/", 1)[-1]
    if name in GENERIC_BASENAMES:
        return None
    stem = name[: -len(".py")] if name.endswith(".py") else name
    return name if len(stem) >= 5 else None


def changed_file_keys(changed: str) -> tuple[set[str], set[str], set[str]]:
    """Return the index keys a changed repository path can match."""

    app_relative = _app_relative(changed)
    modules: set[str] = set()
    module = _module_name(app_relative)
    if module:
        modules.add(module)
    basename = _basename_key(app_relative)
    basenames = {basename} if basename else set()
    return modules, {app_relative}, basenames


def select_focused(
    changed_paths: tuple[str, ...] | list[str],
    index: SelectionIndex | None = None,
) -> tuple[str, ...]:
    """Return the test modules that must run for ``changed_paths``."""

    active = index or build_index()
    selected: set[str] = set()
    for changed in changed_paths or ():
        if not changed.strip():
            continue
        modules, paths, basenames = changed_file_keys(changed)
        for module in modules:
            for token, files in active.by_module.items():
                if token == module or token.startswith(module + "."):
                    selected.update(files)
        for path in paths:
            selected.update(active.by_path.get(path, frozenset()))
        for basename in basenames:
            selected.update(active.by_basename.get(basename, frozenset()))
    selected.difference_update(MANUAL_ONLY_TARGETS)
    # A deleted test module cannot be executed.
    selected = {target for target in selected if (APP_ROOT / target).is_file()}
    if not selected:
        return tuple(sorted(ALWAYS_FOCUSED))
    return tuple(sorted(selected))


def select_safety() -> tuple[str, ...]:
    """Return the mandatory safety node ids in declaration order."""

    targets: list[str] = []
    seen: set[str] = set()
    for node_ids in MANDATORY_SAFETY.values():
        for node_id in node_ids:
            if node_id in seen:
                continue
            seen.add(node_id)
            targets.append(node_id)
    return tuple(targets)


def mandatory_node_id_count() -> int:
    """Return the number of mandatory safety node ids."""

    return len(select_safety())


def git_changed_paths(base: str, repo_root: Path | None = None) -> tuple[str, ...]:
    """Return the changed paths of ``base...HEAD`` from a real git diff."""

    if not base or base.startswith("-") or set(base) == {"0"}:
        raise SelectionError("changed-file base is missing")
    completed = subprocess.run(
        ["git", "diff", "--name-only", "--find-renames", f"{base}...HEAD"],
        cwd=str(repo_root or REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise SelectionError(f"git diff failed: {detail}")
    return tuple(
        line.strip().replace("\\", "/")
        for line in completed.stdout.splitlines()
        if line.strip()
    )


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select the focused or mandatory release test selection.",
    )
    parser.add_argument("--mode", choices=(FOCUSED_MODE, SAFETY_MODE), required=True)
    parser.add_argument("--git-base", default=None)
    parser.add_argument("--changed-file", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(list(sys.argv[1:] if argv is None else argv))
    changed: tuple[str, ...] = ()
    try:
        if args.mode == SAFETY_MODE:
            targets = select_safety()
        else:
            if not args.changed_file and not args.git_base:
                raise SelectionError(
                    "focus selection requires --git-base or --changed-file"
                )
            supplied = list(args.changed_file)
            if args.git_base:
                supplied.extend(git_changed_paths(args.git_base))
            changed = tuple(supplied)
            targets = select_focused(changed)
        if not targets:
            raise SelectionError("selection is empty")
    except SelectionError as exc:
        print(f"SELECTION_FAILED: {exc}", file=sys.stderr)
        return 2
    for target in targets:
        print(target)
    print(
        f"SELECTION mode={args.mode} changed={len(changed)} targets={len(targets)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

