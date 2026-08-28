"""Bounded model/profile resolver for the O'Pip Gemini review gateway."""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GeminiReviewProfile:
    name: str
    model: str
    thinking_level: str
    max_output_tokens: int
    max_diff_bytes: int


PROFILES = {
    "quick-review": GeminiReviewProfile(
        name="quick-review",
        model="gemini-3.7-flash",
        thinking_level="medium",
        max_output_tokens=8192,
        max_diff_bytes=200_000,
    ),
    "architecture-review": GeminiReviewProfile(
        name="architecture-review",
        model="gemini-3.1-pro-preview",
        thinking_level="high",
        max_output_tokens=16_384,
        max_diff_bytes=350_000,
    ),
    "ml-audit": GeminiReviewProfile(
        name="ml-audit",
        model="gemini-3.1-pro-preview",
        thinking_level="high",
        max_output_tokens=16_384,
        max_diff_bytes=350_000,
    ),
    "security-review": GeminiReviewProfile(
        name="security-review",
        model="gemini-3.1-pro-preview",
        thinking_level="high",
        max_output_tokens=16_384,
        max_diff_bytes=250_000,
    ),
}

_TRIGGER = re.compile(
    r"@gemini(?:\s+(quick-review|architecture-review|ml-audit|security-review))?\b",
    re.IGNORECASE,
)


def resolve_profile(trigger: str) -> GeminiReviewProfile:
    match = _TRIGGER.search(trigger or "")
    if not match:
        raise ValueError("Gemini trigger not found")
    profile_name = (match.group(1) or "quick-review").lower()
    return PROFILES[profile_name]


def _write_github_outputs(profile: GeminiReviewProfile) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    rows = {
        "profile": profile.name,
        "model": profile.model,
        "thinking_level": profile.thinking_level,
        "max_output_tokens": str(profile.max_output_tokens),
        "max_diff_bytes": str(profile.max_diff_bytes),
    }
    with Path(output_path).open("a", encoding="utf-8") as handle:
        for key, value in rows.items():
            handle.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trigger", required=True)
    args = parser.parse_args()

    try:
        profile = resolve_profile(args.trigger)
    except ValueError as exc:
        parser.error(str(exc))

    _write_github_outputs(profile)
    print(
        f"profile={profile.name} model={profile.model} "
        f"thinking={profile.thinking_level} "
        f"max_output_tokens={profile.max_output_tokens} "
        f"max_diff_bytes={profile.max_diff_bytes}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
