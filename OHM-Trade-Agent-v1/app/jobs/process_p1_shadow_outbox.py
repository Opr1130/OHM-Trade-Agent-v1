"""Drain the P1 local outbox into the immutable evidence ledger.

This job performs no market scanning and no trade/execution action.
"""

from app.services.p1_shadow_outbox import drain_outbox_to_evidence_ledger, outbox_health


def main() -> None:
    before = outbox_health()
    result = drain_outbox_to_evidence_ledger()
    after = outbox_health()
    print("P1 shadow outbox backlog before:", before["backlog_rows"])
    print("P1 shadow snapshots processed:", result.processed)
    print("P1 shadow duplicates:", result.duplicates)
    print("P1 shadow malformed:", result.malformed)
    print("P1 shadow stopped on error:", result.stopped_on_error)
    print("P1 shadow outbox backlog after:", after["backlog_rows"])
    print("Trade authority changed:", False)


if __name__ == "__main__":
    main()
