from __future__ import annotations

from dataclasses import asdict, fields
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from app.opip.canonical.gap_spool import append_capture_gap
from app.opip.contracts.paper_outcome import resolve_quote_currency
from app.services.paper_outcome_outbox import (
    DELIVERY_COMMITTED,
    DELIVERY_PERMANENT_FAILURE,
    DELIVERY_PENDING,
    build_outcome_envelope,
    canonical_capture_enabled,
    settle_envelope,
    submit_envelope,
)
from app.services.paper_trade_models import (
    NONTERMINAL_STATUSES,
    TERMINAL_STATUSES,
    PaperAccountSummary,
    PaperTradeLifecycle,
)
from app.services.registry_io import load_json, registry_lock, save_json_atomic


STATE_FILE = Path("/app/data/paper_trading/state.json")
EVENT_FILE = Path("/app/data/paper_trading/events.jsonl")

#: Paper-plane evidence-gap spool. Reuses the canonical gap-spool mechanism
#: (identical implementation, non-eviction, fail-closed on corruption) in its
#: own file so a paper evidence failure can never mark the alert governor's
#: evidence window incomplete.
EVIDENCE_GAP_SPOOL_FILE = Path("/app/data/paper_trading/evidence_gap_spool.json")

#: Durable disposition recorded when a paper evidence append cannot be written.
PAPER_EVENT_APPEND_FAILED = "PAPER_EVENT_APPEND_FAILED"

logger = logging.getLogger(__name__)


def _state_lock(path: Path) -> Path:
    return path.parent / f".{path.name}.lock"


def _event_lock(path: Path) -> Path:
    return path.parent / f".{path.name}.lock"


def _normalize_item(item: dict[str, Any]) -> PaperTradeLifecycle:
    allowed = {field.name for field in fields(PaperTradeLifecycle)}
    normalized = {key: value for key, value in item.items() if key in allowed}
    defaults = {
        "candle_interval_minutes": 15,
        "reference_ask": None,
        "entry_price": None,
        "entry_fee": 0.0,
        "quantity_initial": 0.0,
        "quantity_remaining": 0.0,
        "opened_at": None,
        "tp1_hit": False,
        "tp1_at": None,
        "tp1_price": None,
        "tp1_quantity": 0.0,
        "realized_gross_pnl": 0.0,
        "fees_paid": 0.0,
        "closed_at": None,
        "exit_price": None,
        "exit_reason": None,
        "gross_pnl": None,
        "net_pnl": None,
        "net_pnl_pct": None,
        "outcome": None,
        "last_processed_candle_ts": None,
        "last_observed_price": None,
        "revision": 1,
        "paper_only": True,
        "exchange_write_authority": False,
    }
    for key, value in defaults.items():
        normalized.setdefault(key, value)
    return PaperTradeLifecycle(**normalized)


def _load_rows(path: Path) -> dict[str, Any]:
    payload = load_json(path)
    rows = payload.get("lifecycles", payload)
    if not isinstance(rows, dict):
        raise ValueError("paper state must contain lifecycle objects")
    return rows


def _save_rows(rows: dict[str, Any], path: Path) -> None:
    save_json_atomic(
        path,
        {
            "schema_version": 1,
            "paper_only": True,
            "lifecycles": rows,
        },
    )


