"""Consume persisted Tradevisor evidence into a measured report.

This is the consumption disposition for Tradevisor learning artifacts
(governance principle 21). Before this job existed, decision records were
persisted in the advisory inbox with no consumer — orphaned evidence.

Boundaries
----------
- Read-only with respect to the inbox: nothing is rewritten, promoted or
  deleted.
- No policy change is produced; ``policy_promotion`` is always ``NONE``.
- Outcomes are measured only from forward candles, never from the frozen plan,
  and a touch is never treated as a fill.
- The exact-SHA gate is reused unchanged: a worker that is not the exact
  production release publishes nothing (``RELEASE_DRIFT``/``UNVERIFIED``).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.exchanges.kraken import KrakenClient
from app.opip.learning.job_disposition import (
    BLOCKED_RELEASE_DRIFT,
    CONSUMED_EMPTY,
    CONSUMED_OK,
    write_consumption_summary,
)
from app.services import tradevisor_inbox as inbox
from app.services import tradevisor_learning as learning

JOB_NAME = "tradevisor_learning"
DEFAULT_MEASUREMENT_HOURS = 24
CANDLE_INTERVAL_MINUTES = 60
CANDLE_TIMEOUT_SECONDS = 5.0


def resolution_data_root(environ: dict[str, str] | None = None) -> Path | None:
    """Resolve where consumption summaries belong, or None when unavailable.

    The learning plane supplies ``OPIP_LEARNING_DATA_ROOT``; the trading
    container's own ``/app/data`` is the fallback so an operator running the
    job locally still records a disposition.
    """
    env = os.environ if environ is None else environ
    candidate = env.get("OPIP_LEARNING_DATA_ROOT") or "/app/data"
    try:
        root = Path(candidate)
        return root if root.is_dir() else None
    except OSError:
        return None


def consumption_disposition(report: dict) -> str:
    """Map a report to the existing terminal disposition vocabulary."""
    if str(report.get("release_compatibility")) != learning.RELEASE_CURRENT:
        return BLOCKED_RELEASE_DRIFT
    if str(report.get("status")) == "NO_EVIDENCE":
        return CONSUMED_EMPTY
    return CONSUMED_OK


def record_consumption(
    report: dict,
    *,
    data_root: Path | str | None,
    outcomes: dict | None = None,
) -> str | None:
    """Persist the consumption disposition. Never promotes policy."""
    if data_root is None:
        return None
    disposition = consumption_disposition(report)
    payload = {
        "schema_version": "tradevisor-learning-consumption-v1",
        "status": report.get("status"),
        "release_compatibility": report.get("release_compatibility"),
        "cohorts": sorted((report.get("cohorts") or {}).keys()),
        "outcomes_measured": sum(
            1
            for outcome in (outcomes or {}).values()
            if str(outcome.get("status")) == learning.OUTCOME_MEASURED
        ),
        "basis": report.get("basis"),
        "policy_promotion": report.get("policy_promotion", "NONE"),
    }
    try:
        write_consumption_summary(
            data_root,
            job=JOB_NAME,
            disposition=disposition,
            payload=payload,
        )
    except (OSError, ValueError, TypeError):
        return None
    return disposition


def kraken_candle_provider(
    market: str,
    decided_at: datetime,
    *,
    client: KrakenClient | None = None,
):
    """Public OHLC history only, bounded to the measurement window."""
    since = int((decided_at - timedelta(hours=1)).timestamp())
    resolved = client or KrakenClient(timeout_seconds=CANDLE_TIMEOUT_SECONDS)
    return resolved.get_ohlc(market, interval=CANDLE_INTERVAL_MINUTES, since=since)


def build_report(
    *,
    now: datetime | None = None,
    records=None,
    deployed_sha: str | None = None,
    expected_sha: str | None = None,
    candles_provider=None,
    measurement_hours: int = DEFAULT_MEASUREMENT_HOURS,
    native_metrics=None,
) -> dict:
    """Measure every stored record and build the cohort report."""
    rows = inbox.iter_records() if records is None else list(records)
    evaluated_at = now or datetime.now(timezone.utc)
    deployed = (
        deployed_sha if deployed_sha is not None else os.getenv("OPIP_DEPLOYED_SHA", "")
    )
    expected = (
        expected_sha if expected_sha is not None else os.getenv("EXPECTED_SHA", "")
    )

    outcomes: dict[str, dict] = {}
    # The exact-SHA gate runs BEFORE any measurement. A drifted or unverifiable
    # worker must not read candles, compute outcomes, or expose them: previously
    # the gate ran inside build_learning_report(), which cleared the cohorts but
    # still returned (and persisted) computed outcome evidence.
    release = learning.classify_release_compatibility(deployed, expected)
    if release != learning.RELEASE_CURRENT:
        report = learning.build_learning_report(
            rows,
            deployed_sha=deployed,
            expected_sha=expected,
            outcomes={},
            native_metrics=native_metrics,
        )
        return {"report": report.as_dict(), "outcomes": {}}

    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get("dedup_id") or "")
        if not key:
            continue
        # A missing provider leaves the outcome PENDING rather than inventing one.
        outcomes[key] = learning.evaluate_outcome(
            row,
            now=evaluated_at,
            candles_provider=candles_provider,
            measurement_hours=measurement_hours,
            candle_interval=timedelta(minutes=CANDLE_INTERVAL_MINUTES),
        )

    report = learning.build_learning_report(
        rows,
        deployed_sha=deployed,
        expected_sha=expected,
        outcomes=outcomes,
        native_metrics=native_metrics,
    )
    return {"report": report.as_dict(), "outcomes": outcomes}


def main() -> None:
    result = build_report(candles_provider=kraken_candle_provider)
    report = result["report"]
    disposition = record_consumption(
        report,
        data_root=resolution_data_root(),
        outcomes=result["outcomes"],
    )
    print("===== OHM TRADEVISOR OUTCOME MEASUREMENT =====")
    print("Status:", report["status"])
    print("Release compatibility:", report["release_compatibility"])
    print("Policy promotion:", report["policy_promotion"])
    print("Basis:", report["basis"])
    for cohort, metrics in report["cohorts"].items():
        print(f"Cohort {cohort}:")
        for key in (
            "observed",
            "actionable",
            "rejected",
            "measured",
            "entry_never_touched",
            "false_positive_rate",
            "mean_mfe_pct",
            "mean_mae_pct",
            "estimated_net_usd",
        ):
            print(f"  {key}: {metrics.get(key)}")
        if metrics.get("rejection_reasons"):
            print(f"  rejection_reasons: {metrics['rejection_reasons']}")
    print("Incremental value:", report["incremental_value"].get("status"))
    print("Consumption disposition:", disposition or "NOT_RECORDED")
    print("Trade authority changed: False")


if __name__ == "__main__":
    main()
