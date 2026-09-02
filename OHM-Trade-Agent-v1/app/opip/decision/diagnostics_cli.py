"""Read-only CLI for recent O'Pip qualification diagnostics."""

from __future__ import annotations

import argparse

from app.opip.decision.summary import (
    build_recent_qualification_funnel,
    render_recent_qualification_funnel,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()
    report = build_recent_qualification_funnel(window_hours=max(1, args.hours))
    print(render_recent_qualification_funnel(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
