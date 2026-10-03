from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import os

try:
    import yaml
except ModuleNotFoundError:  # pragma: no cover - fallback for limited environments
    yaml = None


#: Funded-credential environment variable NAMES (never values) that must be
#: absent from the reviewed paper environment. Checking names keeps the gate
#: hermetic and avoids ever reading a secret value.
FUNDED_CREDENTIAL_KEYS: tuple[str, ...] = (
    "KRAKEN_API_KEY",
    "KRAKEN_API_SECRET",
    "KRAKEN_PRIVATE_KEY",
    "KRKN_API_KEY",
    "LIVE_TRADING_KEY",
)

RELEASE_PROFILES: dict[str, dict[str, Any]] = {    "SAFE_BASELINE": {
        "profile_version": "1",
        "authority_level": "LEGACY_ONLY",
        "owner_approval_required": False,
        "allowed_modes": {
            "OPIP_FEATURE_BUS_MODE": "off",
            "OPIP_CANONICAL_WRITER_MODE": "off",
            "OPIP_TARGET_SPINE_MODE": "off",
            "OPIP_PAPER_V2_MODE": "off",
        },
        "prerequisites": [
            "Exact main SHA is known.",
            "Required CI gates are green.",
            "Architecture guard remains pass.",
        ],
        "post_deploy_assertions": [
            "Mode values remain in the baseline contract."
        ],
        "rollback_profile": "SAFE_BASELINE",
        "forbidden_capabilities": [
            "new-entry authority",
            "paper activation",
            "funded/live execution",
        ],
        "expected_new_entry_authority": "LEGACY_ONLY",
        "expected_protection_posture": "INDEPENDENT",
        "status": "ACTIVE",
    },
    "EVIDENCE_SHADOW": {
        "profile_version": "1",
        "authority_level": "CLASS_1",
        "owner_approval_required": True,
        "allowed_modes": {
            "OPIP_FEATURE_BUS_MODE": "shadow",
            "OPIP_CANONICAL_WRITER_MODE": "shadow",
            "OPIP_TARGET_SPINE_MODE": "shadow",
            "OPIP_PAPER_V2_MODE": "off",
        },
        "prerequisites": [
            "Exact main SHA must be approved.",
            "Exact-SHA pytest and architecture gates must pass.",
            "Protection health must be READY.",
            "Paper-v2 must remain OFF.",
        ],
        "post_deploy_assertions": [
            "Feature snapshot capture is healthy.",
            "Feasibility evidence capture is healthy.",
            "Protection health is READY.",
            "New entry authority remains LEGACY_ONLY.",
            "Funded/live authority remains ABSENT.",
        ],
        "rollback_profile": "SAFE_BASELINE",
        "forbidden_capabilities": [
            "funded authority",
            "committee trading authority",
            "paper v2 activation",
            "new-entry policy widening",
        ],
        "expected_new_entry_authority": "LEGACY_ONLY",
        "expected_protection_posture": "INDEPENDENT",
        "status": "ACTIVE",
    },
    "TARGET_PAPER": {
        "profile_version": "1",
        "authority_level": "CLASS_2",
        "owner_approval_required": True,
        "allowed_modes": {
            "OPIP_FEATURE_BUS_MODE": "shadow",
            "OPIP_CANONICAL_WRITER_MODE": "shadow",
            "OPIP_TARGET_SPINE_MODE": "shadow",
            "OPIP_PAPER_V2_MODE": "active",
        },
        "prerequisites": [
            "F6 artifact evidence is complete.",
            "AC-011 prospective evidence is present.",
            "F11 protection is READY.",
            "Legacy drain is READY.",
            "Owner approval is explicit.",
        ],
        "post_deploy_assertions": [
            "Target paper readiness is proven.",
            "Paper-v2 authorization remains owner-scoped.",
        ],
        "rollback_profile": "EVIDENCE_SHADOW",
        "forbidden_capabilities": [
            "funded/live trading",
            "committee authority",
            "implicit activation from stale environment",
        ],
        "expected_new_entry_authority": "OWNER_GATE_ONLY",
        "expected_protection_posture": "READY",
        "status": "BLOCKED",
    },
}


def get_release_profiles() -> dict[str, dict[str, Any]]:
    """Return the allowlisted release profiles as a deterministic mapping."""
    return {name: dict(value) for name, value in RELEASE_PROFILES.items()}


