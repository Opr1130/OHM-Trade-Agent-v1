"""Source-backed inventory of O'Pip business storage and its PostgreSQL target (Phase 1).

Phase 1 goal (owner-approved direction): PostgreSQL becomes the sole authoritative store for NEW
business records after an approved cutover. Historical migration (Phase 2) and legacy-file
retirement (Phase 3) are out of scope.

This module is *data only*. It changes no runtime behavior, opens no file and imports no driver.
It exists so the schema-gap matrix is reviewable and cannot rot: a governance test fails when a
new ``/app/data`` path appears in ``app/`` without being classified here, and when a claimed
PostgreSQL target table does not exist in the checked-in migrations.

Honesty rules for the fields:

``basis``
    ``"source"`` means the classification follows from checked-in code (``STREAM_SPECS``, the
    shipper's typed projections, the migrations). ``"path"`` means it was assigned from the file
    name and the owning code's evident purpose and still needs owner confirmation.
``write`` / ``read``
    What exists TODAY. ``SHIPPED_FROM_FILE`` = the analytics shipper copies appended file rows into
    PostgreSQL (files stay authoritative). ``API_ONLY`` = a PostgreSQL store API exists but no
    production call site uses it. Nothing in Phase 1 is ``DIRECT`` until an approved cutover.
"""

from __future__ import annotations

from dataclasses import dataclass

# Kinds ---------------------------------------------------------------------------------------
BUSINESS_RECORD = "BUSINESS_RECORD"  # new records should become PostgreSQL-authoritative
STATE = "STATE"  # mutable runtime state / checkpoint; moves only with its owning writer
CACHE = "CACHE"  # derived or re-fetchable
ARCHIVE = "ARCHIVE"  # rotated history; Phase 2 concern
LOCK = "LOCK"  # process coordination
SAFETY_CRITICAL = "SAFETY_CRITICAL"  # F11 / protection / order authority: never PostgreSQL-gated

# Schema completeness ----------------------------------------------------------------------------
YES, PARTIAL, NO, NOT_APPLICABLE = "YES", "PARTIAL", "NO", "N/A"

# Domains (owner's list) -------------------------------------------------------------------------
SCANNER = "Scanner"
QUALIFICATION = "Qualification"
V2_SCORING = "V2 scoring"
AI = "AI advisory and cost"
TRADEVISOR = "Tradevisor"
TELEGRAM = "Telegram delivery"
LIFECYCLE = "Opportunity lifecycle"
OUTCOMES = "Trade outcomes"
CONFIG = "Configuration"
OPS = "Operations and evidence"

DOMAINS = (
    SCANNER,
    QUALIFICATION,
    V2_SCORING,
    AI,
    TRADEVISOR,
    TELEGRAM,
    LIFECYCLE,
    OUTCOMES,
    CONFIG,
    OPS,
)


@dataclass(frozen=True)
class StorageEntry:
    """One file or directory under ``/app/data`` (path is relative to it)."""

    path: str
    domain: str
    kind: str
    pg_target: str | None = None
    schema: str = NO
    write: str = "NONE"
    read: str = "NONE"
    basis: str = "path"
    note: str = ""

    @property
    def is_directory(self) -> bool:
        return not self.path.rsplit("/", 1)[-1].count(".")

    @property
    def covers_children(self) -> bool:
        """Whether files below this directory are classified by it (a directory-only entry is not)."""
        return self.is_directory and self.path not in _DIRECTORY_ONLY


#: Directories whose files must each be classified individually; the directory entry itself only
#: records that the directory is referenced.
_DIRECTORY_ONLY = frozenset({"opip/qualification"})


def _e(path, domain, kind, pg=None, schema=NO, write="NONE", read="NONE", basis="path", note=""):
    return StorageEntry(path, domain, kind, pg, schema, write, read, basis, note)


_SHIP = "SHIPPED_FROM_FILE"
_DASH = "DASHBOARD_READ_MODEL"

