"""Tracked Telegram acceptance (offline): proves acknowledgement + dedupe evidence.

Uses the CERTIFIED tracked path (app.services.telegram_delivery.send_tracked_telegram)
with a mocked transport, so no network call and no real credential is involved.

Distinguishes explicitly:
  transport_only  - sendMessage returned a message id, nothing recorded
  tracked         - delivery event recorded with a stable message id
  actionable      - tracked + dedupe stable (a real signal delivery contract)

Run: python scripts/telegram_tracked_acceptance.py --out <json> [--app-root <cert>]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
CERTIFIED_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--app-root", default=str(Path(os.environ.get("OPIP_CERTIFIED_SRC", r"C:\Users\ohmpr\opip-mission-07\src")) / "OHM-Trade-Agent-v1"))
    parser.add_argument("--work", default="")
    args = parser.parse_args()

    app_root = Path(args.app_root)
    sys.path.insert(0, str(app_root))
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))

    work = Path(args.work) if args.work else Path(args.out).parent / "telegram-work"
    work.mkdir(parents=True, exist_ok=True)
    state = work / "delivery-state.json"
    events = work / "delivery-events.jsonl"
    for path in (state, events):
        if path.exists():
            path.unlink()

    os.environ.setdefault("WEBHOOK_SECRET", "r1-offline-acceptance-secret-value")
    os.environ["TELEGRAM_ENABLED"] = "false"  # never start a poller

    from app.services import telegram_delivery as td
    import opip_signals

    sent: list[int] = []

    def fake_send(token: str, chat: str, message: str) -> int:
        sent.append(len(sent) + 1)
        return 9000 + len(sent)

    td.send_telegram_message_with_id = fake_send  # mock transport (no network)

    first = td.send_tracked_telegram(
        bot_token="offline-not-a-real-token", chat_id="offline", message="tracked probe",
        identity="R1PROBE", alert_family="ACTIONABLE", event_type="NEW_ENTRY",
        fingerprint="r1-fp", state_file=state, event_file=events,
    )
    second = td.send_tracked_telegram(
        bot_token="offline-not-a-real-token", chat_id="offline", message="tracked probe",
        identity="R1PROBE", alert_family="ACTIONABLE", event_type="NEW_ENTRY",
        fingerprint="r1-fp", state_file=state, event_file=events,
    )
    rows = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines() if line.strip()]
    canonical = td.canonical_message_id("R1PROBE", state_file=state)
    canonical_again = td.canonical_message_id("R1PROBE", state_file=state)

    evaluation = opip_signals.evaluate_telegram_evidence(
        transport_probe={"ok": first.message_id is not None, "message_id": first.message_id},
        events=rows,
    )
    dedupe = opip_signals.evaluate_dedupe(identity="R1PROBE", id_before=canonical, id_after=canonical_again)
    duplicate_detected = opip_signals.evaluate_dedupe(
        identity="R1PROBE", id_before=canonical, id_after=(canonical or 0) + 1, expected_duplicate=True)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "certified_sha": CERTIFIED_SHA,
        "transport": "MOCKED (no network, no real credential)",
        "first_status": first.status,
        "second_status": second.status,
        "canonical_message_id": canonical,
        "events_recorded": len(rows),
        "transport_only_probe": evaluation["transport_only"],
        "tracked_delivery": evaluation["verdict"] == "PASS",
        "dedupe": dedupe,
        "duplicate_probe": duplicate_detected,
        "verdict": "PASS" if evaluation["verdict"] == "PASS" and dedupe["verdict"] == "PASS"
                   and duplicate_detected["verdict"] == "PASS" else "FAIL",
        "note": "A mocked pass proves the tracked-delivery contract, not live Telegram connectivity.",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("verdict", "tracked_delivery", "transport_only_probe",
                                             "canonical_message_id", "events_recorded")}))
    return 0 if payload["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
