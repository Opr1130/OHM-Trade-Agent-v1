"""B/C-4A structural contract test for the cockpit semantic and data contract.

The B/C-4A deliverable is a *frozen* inventory: which canonical fields exist, which
are derivable, which are missing. A document that merely asserts those facts can
silently drift from the code it describes, so this test pins the inventory against
the frozen contract modules themselves.

It exists to make two classes of mistake fail loudly:

1. The frozen Paper-v2 event contracts change and the cockpit contract document
   still claims the old field set.
2. Someone adds a mark/valuation concept the document declares MISSING, without
   updating the document that tells the UI to render drawdown as UNKNOWN.

It reads the canonical modules directly; it never writes and has no trading
authority.
"""

from __future__ import annotations

import pathlib

import pytest

from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    PAPER_ECONOMIC_MODEL_VERSION,
    PAPER_EXECUTION_MODEL_VERSION,
    PAPER_PROTECTION_MODEL_VERSION,
    QualifiedOpportunityDisposition,
    TerminalReconciliationState,
)
from app.opip.contracts.paper_execution_events import (
    PAPER_EXECUTION_EVENT_CONTRACTS,
    PAPER_EXECUTION_EVENT_TYPES,
    PAPER_FILL_RECORDED,
    PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_PROTECTION_PLAN_RECORDED,
    PAPER_PROTECTION_STATE_RECORDED,
    PAPER_PROTECTION_TRIGGER_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
)
from app.opip.contracts.paper_metrics import (
    PAPER_METRIC_REGISTRY_VERSION,
    PAPER_METRICS,
)
from app.opip.contracts.paper_outcome import QUOTE_CURRENCIES

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CONTRACT_DOC = (
    _REPO_ROOT / "docs" / "architecture" / "bc4" / "BC4_COCKPIT_DATA_CONTRACT.md"
)

#: Validated into every paper evidence payload by the frozen contract.
_COMMON_FIELDS = frozenset({"schema_version", "engine"})

#: The exact documented inventory. Each event maps to the required fields the
#: contract document claims for it, *excluding* the common pair above.
_DOCUMENTED_REQUIRED_FIELDS: dict[str, frozenset[str]] = {
    PAPER_OPPORTUNITY_DISPOSITION_RECORDED: frozenset(
        {
            "disposition_id",
            "decision_context_id",
            "disposition_seq",
            "disposition",
            "evaluation_population",
            "quote_currency",
            "requested_capital",
            "disposition_time",
            "reason_code",
        }
    ),
    PAPER_ORDER_INTENT_RECORDED: frozenset(
        {
            "order_intent_id",
            "paper_trade_id",
            "decision_context_id",
            "intent_seq",
            "intent_role",
            "side",
            "order_type",
            "requested_quantity",
            "requested_notional",
            "reason_code",
            "intent_time",
            "execution_model_version",
            "reservation_id",
        }
    ),
    PAPER_EXECUTION_ATTEMPT_RECORDED: frozenset(
        {
            "execution_attempt_id",
            "order_intent_id",
            "paper_trade_id",
            "attempt_seq",
            "execution_state",
            "attempt_time",
            "execution_model_version",
        }
    ),
    PAPER_FILL_RECORDED: frozenset(
        {
            "fill_id",
            "execution_attempt_id",
            "order_intent_id",
            "paper_trade_id",
            "fill_seq",
            "side",
            "quantity",
            "price",
            "fee_cost",
            "spread_cost",
            "slippage_cost",
            "other_supported_cost",
            "fill_time",
            "execution_model_version",
            "economic_model_version",
        }
    ),
    PAPER_PROTECTION_PLAN_RECORDED: frozenset(
        {
            "protection_plan_id",
            "paper_trade_id",
            "plan_seq",
            "stop_price",
            "targets",
            "max_hold_seconds",
            "plan_time",
            "protection_model_version",
        }
    ),
    PAPER_PROTECTION_STATE_RECORDED: frozenset(
        {
            "protection_event_id",
            "protection_plan_id",
            "paper_trade_id",
            "state_seq",
            "from_state",
            "to_state",
            "reason_code",
            "state_time",
            "protection_model_version",
        }
    ),
    PAPER_PROTECTION_TRIGGER_RECORDED: frozenset(
        {
            "protection_trigger_id",
            "protection_plan_id",
            "paper_trade_id",
            "trigger_seq",
            "trigger_type",
            "reference_price",
            "trigger_time",
            "protection_model_version",
        }
    ),
    PAPER_RECONCILIATION_RECORDED: frozenset(
        {
            "reconciliation_id",
            "paper_trade_id",
            "reconciliation_seq",
            "position_state",
            "terminal_reconciliation_state",
            "filled_entry_quantity",
            "filled_exit_quantity",
            "remaining_quantity",
            "reserved_capital",
            "realized_gross_pnl",
            "recorded_execution_costs",
            "realized_net_pnl",
            "reconciled_time",
            "economic_model_version",
        }
    ),
}