INVENTORY: tuple[StorageEntry, ...] = (
    # --- Scanner -----------------------------------------------------------------------------
    _e("opip/qualification/screening_evaluations.jsonl", SCANNER, BUSINESS_RECORD,
       "market.screening", YES, _SHIP, _DASH, "source", "typed projection by the shipper"),
    _e("opip/qualification/screening_evaluations_archive", SCANNER, ARCHIVE, note="rotated history; Phase 2"),
    _e("opip/qualification/scan_summaries.jsonl", SCANNER, BUSINESS_RECORD,
       "raw.ingested_event", PARTIAL, _SHIP, "NONE", "source", "raw JSON only; no typed scan table"),
    _e("scan_activity.jsonl", SCANNER, BUSINESS_RECORD, None, NO, "NONE", "NONE", "path",
       "not a registered stream; no PostgreSQL home"),
    _e("scan", SCANNER, CACHE, note="scan working artifacts"),
    _e("full_market_observations.jsonl", SCANNER, BUSINESS_RECORD,
       "market.observation", YES, _SHIP, "NONE", "source", "typed projection by the shipper"),
    _e("full_market_observation_state.json", SCANNER, STATE),
    _e("full_market_observation_alert_governor_state.json", SCANNER, STATE),
    _e("full_market_transition_detections.jsonl", SCANNER, BUSINESS_RECORD, note="shadow detections; not a stream"),
    _e("full_market_transition_outcomes.jsonl", OUTCOMES, BUSINESS_RECORD, note="hit_2/5/10/20pct are MFE-based"),
    _e("full_market_transition_capture_state.json", SCANNER, STATE),
    _e("full_market_transition_outcome_state.json", OUTCOMES, STATE),
    _e("movement_discovery_v2_1.jsonl", SCANNER, BUSINESS_RECORD, note="discovery research stream"),
    _e("movement_discovery_v2_1_outcomes.jsonl", OUTCOMES, BUSINESS_RECORD),
    _e("movement_discovery_v2_1_capture_state.json", SCANNER, STATE),
    _e("movement_discovery_v2_1_outcome_state.json", OUTCOMES, STATE),
    _e("movement_discovery_v2_2_shadow.jsonl", SCANNER, BUSINESS_RECORD, note="shadow only"),
    _e("native_flow_shadow.jsonl", SCANNER, BUSINESS_RECORD, note="shadow only"),
    _e("explosion_precursor_snapshots.jsonl", SCANNER, BUSINESS_RECORD, note="shadow only"),
    _e("explosion_state_snapshots.jsonl", SCANNER, BUSINESS_RECORD, note="shadow only"),
    _e("explosion_state_outcomes.jsonl", OUTCOMES, BUSINESS_RECORD, note="shadow only"),
    _e("explosion_state_outcome_state.json", OUTCOMES, STATE),
    _e("early_watch_scheduler_state.json", SCANNER, STATE),
    _e("learning_scheduler_state.json", SCANNER, STATE),
    _e("opip/discovery", SCANNER, BUSINESS_RECORD, note="attributions / forward_outcomes / dead letter jsonl"),
    _e("opip/discovery/ef01_reconciliation_report.json", OPS, STATE),
    _e("opip/events", SCANNER, STATE, note="event-calendar provider state"),
    _e("opip/streaming", SCANNER, STATE),
    # --- Qualification -----------------------------------------------------------------------
    _e("opip/qualification/funnel_events.jsonl", QUALIFICATION, BUSINESS_RECORD,
       "lifecycle.stage_transition", YES, _SHIP, _DASH, "source",
       "also projects lifecycle.episode and lifecycle.candidate"),
    _e("opip/qualification/funnel_events_archive", QUALIFICATION, ARCHIVE, note="rotated history; Phase 2"),
    _e("opip/qualification", QUALIFICATION, STATE, note="directory only; each file is classified individually"),
    _e("opip/qualification/early_timing_ledger_state.json", QUALIFICATION, STATE),
    _e("candidate_trace.jsonl", QUALIFICATION, BUSINESS_RECORD,
       "raw.ingested_event", PARTIAL, _SHIP, "NONE", "source", "raw JSON only"),
    _e("decision_telemetry.jsonl", QUALIFICATION, BUSINESS_RECORD,
       "raw.ingested_event", PARTIAL, _SHIP, "NONE", "source", "raw JSON only"),
    _e("opip_trade_quality_evidence_v1.jsonl", QUALIFICATION, BUSINESS_RECORD,
       "raw.ingested_event", PARTIAL, _SHIP, "NONE", "source", "raw JSON only"),
    _e("opip/decision/equivalence", QUALIFICATION, BUSINESS_RECORD, note="decision equivalence evidence"),
    _e("opip/committee", QUALIFICATION, BUSINESS_RECORD, note="Committee is OFF; evidence only"),
    # --- V2 scoring --------------------------------------------------------------------------
    _e("opip/qualification/scoring_comparison.jsonl", V2_SCORING, BUSINESS_RECORD, None, NO,
       "NONE", "NONE", "source", "V1/V2 shadow comparison; NOT a registered stream (confirmed gap)"),
    _e("opip/release/runtime.json", V2_SCORING, STATE, note="release provenance stamp"),
    # --- AI advisory and cost ----------------------------------------------------------------
    _e("openai_usage.jsonl", AI, BUSINESS_RECORD, "signal.ai_call", PARTIAL, "API_ONLY", "NONE", "path",
       "tokens only; no USD or pricing provenance in the source rows"),
    _e("opip_ai_router_usage.jsonl", AI, BUSINESS_RECORD, "signal.ai_call", PARTIAL, "API_ONLY", "NONE", "path",
       "tokens only; no USD or pricing provenance in the source rows"),
    _e("chief_review_cache.json", AI, CACHE),
    _e("external_order_reviews.json", AI, BUSINESS_RECORD, note="external AI is advisory evidence"),
    # --- Tradevisor --------------------------------------------------------------------------
    _e("tradevisor_v1_inbox.json", TRADEVISOR, BUSINESS_RECORD, None, NO, "NONE", "NONE", "source",
       "webhook inbox; events + metrics"),
    _e("tradingview_v2_inbox.json", TRADEVISOR, BUSINESS_RECORD),
    # --- Telegram delivery -------------------------------------------------------------------
    _e("telegram_delivery_events.jsonl", TELEGRAM, BUSINESS_RECORD,
       "signal.delivery_event", PARTIAL, "API_ONLY", "NONE", "source",
       "also shipped raw to raw.ingested_event; typed table added in migration 007"),
    _e("telegram_delivery_state.json", TELEGRAM, STATE),
    _e("qualified_alert_outbox.json", TELEGRAM, STATE, note="retry record; mutable"),
    _e("pending_setup_terminal_alert_outbox.json", TELEGRAM, STATE),
    _e("pending_setup_alert_state.json", TELEGRAM, STATE),
    _e("alert_state.json", TELEGRAM, STATE),
    _e("alert_governor_state.json", TELEGRAM, STATE),
    _e("notification_state.json", TELEGRAM, STATE),
    _e("attention_budget_state.json", TELEGRAM, STATE),
    _e("emergency_alert_state.json", TELEGRAM, STATE),
    _e("telegram_market_watches.json", TELEGRAM, STATE),
    _e("trade_monitor_state.json", TELEGRAM, STATE),
    # --- Opportunity lifecycle ---------------------------------------------------------------
    _e("pending_setups.json", LIFECYCLE, STATE, note="mutable registry"),
    _e("active_trades.json", LIFECYCLE, STATE, note="mutable registry"),
    _e("opportunity_monitor_queue.json", LIFECYCLE, STATE),
    _e("opip_entry_watch.json", LIFECYCLE, STATE),
    _e("intelligence_learning/events.jsonl", LIFECYCLE, BUSINESS_RECORD,
       "signal.intelligence_event", YES, _SHIP, _DASH, "source", "typed projection by the shipper"),
    _e("intelligence_learning/journeys.json", LIFECYCLE, STATE),
    _e("intelligence_learning/profile.json", LIFECYCLE, STATE),
    _e("intelligence_learning/freqtrade_ingest_state.json", LIFECYCLE, STATE),
    _e("intelligence_learning/freqtrade_ingest_dedup.sqlite", LIFECYCLE, STATE),
    _e("freqtrade_bridge", LIFECYCLE, STATE),
    _e("paper_trading/events.jsonl", LIFECYCLE, BUSINESS_RECORD,
       "paper.trade_event", YES, _SHIP, "NONE", "source", "typed projection by the shipper"),
    _e("paper_trading/state.json", LIFECYCLE, STATE),
    _e("paper_trading/control.json", LIFECYCLE, STATE),
    _e("paper_trading/evidence_gap_spool.json", LIFECYCLE, STATE),
    _e("order_intents.json", LIFECYCLE, SAFETY_CRITICAL, note="order authority: owner-gated, never PostgreSQL-gated"),
    # --- Trade outcomes ----------------------------------------------------------------------
    _e("trade_outcomes.json", OUTCOMES, BUSINESS_RECORD, "learning.signal_outcome", PARTIAL, "API_ONLY", "NONE",
       "path", "scorecard does not read this file"),
    _e("phase3c_forward_outcomes.jsonl", OUTCOMES, BUSINESS_RECORD, "learning.signal_outcome", PARTIAL,
       "API_ONLY", "NONE", "path", "forward outcomes; outcome class must be labelled"),
    _e("phase3c_verified_edge_report.json", OUTCOMES, CACHE),
    _e("phase3b_shadow_telemetry.jsonl", OUTCOMES, BUSINESS_RECORD, note="shadow only"),
    _e("shadow_learning.json", OUTCOMES, STATE),
    _e("price_movement_learning.json", OUTCOMES, STATE),
    _e("execution_learning.json", OUTCOMES, STATE),
    _e("strategy_calibration_profile.json", OUTCOMES, STATE),
    _e("opip_ml_feature_snapshots_v1", OUTCOMES, BUSINESS_RECORD, "learning.feature_snapshot", PARTIAL, "NONE",
       "NONE", "path", "table exists in 001; nothing writes it"),
    _e("opip_ml_capture_health.json", OUTCOMES, STATE),
    _e("opip_ml_capture_dead_letter.jsonl", OUTCOMES, BUSINESS_RECORD),
    _e("opip_ml_capture_checkpoint.json", OUTCOMES, STATE),
    _e("opip_ml_data_readiness_v1.json", OUTCOMES, CACHE),
    _e("opip/analytics/opip_analytics.db", OUTCOMES, BUSINESS_RECORD, note="SQLite analytics store"),
    _e("opip/canonical", OUTCOMES, BUSINESS_RECORD, note="canonical SQLite writer (paper/quote evidence)"),
    # --- Configuration -----------------------------------------------------------------------
    _e("operator_control.json", CONFIG, STATE, note="operator pause/quiet-hours state"),
    _e("opip/strategy-admin", CONFIG, STATE),
    _e("opip/strategy-admin/active.json", CONFIG, STATE),
    _e("asset_identity_registry.json", CONFIG, STATE),
    _e("coinmarketcal_coin_map.json", CONFIG, CACHE),
    _e("opip/events/coinmarketcal_identity_map.json", CONFIG, CACHE),
    _e("opip/events/ingest_state.json", CONFIG, STATE),
    _e("opip/events/provider_health.json", CONFIG, STATE),
    _e("protection_admission_stamp.json", CONFIG, SAFETY_CRITICAL, note="F11 admission stamp: independent of PostgreSQL"),
    _e("protection_admission_stamp_history.json", CONFIG, SAFETY_CRITICAL),
    _e("opip/protection/protection_state.json", CONFIG, SAFETY_CRITICAL, note="F11 protection state"),
    _e("opip/risk", CONFIG, SAFETY_CRITICAL),
    # --- Operations and evidence -------------------------------------------------------------
    _e("p1_evidence_ledger.jsonl", OPS, BUSINESS_RECORD,
       "raw.ingested_event", PARTIAL, _SHIP, "NONE", "source", "raw JSON only"),
    _e("p1_shadow_outbox.jsonl", OPS, ARCHIVE, note="retired stream tombstone"),
    _e("p1_shadow_outbox_dead_letter.jsonl", OPS, ARCHIVE),
    _e("p1_shadow_outbox_checkpoint.json", OPS, ARCHIVE),
    _e("opportunity_evidence.jsonl", OPS, BUSINESS_RECORD),
    _e("opip/opportunity_accountability.jsonl", OPS, BUSINESS_RECORD,
       "learning.opportunity_accountability_latest_v", PARTIAL, _SHIP, _DASH, "source",
       "raw JSON plus read-model views"),
    _e("opip/opportunity_accountability_summary.json", OPS, CACHE),
    _e("system_incidents.json", OPS, STATE),
    _e("opip/learning/telemetry.json", OPS, STATE),
    _e(".unified_cycle.lock", OPS, LOCK),
    _e("irr", OPS, STATE),
    _e("learning", OPS, STATE),
    _e("opip/canonical/opip_canonical_v1.sqlite3", OUTCOMES, BUSINESS_RECORD, note="canonical SQLite writer"),
)


