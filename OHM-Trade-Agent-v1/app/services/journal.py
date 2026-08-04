import json
from pathlib import Path

from app.models.signal import SignalDecision, TradingSignal


JOURNAL_PATH = Path("data/signals.jsonl")


def append_signal(signal: TradingSignal, decision: SignalDecision) -> None:
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "signal": signal.model_dump(mode="json"),
        "decision": decision.model_dump(mode="json"),
    }
    with JOURNAL_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