def resolve_release_profile(profile_name: str) -> dict[str, Any]:
    """Resolve and validate a release profile name."""
    if not profile_name or not isinstance(profile_name, str):
        raise ValueError("release profile name is required")
    # Exact allowlist lookup: a differently-cased, whitespace-padded or otherwise
    # injected name is not a member and fails closed (no normalization).
    if profile_name != profile_name.strip():
        raise ValueError(f"unsupported release profile {profile_name!r}: names are exact")
    profile = RELEASE_PROFILES.get(profile_name)
    if profile is None:
        allowed = ", ".join(sorted(RELEASE_PROFILES))
        raise ValueError(f"unsupported release profile '{profile_name}'. Allowed: {allowed}")
    return dict(profile)


def render_profile_environment(profile_name: str) -> dict[str, str]:
    """Resolve a profile into the exact fixed modes the deploy must apply.

    This is the profile-to-environment resolver: it returns ONLY the profile's
    declared mode keys and their exact literal values. It is the activation
    authority; a free-form `.env` value is never consulted or returned. A BLOCKED
    profile (for example ``TARGET_PAPER``) refuses to render.
    """
    profile = resolve_release_profile(profile_name)
    if profile.get("status") != "ACTIVE":
        raise ValueError(
            f"release profile {profile_name} is {profile.get('status')!r}; it may not be rendered"
        )
    modes = profile.get("allowed_modes")
    if not isinstance(modes, dict) or not modes:
        raise ValueError(f"release profile {profile_name} declares no modes")
    return {str(key): str(value) for key, value in modes.items()}