def cutover_readiness(entry: StorageEntry) -> str:
    """Derived, never stored: how far an entry is from a PostgreSQL-authoritative cutover."""
    if entry.kind == SAFETY_CRITICAL:
        return "EXCLUDED_BY_DESIGN"
    if entry.kind in {LOCK, CACHE, ARCHIVE}:
        return "N/A"
    if entry.kind == STATE:
        return "FOLLOWS_OWNING_WRITER"
    if entry.schema == YES and entry.write == _SHIP:
        return "SCHEMA_READY_WRITE_NOT_INTEGRATED"
    if entry.schema in {YES, PARTIAL} and entry.write == "API_ONLY":
        return "SCHEMA_READY_WRITE_NOT_INTEGRATED"
    return "NOT_READY"


def entries_for(domain: str) -> list[StorageEntry]:
    return [entry for entry in INVENTORY if entry.domain == domain]


def covering_entry(relative_path: str) -> StorageEntry | None:
    """The inventory entry that classifies ``relative_path`` (exact, or an ancestor directory)."""
    exact = {entry.path: entry for entry in INVENTORY}
    if relative_path in exact:
        return exact[relative_path]
    parts = relative_path.split("/")
    for size in range(len(parts) - 1, 0, -1):
        candidate = exact.get("/".join(parts[:size]))
        if candidate is not None and candidate.covers_children:
            return candidate
    return None