def _event_id(trade: PaperTradeLifecycle, event_type: str) -> str:
    raw = f"{trade.paper_trade_id}|{trade.revision}|{event_type}"
    return "PTE:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _record_evidence_disposition(
    trade: PaperTradeLifecycle,
    event_type: str,
    *,
    error_code: str,
    gap_spool_file: Path = EVIDENCE_GAP_SPOOL_FILE,
) -> None:
    """Make a lost paper evidence append durable, visible, and reconcilable.

    A lifecycle row can read CLOSED while its terminal event - the shipper's
    source and the only durable record of that transition - was never written.
    Silently returning would leave operational state complete and evidence
    missing with nothing to repair from, which the repository's learning-
    consumption invariant treats as a defect.

    This never raises: evidence bookkeeping must not affect paper trading or
    the production scan cycle.
    """
    try:
        append_capture_gap(
            idempotency_key=_event_id(trade, event_type),
            scan_id=str(trade.episode_id or ""),
            identity=str(trade.paper_trade_id),
            intended_event_type=str(event_type).upper(),
            error_code=error_code,
            path=gap_spool_file,
        )
    except Exception as exc:
        # The spool itself is unavailable or corrupt. Fail closed rather than
        # silent: a corrupt spool also makes any "evidence window complete"
        # claim unprovable, so this must be observable.
        logger.error(
            "paper evidence disposition failed; evidence window uncertified "
            "(%s event for %s): %s",
            event_type,
            trade.paper_trade_id,
            exc,
        )


def _append_event(
    trade: PaperTradeLifecycle,
    event_type: str,
    *,
    event_file: Path,
    details: dict[str, Any] | None = None,
    gap_spool_file: Path = EVIDENCE_GAP_SPOOL_FILE,
) -> bool:
    """Append one durable paper event. Returns False when evidence was lost.

    Returning rather than raising preserves the documented invariant that paper
    persistence problems never affect live ranking or trading authority, while
    the recorded disposition keeps the loss from being silent.
    """
    event = {
        "event_id": _event_id(trade, event_type),
        "event_type": str(event_type).upper(),
        "paper_trade_id": trade.paper_trade_id,
        "episode_id": trade.episode_id,
        "cohort_id": trade.cohort_id,
        "symbol": trade.symbol,
        "status": trade.status,
        "revision": trade.revision,
        "updated_at": trade.updated_at,
        "paper_only": True,
        "population": "PAPER_TRADE_V1",
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "exit_reason": trade.exit_reason,
        "tp1_hit": trade.tp1_hit,
        "gross_pnl": trade.gross_pnl,
        "net_pnl": trade.net_pnl,
        "net_pnl_pct": trade.net_pnl_pct,
        "outcome": trade.outcome,
        "details": details or {},
    }
    event_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        with registry_lock(_event_lock(event_file)):
            with event_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
    except (OSError, TimeoutError):
        _record_evidence_disposition(
            trade,
            event_type,
            error_code=PAPER_EVENT_APPEND_FAILED,
            gap_spool_file=gap_spool_file,
        )
        return False
    return True


def create_lifecycle(
    trade: PaperTradeLifecycle,
    *,
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
) -> PaperTradeLifecycle:
    trade.symbol = trade.symbol.upper()
    trade.direction = trade.direction.upper()
    if trade.direction != "LONG":
        raise ValueError("Paper Trade v1 supports LONG lifecycles only")
    if trade.status not in NONTERMINAL_STATUSES:
        raise ValueError("paper lifecycle must start pending or open")
    if not trade.paper_only or trade.exchange_write_authority:
        raise ValueError("paper lifecycle authority invariant violated")

    with registry_lock(_state_lock(state_file)):
        rows = _load_rows(state_file)
        existing = rows.get(trade.paper_trade_id)
        if isinstance(existing, dict):
            return _normalize_item(existing)
        for row in rows.values():
            if not isinstance(row, dict):
                continue
            if (
                str(row.get("symbol") or "").upper() == trade.symbol
                and str(row.get("status") or "").upper() in NONTERMINAL_STATUSES
            ):
                raise ValueError(f"paper lifecycle already active for {trade.symbol}")
        rows[trade.paper_trade_id] = asdict(trade)
        _save_rows(rows, state_file)

    _append_event(trade, "CREATED", event_file=event_file)
    return trade