#: Metric ids the contract document names as the authoritative semantics. The
#: cockpit must consume these rather than reimplementing them.
_DOCUMENTED_METRIC_IDS = frozenset(
    {
        "paper.realized_net_pnl",
        "paper.net_expectancy",
        "paper.execution_cost",
        "paper.fill_ratio",
        "paper.entry_latency",
        "paper.exit_latency",
        "paper.entry_price_drift_bps",
        "paper.exit_price_drift_bps",
        "paper.in_position_mfe_pct",
        "paper.in_position_mae_pct",
        "paper.capture_efficiency",
        "paper.realized_equity_drawdown_pct",
        "paper.unresolved_count",
    }
)


def test_contract_document_exists_and_is_version_controlled_source():
    """The B/C-4A deliverable must exist as a real artifact, not a claim."""
    assert _CONTRACT_DOC.is_file(), f"missing B/C-4A contract document: {_CONTRACT_DOC}"
    text = _CONTRACT_DOC.read_text(encoding="utf-8")
    assert "BC4_COCKPIT_DATA_CONTRACT" in text or "B/C-4 Cockpit" in text
    # The four classifications the program requires must all be present.
    for label in ("AVAILABLE", "DERIVABLE", "MISSING", "DEFER"):
        assert label in text, f"contract document omits classification {label}"


def test_documented_event_inventory_matches_the_frozen_contract():
    """Every event and field the document claims must exist exactly as claimed."""
    assert PAPER_EXECUTION_EVENT_TYPES == frozenset(_DOCUMENTED_REQUIRED_FIELDS)

    for event_type, documented in _DOCUMENTED_REQUIRED_FIELDS.items():
        contract = PAPER_EXECUTION_EVENT_CONTRACTS[event_type]
        assert contract.required_fields == _COMMON_FIELDS | documented, (
            f"{event_type}: frozen required fields drifted from the frozen "
            "B/C-4A inventory"
        )


def test_documented_metric_registry_ids_all_exist():
    """The document must not name a metric the versioned registry does not define."""
    assert PAPER_METRIC_REGISTRY_VERSION == "paper-metrics-v1"
    missing = sorted(_DOCUMENTED_METRIC_IDS - set(PAPER_METRICS))
    assert not missing, f"documented metrics absent from the registry: {missing}"

    # The registry is the only semantic authority: every entry must be self-describing.
    for metric_id, definition in PAPER_METRICS.items():
        assert definition.metric_id == metric_id
        assert definition.authoritative_source
        assert definition.eligible_population
        assert definition.formula


def test_usd_and_usdt_remain_distinct_portfolios():
    """Currency separation is a frozen invariant the cockpit must never sum across."""
    assert QUOTE_CURRENCIES == frozenset({"USD", "USDT"})


def test_strategy_axis_is_policy_version_not_an_invented_strategy_name():
    """No canonical `strategy_name` exists; the axis is policy version+fingerprint.

    The document records strategy identity as DERIVABLE from the decision context.
    If a canonical strategy name were ever added, the cockpit contract would need
    updating, so this pins the current truthful shape.
    """
    from app.opip.canonical.decision_context_bridge import DecisionContextFacts

    fields = set(DecisionContextFacts.__dataclass_fields__)
    assert "policy_version" in fields
    assert "policy_fingerprint" in fields
    assert "strategy_name" not in fields
    assert "strategy_version" not in fields


def test_frozen_model_versions_still_identify_their_semantics():
    """The document binds cost/economics to a version; that binding must hold."""
    assert PAPER_EXECUTION_MODEL_VERSION == "opip-paper-exec-l1-v2"
    assert PAPER_ECONOMIC_MODEL_VERSION == "opip-paper-economics-v2"
    assert PAPER_PROTECTION_MODEL_VERSION == "opip-paper-protection-v2"
    assert ENGINE_OPIP_PAPER_V2 == "OPIP_PAPER_V2"


def test_terminal_states_distinguish_unresolved_from_resolved():
    """Reconciliation vocabulary the cockpit renders must stay non-aliasing."""
    values = {state.value for state in TerminalReconciliationState}
    assert "FINAL_VERIFIED" in values
    assert "FLAT_AWAITING_RECONCILIATION" in values
    # An unresolved lifecycle must never be rendered as a verified one.
    assert "UNRESOLVED_EVIDENCE" in values
    assert "FINAL_VERIFIED" != "UNRESOLVED_EVIDENCE"


