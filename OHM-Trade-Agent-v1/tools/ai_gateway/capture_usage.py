from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def _load_execution_messages(path: Path | None) -> list[dict[str, Any]]:
    if path is None or not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(raw, dict):
        return [raw]
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _result_message(messages: Iterable[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in messages:
        if item.get("type") == "result":
            result = item
    return result


def _sum_usage(messages: Iterable[dict[str, Any]], key: str) -> int:
    total = 0
    for item in messages:
        usage = item.get("usage")
        if not isinstance(usage, dict):
            message = item.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
        if not isinstance(usage, dict):
            continue
        try:
            total += int(usage.get(key) or 0)
        except (TypeError, ValueError):
            continue
    return total


def build_usage_record(
    *,
    execution_file: Path | None,
    profile: str,
    provider: str,
    model: str,
    max_turns: int,
    max_budget_usd: float,
    repository: str,
    run_id: str,
    event_name: str,
    actor: str,
    action_conclusion: str,
) -> dict[str, Any]:
    messages = _load_execution_messages(execution_file)
    result = _result_message(messages)

    cost = result.get("total_cost_usd")
    try:
        actual_cost_usd = round(float(cost), 6) if cost is not None else None
    except (TypeError, ValueError):
        actual_cost_usd = None

    turns = result.get("num_turns")
    try:
        num_turns = int(turns) if turns is not None else None
    except (TypeError, ValueError):
        num_turns = None

    status = (
        "observed"
        if result
        else (
            "execution_file_missing"
            if execution_file is None or not execution_file.is_file()
            else "result_record_missing"
        )
    )

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source": "github-actions",
        "provider": provider,
        "profile": profile,
        "model": model,
        "limits": {
            "max_turns": int(max_turns),
            "max_budget_usd": round(float(max_budget_usd), 2),
        },
        "observed": {
            "status": status,
            "action_conclusion": action_conclusion or "unknown",
            "actual_cost_usd": actual_cost_usd,
            "num_turns": num_turns,
            "input_tokens": _sum_usage(messages, "input_tokens"),
            "output_tokens": _sum_usage(messages, "output_tokens"),
            "cache_creation_input_tokens": _sum_usage(
                messages, "cache_creation_input_tokens"
            ),
            "cache_read_input_tokens": _sum_usage(
                messages, "cache_read_input_tokens"
            ),
        },
        "github": {
            "repository": repository,
            "run_id": str(run_id),
            "event_name": event_name,
            "actor": actor,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write non-sensitive O'Pip AI coding usage telemetry."
    )
    parser.add_argument("--execution-file", default="")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--max-turns", required=True, type=int)
    parser.add_argument("--max-budget-usd", required=True, type=float)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--actor", required=True)
    parser.add_argument("--action-conclusion", default="")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    execution_file = Path(args.execution_file) if args.execution_file else None
    record = build_usage_record(
        execution_file=execution_file,
        profile=args.profile,
        provider=args.provider,
        model=args.model,
        max_turns=args.max_turns,
        max_budget_usd=args.max_budget_usd,
        repository=args.repository,
        run_id=args.run_id,
        event_name=args.event_name,
        actor=args.actor,
        action_conclusion=args.action_conclusion,
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    observed = record["observed"]
    print(
        f"profile={record['profile']} model={record['model']} "
        f"cost={observed['actual_cost_usd']} turns={observed['num_turns']} "
        f"status={observed['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
