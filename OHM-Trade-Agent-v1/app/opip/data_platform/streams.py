from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StreamSpec:
    name: str
    relative_path: Path
    kind: str
    required: bool = False


STREAM_SPECS: tuple[StreamSpec, ...] = (
    StreamSpec(
        "screening_evaluations",
        Path("opip/qualification/screening_evaluations.jsonl"),
        "screening",
        True,
    ),
    StreamSpec(
        "funnel_events",
        Path("opip/qualification/funnel_events.jsonl"),
        "funnel",
        True,
    ),
    StreamSpec(
        "scan_summaries",
        Path("opip/qualification/scan_summaries.jsonl"),
        "generic",
        True,
    ),
    StreamSpec(
        "intelligence_events",
        Path("intelligence_learning/events.jsonl"),
        "intelligence",
        True,
    ),
    StreamSpec(
        "full_market_observations",
        Path("full_market_observations.jsonl"),
        "market_observation",
        True,
    ),
    StreamSpec(
        "p1_shadow_outbox",
        Path("p1_shadow_outbox.jsonl"),
        "generic",
    ),
    StreamSpec(
        "p1_evidence_ledger",
        Path("p1_evidence_ledger.jsonl"),
        "generic",
    ),
    StreamSpec(
        "paper_trade_events",
        Path("paper_trading/events.jsonl"),
        "paper_event",
    ),
    StreamSpec(
        "telegram_delivery_events",
        Path("telegram_delivery_events.jsonl"),
        "generic",
    ),
    StreamSpec(
        "decision_telemetry",
        Path("decision_telemetry.jsonl"),
        "generic",
    ),
    StreamSpec(
        "trade_quality_evidence",
        Path("opip_trade_quality_evidence_v1.jsonl"),
        "generic",
    ),
    StreamSpec(
        "candidate_trace",
        Path("candidate_trace.jsonl"),
        "generic",
    ),
    StreamSpec(
        "opportunity_accountability",
        Path("opip/opportunity_accountability.jsonl"),
        "generic",
    ),
)


def resolve_streams(data_root: Path) -> list[tuple[StreamSpec, Path]]:
    return [(spec, data_root / spec.relative_path) for spec in STREAM_SPECS]
