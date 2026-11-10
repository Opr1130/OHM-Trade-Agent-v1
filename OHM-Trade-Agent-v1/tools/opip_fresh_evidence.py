"""Bounded, prospective, evidence-only snapshots; never trains or trades.

Runs as an isolated scheduler invocation. Inputs are existing O'Pip persistence;
a source record is NOT evidence of a manual exchange fill. No network imports.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

DEFAULT_ROOT = Path("/app/data")
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_IDENTITIES = 10000
SCHEMA = "opip-fresh-evidence-v1"
SOURCES = {
    "TRADE_OUTCOME": ("trade_outcomes.json", "map"),
    "TRADEVISOR_INBOX": ("tradevisor_v1_inbox.json", "events"),
    "TELEGRAM_DELIVERY": ("telegram_delivery_events.jsonl", "jsonl"),
}
SAFE_TRADE_FIELDS = (
    "trade_id", "signal_id", "journey_id", "symbol", "direction",
    "recommendation_timestamp", "recommendation_action", "chief_decision",
    "technical_score", "planned_entry_low", "planned_entry_high",
    "planned_stop", "planned_target_1", "planned_target_2",
    "entered_trade", "entered_at", "entry_price", "entry_price_source",
    "terminal_status", "terminal_timestamp", "terminal_reason",
    "target_1_observed", "target_2_observed", "stop_observed",
    "mfe_pct", "mae_pct",
)
SAFE_INBOX_FIELDS = ("dedup_id", "received_at", "processed_at", "status", "decision", "final_disposition")
SAFE_DELIVERY_FIELDS = ("alert_id", "signal_id", "journey_id", "trade_id", "pair",
    "attempted_at", "status", "delivered", "alert_family", "event_type")
TIME_KEYS = {
    "TRADE_OUTCOME": ("recommendation_timestamp", "entered_at", "terminal_timestamp"),
    "TRADEVISOR_INBOX": ("received_at", "processed_at"),
    "TELEGRAM_DELIVERY": ("attempted_at",),
}


def _utc(value: Any) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if ts.tzinfo is None or ts.utcoffset() is None:
            return None
        return ts.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def _read(path: Path, *, max_bytes: int) -> Any:
    if path.is_symlink():
        raise ValueError("symlink source is forbidden")
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > max_bytes:
        raise ValueError("source is not a bounded regular file")
    return path.read_text(encoding="utf-8")


def _iter_source(kind: str, raw: str):
    if kind == "jsonl":
        for line in raw.splitlines():
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict):
                    yield row
        return
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("source root must be object")
    events = value.get("events") if kind == "events" else value
    if not isinstance(events, dict):
        raise ValueError("events must be object")
    for identity, row in events.items():
        if isinstance(row, dict):
            yield {"_source_key": str(identity), **row}


def _project(family: str, row: dict[str, Any]) -> dict[str, Any]:
    fields = {
        "TRADE_OUTCOME": SAFE_TRADE_FIELDS,
        "TRADEVISOR_INBOX": SAFE_INBOX_FIELDS,
        "TELEGRAM_DELIVERY": SAFE_DELIVERY_FIELDS,
    }[family]
    out = {k: row.get(k) for k in fields}
    if family == "TRADEVISOR_INBOX":
        signal = row.get("signal")
        if isinstance(signal, dict):
            out["market"] = signal.get("market")
            out["signal"] = signal.get("signal")
            out["triggered_at"] = signal.get("triggered_at")
        decision = row.get("decision_record")
        if isinstance(decision, dict):
            out["decision_record_id"] = decision.get("decision_id")
    if family == "TRADE_OUTCOME":
        # Never infer realized exchange P&L from the trade plan or target touches.
        out["realized_net_profit_usd"] = None
        out["fill_verification"] = "NOT_CONNECTED"
    return out


def _hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _identity(family: str, row: dict[str, Any]) -> str | None:
    if family == "TRADE_OUTCOME":
        return str(row.get("record_key") or row.get("trade_id") or "") or None
    if family == "TRADEVISOR_INBOX":
        return str(row.get("dedup_id") or row.get("_source_key") or "") or None
    return str(row.get("alert_id") or "") + ":" + str(row.get("attempt") or "") + ":" + str(row.get("attempted_at") or "") if row.get("alert_id") else None


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
        prefix=".fresh-state-", delete=False) as handle:
        tmp = Path(handle.name)
        json.dump(value, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def capture(root: Path, *, now: datetime | None = None, max_bytes: int = MAX_SOURCE_BYTES,
    state_path: Path | None = None, ledger_path: Path | None = None) -> dict[str, Any]:
    """Append changed, prospective evidence only; failed inputs never advance state."""
    now = now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        raise ValueError("now must be timezone aware")
    state_path = state_path or root / "opip" / "fresh_evidence" / "state.json"
    ledger_path = ledger_path or root / "opip" / "fresh_evidence" / "events.jsonl"
    if state_path.is_symlink() or ledger_path.is_symlink():
        raise ValueError("symlink destination is forbidden")
    if state_path.exists():
        state = json.loads(_read(state_path, max_bytes=max_bytes))
        if state.get("schema") != SCHEMA:
            raise ValueError("incompatible state schema")
    else:
        state = {"schema": SCHEMA, "started_at": now.isoformat(), "hashes": {}}
    epoch = _utc(state["started_at"])
    if epoch is None:
        raise ValueError("invalid activation timestamp")
    hashes = dict(state["hashes"])
    if len(hashes) > MAX_IDENTITIES:
        raise ValueError("identity capacity exceeded")
    staged = []
    coverage = {}
    for family, (filename, kind) in SOURCES.items():
        raw = _read(root / filename, max_bytes=max_bytes)
        if raw is None:
            coverage[family] = "MISSING"
            continue
        coverage[family] = "PRESENT"
        for row in _iter_source(kind, raw):
            stamps = [_utc(row.get(key)) for key in TIME_KEYS[family]]
            if not any(t is not None and epoch <= t <= now for t in stamps):
                continue
            identity = _identity(family, row)
            if not identity:
                continue
            fields = _project(family, row)
            key = family + ":" + identity
            digest = _hash(fields)
            if hashes.get(key) == digest:
                continue
            hashes[key] = digest
            staged.append({"schema": SCHEMA, "captured_at": now.isoformat(),
                "source": family, "source_id": identity, "source_hash": digest,
                "evidence": fields, "model_promotion": False,
                "trade_authority_changed": False})
            if len(hashes) > MAX_IDENTITIES:
                raise ValueError("identity capacity exceeded")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    if staged:
        with ledger_path.open("a", encoding="utf-8") as file:
            for item in staged:
                file.write(json.dumps(item, sort_keys=True, default=str, allow_nan=False) + "\n")
            file.flush()
            os.fsync(file.fileno())
    state["hashes"] = hashes
    state["last_capture_at"] = now.isoformat()
    _atomic_json(state_path, state)
    return {"status": "CAPTURED", "new_rows": len(staged), "coverage": coverage,
        "epoch": state["started_at"], "training": "OFF", "execution": "OFF"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    if os.getenv("OPIP_FRESH_EVIDENCE_ENABLED", "false").lower() not in ("true", "1"):
        print(json.dumps({"status": "DISABLED", "training": "OFF", "execution": "OFF"}))
        return
    print(json.dumps(capture(args.root), sort_keys=True))


if __name__ == "__main__":
    main()