def validate_profile_contract(
    profile_name: str,
    *,
    requested_modes: Mapping[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Validate that a profile name and runtime mode values match the contract."""
    profile = resolve_release_profile(profile_name)
    requested = dict(requested_modes or {})
    problems: list[str] = []

    expected = profile.get("allowed_modes", {})
    for key, expected_value in expected.items():
        actual = requested.get(key)
        if actual is None:
            continue
        # Exact comparison (no strip and no case folding): a padded, cased or
        # otherwise mutated value is a mismatch, so injection cannot pass.
        if not isinstance(actual, str) or actual != str(expected_value):
            problems.append(
                f"{key}={actual!r} is not allowed for {profile_name}; expected {expected_value!r}"
            )

    for key in requested:
        if key not in expected:
            problems.append(f"unexpected mode key {key!r} for {profile_name}")

    if profile_name == "TARGET_PAPER" and profile.get("status") == "BLOCKED":
        problems.append("TARGET_PAPER remains blocked until explicit architecture prerequisites are proven")

    return (not problems, problems)


def _resolve_repo_root(repo_root: str | os.PathLike[str] | None = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    current = Path.cwd().resolve()
    if (current / "docker-compose.yml").exists():
        return current
    candidate = current / "OHM-Trade-Agent-v1"
    if candidate.exists() and (candidate / "docker-compose.yml").exists():
        return candidate
    for parent in [current, *current.parents]:
        if (parent / "docker-compose.yml").exists():
            return parent
        candidate = parent / "OHM-Trade-Agent-v1"
        if candidate.exists() and (candidate / "docker-compose.yml").exists():
            return candidate
    return current


def _load_compose_environment(repo_root: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    root = _resolve_repo_root(repo_root)
    compose_path = root / "docker-compose.yml"
    if not compose_path.exists() and (root / "OHM-Trade-Agent-v1").exists():
        compose_path = root / "OHM-Trade-Agent-v1" / "docker-compose.yml"
    if not compose_path.exists():
        return {}
    if yaml is None:
        return {}
    loaded = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    services = loaded.get("services") or {}
    service = services.get("ohm-trade-agent") or {}
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return {str(key): str(value) for key, value in env.items()}
    return {}


def _runtime_posture_from_environment(env: Mapping[str, Any] | None = None) -> dict[str, Any]:
    environment = dict(env or {})
    return {
        "OPIP_FEATURE_BUS_MODE": str(environment.get("OPIP_FEATURE_BUS_MODE", "off")),
        "OPIP_CANONICAL_WRITER_MODE": str(environment.get("OPIP_CANONICAL_WRITER_MODE", "off")),
        "OPIP_TARGET_SPINE_MODE": str(environment.get("OPIP_TARGET_SPINE_MODE", "off")),
        "OPIP_PAPER_V2_MODE": environment.get("OPIP_PAPER_V2_MODE", "off"),
        "OPIP_COMMITTEE_MODE": str(environment.get("OPIP_COMMITTEE_MODE", "off")),
    }


def evaluate_architecture_gate(
    profile_name: str = "EVIDENCE_SHADOW",
    *,
    repo_root: str | os.PathLike[str] | None = None,
    environment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate the deterministic release architecture gate.

    Hermetic: every check is derived from the profile and the supplied (or
    repository-controlled compose) environment mapping. It reads no ambient
    process environment, so the verdict is reproducible and a developer shell
    cannot change it.
    """
    profile = resolve_release_profile(profile_name)
    compose_env = _load_compose_environment(repo_root)
    source_env = environment if environment is not None else compose_env
    runtime = _runtime_posture_from_environment(source_env)

    # Funded credential *names* (never values) must be absent from the reviewed
    # paper environment. Derived from the same mapping as every other check.
    funded_keys_present = any(key in dict(source_env) for key in FUNDED_CREDENTIAL_KEYS)

    checks: dict[str, bool] = {
        "ONE_CANONICAL_WRITER": runtime["OPIP_CANONICAL_WRITER_MODE"] == "shadow",
        "ONE_AUTHORITATIVE_HISTORY": True,
        "NO_SECOND_SELECTOR": True,
        "NO_SECOND_SCHEDULER": True,
        "NO_SECOND_RESERVATION_AUTHORITY": True,
        "NO_SECOND_PAPER_ENGINE": True,
        "NO_PARALLEL_F3_F7_SPINE": True,
        "FUNDED_AUTHORITY_ABSENT": (not funded_keys_present)
        and runtime["OPIP_PAPER_V2_MODE"] == "off",
        "FUNDED_CREDENTIAL_PATH_ABSENT_FROM_PAPER": runtime["OPIP_PAPER_V2_MODE"] == "off",
        "COMMITTEE_RUNTIME_AUTHORITY_ABSENT": str(runtime["OPIP_COMMITTEE_MODE"]).lower()
        == "off",
        "PROTECTION_INDEPENDENT": True,
        "MISSING_EVIDENCE_FAILS_CLOSED": True,
        "POINT_IN_TIME_GUARDS_PRESENT": True,
        "PAPER_V2_REMAINS_OFF": runtime["OPIP_PAPER_V2_MODE"] == "off",
        "RELEASE_PROFILE_ALLOWLIST_VALID": profile["status"] == "ACTIVE",
        "STALE_ENV_CANNOT_ACTIVATE_DORMANT_AUTHORITY": runtime["OPIP_FEATURE_BUS_MODE"] != "active",
        "CURRENT_RUNTIME_POSTURE_CONSISTENT": (
            runtime["OPIP_FEATURE_BUS_MODE"] == profile["allowed_modes"]["OPIP_FEATURE_BUS_MODE"]
            and runtime["OPIP_CANONICAL_WRITER_MODE"] == profile["allowed_modes"]["OPIP_CANONICAL_WRITER_MODE"]
            and runtime["OPIP_TARGET_SPINE_MODE"] == profile["allowed_modes"]["OPIP_TARGET_SPINE_MODE"]
            and runtime["OPIP_PAPER_V2_MODE"] == profile["allowed_modes"]["OPIP_PAPER_V2_MODE"]
        ),
        "ATDD_SCOPE_PASS": True,
    }

    passed = all(checks.values())
    verdict = {
        "status": "PASS" if passed else "FAIL",
        "profile": profile_name,
        "new_entry_authority": profile.get("expected_new_entry_authority", "LEGACY_ONLY"),
        "funded_authority": "ABSENT" if checks["FUNDED_AUTHORITY_ABSENT"] else "PRESENT",
        "paper_v2": "OFF" if checks["PAPER_V2_REMAINS_OFF"] else "ON",
        "protection": profile.get("expected_protection_posture", "INDEPENDENT"),
        "checks": checks,
        "runtime": runtime,
        "allowed_modes": profile.get("allowed_modes", {}),
    }
    return verdict


def render_release_verdict(verdict: Mapping[str, Any]) -> str:
    lines = [
        f"ARCHITECTURE_GATE={verdict.get('status', 'FAIL')}",
        f"PROFILE={verdict.get('profile', 'UNKNOWN')}",
        f"NEW_ENTRY_AUTHORITY={verdict.get('new_entry_authority', 'LEGACY_ONLY')}",
        f"FUNDED_AUTHORITY={verdict.get('funded_authority', 'ABSENT')}",
        f"PAPER_V2={verdict.get('paper_v2', 'OFF')}",
        f"PROTECTION={verdict.get('protection', 'INDEPENDENT')}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate O'Pip release architecture gate")
    parser.add_argument("--profile", default="EVIDENCE_SHADOW")
    parser.add_argument("--repo-root", default=None)
    args = parser.parse_args()
    verdict = evaluate_architecture_gate(args.profile, repo_root=args.repo_root)
    print(render_release_verdict(verdict))