def test_disposition_vocabulary_keeps_rejection_reasons_distinct():
    """Opportunity accountability counts must not collapse distinct reasons."""
    values = {member.value for member in QualifiedOpportunityDisposition}
    assert "ADMITTED" in values
    for reason in ("CAPACITY_REJECTED", "CAPITAL_REJECTED", "NO_FILL_EXPIRED"):
        assert reason in values, f"disposition vocabulary lost {reason}"
    # UNRESOLVED is reserved for genuinely uncategorizable outcomes.
    assert "UNRESOLVED" in values


def test_no_marked_equity_valuation_exists_for_paper_v2():
    """Guards the document's central MISSING claim.

    The contract declares marked-equity drawdown MISSING because no canonical
    evidence binds a current mark price to an OPEN Paper-v2 position, and requires
    the UI to render it as UNKNOWN rather than 0. If a mark/valuation projection is
    ever introduced, this test fails so the contract (and the UI trust rendering)
    is updated deliberately instead of silently.
    """
    canonical_root = _REPO_ROOT / "app" / "opip" / "canonical"
    forbidden = ("mark_price", "marked_equity", "mark_to_market", "current_valuation")

    offenders: list[str] = []
    for path in sorted(canonical_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                offenders.append(f"{path.name}:{token}")

    assert not offenders, (
        "a marked-valuation concept now exists in the canonical package, so the "
        f"B/C-4A contract's MISSING claim is stale: {offenders}"
    )


def test_contract_document_records_marked_drawdown_as_missing_not_zero():
    """The document must state the UNKNOWN/INCOMPLETE rule explicitly."""
    text = _CONTRACT_DOC.read_text(encoding="utf-8")
    assert "UNKNOWN / INCOMPLETE" in text
    # And it must name the realized series as the authoritative risk series.
    assert "realized-equity drawdown" in text or "realized_equity_drawdown_pct" in text


def test_contract_document_forbids_invented_statistical_thresholds():
    """The cockpit must not fabricate the thresholds G-contract forbids.

    The document deliberately *names* the forbidden thresholds in order to forbid
    them, so a naive substring ban would flag the prohibition itself. What must be
    true is (a) the abstention vocabulary is present, (b) thresholds are explicitly
    declared not to be architecture constants, and (c) the registered statistical
    protocol that says so is a real, present authority.
    """
    text = _CONTRACT_DOC.read_text(encoding="utf-8")

    # (a) thin cohorts abstain rather than pass a fabricated threshold.
    assert "INSUFFICIENT_EVIDENCE" in text

    # (b) the prohibition is stated, not merely implied.
    assert "not** an architecture constant" in text or (
        "not an architecture constant" in text
    )
    assert "Do not promote" in text or "**Do not** promote" in text

    # (c) the authority for that rule is a real file in this repository.
    protocol = (
        _REPO_ROOT
        / "docs"
        / "architecture"
        / "v1.2"
        / "G_STATISTICAL_PROTOCOL.md"
    )
    assert protocol.is_file(), "the registered statistical protocol is missing"
    protocol_text = protocol.read_text(encoding="utf-8")
    assert "experiment-derived and owner-approved per registered family" in protocol_text
    assert "Do **not** promote reviewer-supplied N_eff" in protocol_text

    # (d) no *asserted* universal threshold. An asserted threshold would appear as a
    #     rule rather than as a quoted prohibition, so pin the rule-shaped forms.
    for asserted in (
        "require N >=",
        "require N >",
        "must be at least 30 trades",
        "minimum of 30 trades",
        "profit factor must exceed",
    ):
        assert asserted not in text, f"contract document asserts threshold {asserted}"


def test_contract_document_asserts_no_new_infrastructure():
    """The slice must reuse the existing plane; infra creep is a contract breach."""
    text = _CONTRACT_DOC.read_text(encoding="utf-8")
    assert "No new infrastructure" in text
    assert "PostgreSQL 17" in text


@pytest.mark.parametrize(
    "surface",
    [
        "app/opip/data_platform/read_model.py",
        "app/opip/data_platform/freshness.py",
    ],
)
def test_documented_authoritative_sources_exist(surface: str):
    """Every path the source map cites must actually exist."""
    assert (_REPO_ROOT / surface).is_file(), f"source map cites missing {surface}"


def test_documented_grafana_and_analytics_sources_exist():
    """The presentation and ingest authorities cited by the contract must exist."""
    for relative in (
        "deploy/analytics/README.md",
        "deploy/grafana/README.md",
        "deploy/grafana/dashboards/opip-intelligence-cockpit-v1.json",
        "app/opip/contracts/paper_metrics.py",
        "app/opip/contracts/paper_economics.py",
    ):
        assert (_REPO_ROOT / relative).is_file(), f"contract cites missing {relative}"