def domain_matrix() -> list[dict[str, str]]:
    """One row per domain in the owner's column order."""
    rows: list[dict[str, str]] = []
    for domain in DOMAINS:
        members = entries_for(domain)
        business = [entry for entry in members if entry.kind == BUSINESS_RECORD]
        targets = sorted({entry.pg_target for entry in business if entry.pg_target})
        schemas = {entry.schema for entry in business}
        if not business:
            schema = NOT_APPLICABLE
        elif schemas == {YES}:
            schema = YES
        elif schemas == {NO}:
            schema = NO
        else:
            schema = PARTIAL
        writes = sorted({entry.write for entry in business if entry.write != "NONE"}) or ["NONE"]
        reads = sorted({entry.read for entry in business if entry.read != "NONE"}) or ["NONE"]
        readiness = sorted({cutover_readiness(entry) for entry in business}) or ["N/A"]
        rows.append(
            {
                "domain": domain,
                "existing_storage": ", ".join(f"`{entry.path}`" for entry in business) or "state files only",
                "postgresql_target": ", ".join(f"`{target}`" for target in targets) or "none",
                "schema_complete": schema,
                "write_integration": ", ".join(writes),
                "read_integration": ", ".join(reads),
                "cutover_readiness": ", ".join(readiness),
            }
        )
    return rows


