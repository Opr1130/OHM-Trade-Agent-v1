from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

_TRIGGER_RE = re.compile(
    r"@claude(?:\s+(?P<profile>cheap|standard|deep))?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExecutionProfile:
    name: str
    provider: str
    model: str
    max_turns: int
    max_budget_usd: float

    def github_outputs(self) -> Mapping[str, str]:
        return {
            "profile": self.name,
            "provider": self.provider,
            "model": self.model,
            "max_turns": str(self.max_turns),
            "max_budget_usd": f"{self.max_budget_usd:.2f}",
        }


PROFILES: dict[str, ExecutionProfile] = {
    "cheap": ExecutionProfile(
        name="cheap",
        provider="anthropic",
        model="haiku",
        max_turns=6,
        max_budget_usd=0.40,
    ),
    "standard": ExecutionProfile(
        name="standard",
        provider="anthropic",
        model="sonnet",
        max_turns=12,
        max_budget_usd=1.50,
    ),
    "deep": ExecutionProfile(
        name="deep",
        provider="anthropic",
        model="sonnet",
        max_turns=20,
        max_budget_usd=3.50,
    ),
}


def profile_name_from_trigger(text: str) -> str:
    match = _TRIGGER_RE.search(text or "")
    if not match:
        raise ValueError("trigger must contain @claude")
    return (match.group("profile") or "standard").lower()


def resolve_profile(name: str) -> ExecutionProfile:
    key = str(name or "").strip().lower()
    try:
        return PROFILES[key]
    except KeyError as exc:
        allowed = ", ".join(PROFILES)
        raise ValueError(
            f"unknown AI execution profile {name!r}; allowed: {allowed}"
        ) from exc


def resolve_from_trigger(text: str) -> ExecutionProfile:
    return resolve_profile(profile_name_from_trigger(text))


def write_github_outputs(
    profile: ExecutionProfile,
    output_path: str | os.PathLike[str],
) -> None:
    path = Path(output_path)
    with path.open("a", encoding="utf-8") as handle:
        for key, value in profile.github_outputs().items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve the bounded O'Pip AI coding profile from an @claude trigger."
    )
    parser.add_argument("--trigger", required=True)
    parser.add_argument(
        "--github-output",
        default=os.getenv("GITHUB_OUTPUT"),
        help="GitHub Actions output file. Defaults to GITHUB_OUTPUT.",
    )
    args = parser.parse_args()

    profile = resolve_from_trigger(args.trigger)
    if args.github_output:
        write_github_outputs(profile, args.github_output)

    print(
        f"profile={profile.name} provider={profile.provider} model={profile.model} "
        f"max_turns={profile.max_turns} max_budget_usd={profile.max_budget_usd:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
