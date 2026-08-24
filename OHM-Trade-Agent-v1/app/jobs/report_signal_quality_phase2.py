from __future__ import annotations

import json

from app.services.signal_quality_phase2 import run_phase2_replay


def main() -> None:
    report = run_phase2_replay()
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