def render_markdown() -> str:
    lines = [
        "# O'Pip PostgreSQL Phase 1: storage inventory and schema-gap matrix",
        "",
        "Generated from `app/opip/data_platform/storage_inventory.py` (do not edit by hand; a test",
        "compares this file with the module). Scope: NEW production business records after an approved",
        "cutover. Phase 2 (historical migration) and Phase 3 (legacy retirement) are separate.",
        "",
        "Legend: `basis=source` follows from checked-in code; `basis=path` was assigned from the file",
        "name and still needs owner confirmation. Nothing here is `DIRECT` before an approved cutover.",
        "",
        "## Domain matrix",
        "",
        "| Domain | Existing storage | PostgreSQL target | Schema complete | Write integration | Read integration | Cutover readiness |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in domain_matrix():
        lines.append(
            "| {domain} | {existing_storage} | {postgresql_target} | {schema_complete} | "
            "{write_integration} | {read_integration} | {cutover_readiness} |".format(**row)
        )
    lines += ["", "## Per-path appendix", "", "| Path | Domain | Kind | Target | Schema | Write | Read | Basis | Note |", "|---|---|---|---|---|---|---|---|---|"]
    for entry in sorted(INVENTORY, key=lambda item: (DOMAINS.index(item.domain), item.path)):
        lines.append(
            f"| `{entry.path}` | {entry.domain} | {entry.kind} | {entry.pg_target or '-'} | {entry.schema} | "
            f"{entry.write} | {entry.read} | {entry.basis} | {entry.note or '-'} |"
        )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "DOMAINS",
    "INVENTORY",
    "StorageEntry",
    "covering_entry",
    "cutover_readiness",
    "domain_matrix",
    "entries_for",
    "render_markdown",
]