def save_lifecycle(
    trade: PaperTradeLifecycle,
    *,
    event_type: str,
    state_file: Path = STATE_FILE,
    event_file: Path = EVENT_FILE,
    details: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> PaperTradeLifecycle:
    if not trade.paper_only or trade.exchange_write_authority:
        raise ValueError("paper lifecycle authority invariant violated")
    if trade.status not in NONTERMINAL_STATUSES | TERMINAL_STATUSES:
        raise ValueError(f"unsupported paper status: {trade.status}")

    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    terminal = trade.status in TERMINAL_STATUSES
    envelope: dict[str, Any] | None = None
    with registry_lock(_state_lock(state_file)):
        rows = _load_rows(state_file)
        current = rows.get(trade.paper_trade_id)
        if not isinstance(current, dict):
            raise KeyError(f"paper lifecycle not found: {trade.paper_trade_id}")
        trade.revision = int(current.get("revision") or 1) + 1
        trade.updated_at = stamp.isoformat()

        # Build the exact recovery intent BEFORE persisting, so the terminal
        # lifecycle row and the intent that describes it commit together. The
        # revision is already final here, which is what makes the persisted
        # intent byte-identical on every later retry.
        if terminal:
            envelope = build_outcome_envelope(
                trade,
                terminal_event_type=event_type,
                terminal_event_id=_event_id(trade, event_type),
            )
            if envelope is not None:
                trade.outcome_outbox = envelope

        rows[trade.paper_trade_id] = asdict(trade)
        _save_rows(rows, state_file)

    # The append is self-recording: `_append_event` durably records its own
    # evidence gap on failure, so a lost terminal event is already visible.
    _append_event(trade, event_type, event_file=event_file, details=details)

    # Delivery happens only after the operational transition is durable, and
    # outside the state lock so a slow or unavailable writer can never block
    # paper monitoring.
    if envelope is not None:
        _deliver_outcome(trade, envelope, state_file=state_file)
    return trade


def _deliver_outcome(
    trade: PaperTradeLifecycle,
    envelope: dict[str, Any],
    *,
    state_file: Path,
) -> dict[str, Any]:
    """Submit the persisted intent and record the resulting disposition."""
    if not canonical_capture_enabled():
        # Canonical outcome capture is off. The envelope stays PENDING with no
        # gap recorded, so nothing claims an evidence window is complete and no
        # unactionable failure noise is produced.
        return envelope

    updated = submit_envelope(dict(envelope))
    settle_envelope(
        updated,
        paper_trade_id=trade.paper_trade_id,
        episode_id=str(trade.episode_id or ""),
        spool_file=EVIDENCE_GAP_SPOOL_FILE,
    )
    _persist_outbox(trade.paper_trade_id, updated, state_file=state_file)
    return updated


def _persist_outbox(
    paper_trade_id: str, envelope: dict[str, Any], *, state_file: Path
) -> None:
    """Persist a delivery disposition without touching economic fields."""
    try:
        with registry_lock(_state_lock(state_file)):
            rows = _load_rows(state_file)
            row = rows.get(paper_trade_id)
            if not isinstance(row, dict):
                return
            row["outcome_outbox"] = envelope
            _save_rows(rows, state_file)
    except Exception as exc:
        logger.error(
            "could not persist paper outcome disposition for %s: %s", paper_trade_id, exc
        )


def reconcile_pending_outcomes(*, state_file: Path = STATE_FILE) -> dict[str, int]:
    """Resubmit persisted outcome intents that are still undelivered.

    Idempotency is what makes reconciliation safe: a resubmission of an intent
    the writer already committed returns ``DUPLICATE_OK`` and resolves the
    matching gap, so retrying can never create a second economic outcome.

    ``PERMANENT_FAILURE`` envelopes are deliberately skipped - a conflicting or
    malformed outcome cannot be fixed by trying again, so it stays visibly
    incomplete instead of looping.
    """
    if not canonical_capture_enabled():
        return {"pending": 0, "committed": 0, "permanent_failure": 0, "skipped": 1}

    with registry_lock(_state_lock(state_file)):
        rows = _load_rows(state_file)

    targets: list[tuple[str, str, dict[str, Any]]] = []
    permanent = 0
    for paper_trade_id, row in rows.items():
        if not isinstance(row, dict):
            continue
        envelope = row.get("outcome_outbox")
        if not isinstance(envelope, dict):
            continue
        delivery = str(envelope.get("delivery"))
        if delivery == DELIVERY_PERMANENT_FAILURE:
            permanent += 1
            continue
        if delivery == DELIVERY_PENDING and isinstance(envelope.get("intent"), dict):
            targets.append(
                (str(paper_trade_id), str(row.get("episode_id") or ""), envelope)
            )

    committed = 0
    still_pending = 0
    for paper_trade_id, episode_id, envelope in targets:
        updated = submit_envelope(dict(envelope))
        settle_envelope(
            updated,
            paper_trade_id=paper_trade_id,
            episode_id=episode_id,
            spool_file=EVIDENCE_GAP_SPOOL_FILE,
        )
        _persist_outbox(paper_trade_id, updated, state_file=state_file)
        if str(updated.get("delivery")) == DELIVERY_COMMITTED:
            committed += 1
        else:
            still_pending += 1

    return {
        "pending": still_pending,
        "committed": committed,
        "permanent_failure": permanent,
        "skipped": 0,
    }


def get_lifecycles(*, state_file: Path = STATE_FILE) -> list[PaperTradeLifecycle]:
    with registry_lock(_state_lock(state_file)):
        rows = _load_rows(state_file)
    return [
        _normalize_item(row)
        for row in rows.values()
        if isinstance(row, dict)
    ]


def get_nonterminal_lifecycles(
    *,
    state_file: Path = STATE_FILE,
) -> list[PaperTradeLifecycle]:
    return [
        trade
        for trade in get_lifecycles(state_file=state_file)
        if trade.status in NONTERMINAL_STATUSES
    ]


def has_nonterminal_symbol(
    symbol: str,
    *,
    state_file: Path = STATE_FILE,
) -> bool:
    wanted = str(symbol or "").upper()
    return any(
        trade.symbol == wanted
        for trade in get_nonterminal_lifecycles(state_file=state_file)
    )


def account_summary(
    starting_equity: float,
    *,
    state_file: Path = STATE_FILE,
) -> PaperAccountSummary:
    if float(starting_equity) <= 0:
        raise ValueError("starting_equity must be positive")
    rows = get_lifecycles(state_file=state_file)
    realized = sum(
        float(trade.net_pnl or 0.0)
        for trade in rows
        if trade.status == "CLOSED"
    )
    realized_by_currency: dict[str, float] = {}
    for trade in rows:
        if trade.status != "CLOSED":
            continue
        currency = resolve_quote_currency(
            trade.symbol, quote_currency=trade.quote_currency
        )
        if currency is None:
            # Unprovable currency is never folded into a currency total.
            currency = "UNKNOWN"
        realized_by_currency[currency] = round(
            realized_by_currency.get(currency, 0.0) + float(trade.net_pnl or 0.0), 8
        )
    reserved = sum(
        float(trade.capital)
        + (
            float(trade.fees_paid)
            if trade.status in {"OPEN", "UNRESOLVED"}
            else float(trade.capital) * float(trade.fee_rate)
        )
        for trade in rows
        if trade.status in NONTERMINAL_STATUSES or trade.status == "UNRESOLVED"
    )
    closed_equity = float(starting_equity) + realized
    return PaperAccountSummary(
        starting_equity=round(float(starting_equity), 2),
        realized_net_pnl=round(realized, 8),
        closed_equity=round(closed_equity, 8),
        reserved_capital=round(reserved, 8),
        available_capital=round(max(0.0, closed_equity - reserved), 8),
        pending_entries=sum(trade.status == "PENDING_ENTRY" for trade in rows),
        open_positions=sum(trade.status == "OPEN" for trade in rows),
        closed_trades=sum(trade.status == "CLOSED" for trade in rows),
        cancelled_setups=sum(trade.status == "CANCELLED" for trade in rows),
        unresolved_trades=sum(trade.status == "UNRESOLVED" for trade in rows),
        realized_net_pnl_by_currency=realized_by_currency,
    )
