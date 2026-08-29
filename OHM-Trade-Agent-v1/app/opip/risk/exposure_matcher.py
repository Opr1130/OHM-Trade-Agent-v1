"""Read-only exposure resolution for the O'Pip Event Risk Shield.

Real advisory and paper families remain isolated. Active real positions used by
the live shield require the existing Kraken read-only position-verification
facade; stale local registry state is never treated as exchange truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import re
from typing import Any, Callable

from app.opip.events.contract import MappingStatus, parse_utc, require_utc
from app.opip.events.identity import (
    ASSET_IDENTITY_REGISTRY,
    normalize_symbol,
    resolve_registry_identity,
)
from app.opip.risk.contract import Direction, ExposureFamily, ExposureState, ExposureView


logger = logging.getLogger(__name__)
SUPPORTED_QUOTES = ("ZUSDT", "USDT", "ZUSD", "USD")
PAPER_PENDING_STATUSES = frozenset({"PENDING_ENTRY"})
PAPER_OPEN_STATUSES = frozenset({"OPEN"})
LEGACY_ASSET_ALIASES: dict[str, str] = {
    "XXBT": "BTC", "XBT": "BTC", "XETH": "ETH", "XXDG": "DOGE",
    "XDG": "DOGE", "XXRP": "XRP", "XXLM": "XLM", "XLTC": "LTC",
    "XZEC": "ZEC", "XXMR": "XMR", "XETC": "ETC", "XMLN": "MLN",
}


@dataclass(frozen=True)
class ExposureCollectionResult:
    exposures: tuple[ExposureView, ...]
    coverage_complete: bool
    source_status: dict[str, str]
    warnings: tuple[str, ...] = ()


VerifierFactory = Callable[[], Any]


def normalize_market(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(symbol or "")).upper()


def base_asset_of(symbol: str) -> str:
    market = normalize_market(symbol)
    base = market
    for quote in SUPPORTED_QUOTES:
        if market.endswith(quote) and len(market) > len(quote):
            base = market[: -len(quote)]
            break
    return normalize_symbol(LEGACY_ASSET_ALIASES.get(base, base))


def _direction(value: str | None) -> Direction:
    raw = str(value or "").strip().upper()
    if raw == "LONG":
        return Direction.LONG
    if raw == "SHORT":
        return Direction.SHORT
    raise ValueError(f"unsupported exposure direction {value!r}")


def _resolve_canonical_identity(
    base_asset: str, *, decision_at: datetime, identity_registry: Path
) -> tuple[str | None, str | None, str]:
    try:
        identity = resolve_registry_identity(
            source_symbol=base_asset,
            source_name=None,
            provider_asset_id=None,
            as_of=decision_at,
            path=identity_registry,
        )
    except (OSError, ValueError, KeyError, TypeError):
        logger.exception("O'Pip exposure identity resolution failed for %s", base_asset)
        return None, None, MappingStatus.UNKNOWN.value
    if identity.mapping_status != MappingStatus.UNIQUE:
        return None, None, identity.mapping_status.value
    return (
        identity.canonical_asset_id,
        identity.canonical_asset_name,
        identity.mapping_status.value,
    )


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_view(
    *,
    exposure_id: str,
    exposure_family: ExposureFamily,
    exposure_state: ExposureState,
    source_registry: str,
    symbol: str,
    direction: str | None,
    status: str,
    decision_at: datetime,
    identity_registry: Path,
    entry_price: float | None = None,
    stop_price: float | None = None,
    opened_at: str | None = None,
    venue: str | None = None,
    verification_status: str = "NOT_REQUIRED",
    verification_reason: str | None = None,
) -> ExposureView | None:
    market = normalize_market(symbol)
    base = base_asset_of(symbol)
    if not market or not base:
        return None
    parsed_direction = _direction(direction)
    canonical_id, canonical_name, identity_status = _resolve_canonical_identity(
        base, decision_at=decision_at, identity_registry=identity_registry
    )
    opened_at_utc = None
    if opened_at:
        try:
            opened_at_utc = parse_utc(opened_at, field_name="opened_at")
        except ValueError:
            opened_at_utc = None
    return ExposureView(
        exposure_id=str(exposure_id),
        exposure_family=exposure_family,
        exposure_state=exposure_state,
        source_registry=str(source_registry),
        symbol=market,
        base_asset=normalize_symbol(base) or base,
        direction=parsed_direction,
        status=str(status or ""),
        snapshot_at_utc=decision_at,
        canonical_asset_id=canonical_id,
        canonical_asset_name=canonical_name,
        identity_status=identity_status,
        venue=venue,
        entry_price=_safe_float(entry_price),
        stop_price=_safe_float(stop_price),
        opened_at_utc=opened_at_utc,
        verification_status=str(verification_status or "NOT_REQUIRED"),
        verification_reason=verification_reason,
    )


def _load_active_real(
    *,
    decision_at: datetime,
    identity_registry: Path,
    verify_positions: bool,
    verifier_factory: VerifierFactory | None,
) -> tuple[list[ExposureView], bool, list[str]]:
    from app.services.active_trade_registry import get_active_trades

    trades = list(get_active_trades())
    verifier = None
    if verify_positions and trades:
        if verifier_factory is None:
            from app.services.kraken_position_verification import KrakenPositionVerifier
            verifier_factory = KrakenPositionVerifier
        verifier = verifier_factory()
        verifier.refresh()

    views: list[ExposureView] = []
    complete = True
    warnings: list[str] = []
    for trade in trades:
        verification_status = "NOT_REQUIRED"
        verification_reason = None
        if verifier is not None:
            verification = verifier.verify(trade)
            verification_status = str(verification.status)
            verification_reason = str(verification.reason or "") or None
            if verification_status == "ABSENT":
                warnings.append(f"ACTIVE_REAL_ABSENT:{trade.trade_id or trade.symbol}")
                continue
            if not bool(getattr(verification, "verified", False)):
                complete = False
                warnings.append(
                    f"ACTIVE_REAL_VERIFICATION_{verification_status}:{trade.trade_id or trade.symbol}"
                )
                continue
        try:
            view = _build_view(
                exposure_id=str(trade.trade_id or f"ACTIVE:{trade.symbol}"),
                exposure_family=ExposureFamily.REAL_ADVISORY,
                exposure_state=ExposureState.ACTIVE,
                source_registry="active_trade_registry",
                symbol=trade.symbol,
                direction=trade.direction,
                status=trade.status,
                decision_at=decision_at,
                identity_registry=identity_registry,
                entry_price=trade.entry_price,
                stop_price=trade.stop_price,
                opened_at=trade.opened_at or None,
                venue="KRAKEN",
                verification_status=("VERIFIED" if verifier is not None else verification_status),
                verification_reason=verification_reason,
            )
        except (ValueError, TypeError) as exc:
            complete = False
            warnings.append(f"ACTIVE_REAL_INVALID:{trade.trade_id or trade.symbol}:{type(exc).__name__}")
            continue
        if view is not None:
            views.append(view)
    return views, complete, warnings


def active_real_exposures(
    *,
    decision_at: datetime,
    identity_registry: Path = ASSET_IDENTITY_REGISTRY,
    verify_positions: bool = False,
    verifier_factory: VerifierFactory | None = None,
) -> tuple[ExposureView, ...]:
    views, _, _ = _load_active_real(
        decision_at=require_utc(decision_at, field_name="decision_at"),
        identity_registry=identity_registry,
        verify_positions=verify_positions,
        verifier_factory=verifier_factory,
    )
    return tuple(views)


def pending_setup_exposures(
    *, decision_at: datetime, identity_registry: Path = ASSET_IDENTITY_REGISTRY
) -> tuple[ExposureView, ...]:
    from app.services.pending_setup_registry import get_pending_setups

    views: list[ExposureView] = []
    for setup in get_pending_setups():
        view = _build_view(
            exposure_id=str(setup.trade_id or f"PENDING:{setup.symbol}"),
            exposure_family=ExposureFamily.REAL_ADVISORY,
            exposure_state=ExposureState.PENDING,
            source_registry="pending_setup_registry",
            symbol=setup.symbol,
            direction=setup.direction,
            status=setup.status,
            decision_at=decision_at,
            identity_registry=identity_registry,
            entry_price=setup.entry_high,
            stop_price=setup.stop_price,
            opened_at=setup.created_at or None,
            venue="KRAKEN",
        )
        if view is not None:
            views.append(view)
    return tuple(views)


def paper_exposures(
    *,
    decision_at: datetime,
    identity_registry: Path = ASSET_IDENTITY_REGISTRY,
    state_file: Path | None = None,
) -> tuple[ExposureView, ...]:
    from app.services import paper_trade_registry

    kwargs = {"state_file": state_file} if state_file is not None else {}
    views: list[ExposureView] = []
    for lifecycle in paper_trade_registry.get_nonterminal_lifecycles(**kwargs):
        status = str(lifecycle.status or "").upper()
        if status not in PAPER_PENDING_STATUSES | PAPER_OPEN_STATUSES:
            continue
        view = _build_view(
            exposure_id=str(lifecycle.paper_trade_id),
            exposure_family=ExposureFamily.PAPER,
            exposure_state=(ExposureState.PENDING if status in PAPER_PENDING_STATUSES else ExposureState.ACTIVE),
            source_registry="paper_trade_registry",
            symbol=lifecycle.symbol,
            direction=lifecycle.direction,
            status=status,
            decision_at=decision_at,
            identity_registry=identity_registry,
            entry_price=lifecycle.entry_price,
            stop_price=lifecycle.stop_price,
            opened_at=lifecycle.opened_at or lifecycle.created_at or None,
            venue="KRAKEN",
        )
        if view is not None:
            views.append(view)
    return tuple(views)


def collect_exposures_with_status(
    *,
    decision_at: datetime,
    identity_registry: Path = ASSET_IDENTITY_REGISTRY,
    paper_state_file: Path | None = None,
    include_paper: bool = True,
    verify_active_real: bool = True,
    verifier_factory: VerifierFactory | None = None,
) -> ExposureCollectionResult:
    cutoff = require_utc(decision_at, field_name="decision_at")
    collected: list[ExposureView] = []
    warnings: list[str] = []
    statuses: dict[str, str] = {}
    complete = True

    try:
        rows, source_complete, source_warnings = _load_active_real(
            decision_at=cutoff,
            identity_registry=identity_registry,
            verify_positions=verify_active_real,
            verifier_factory=verifier_factory,
        )
        collected.extend(rows)
        complete = complete and source_complete
        statuses["active_real"] = "OK" if source_complete else "DEGRADED"
        warnings.extend(source_warnings)
    except Exception as exc:
        logger.exception("O'Pip Event Risk Shield could not read active real exposures")
        complete = False
        statuses["active_real"] = "UNAVAILABLE"
        warnings.append(f"EXPOSURE_SOURCE_ACTIVE_REAL_UNAVAILABLE:{type(exc).__name__}")

    for name, loader in (
        ("pending_setup", lambda: pending_setup_exposures(decision_at=cutoff, identity_registry=identity_registry)),
        ("paper", lambda: paper_exposures(decision_at=cutoff, identity_registry=identity_registry, state_file=paper_state_file)),
    ):
        if name == "paper" and not include_paper:
            statuses[name] = "DISABLED"
            continue
        try:
            collected.extend(loader())
            statuses[name] = "OK"
        except Exception as exc:
            logger.exception("O'Pip Event Risk Shield could not read %s exposures", name)
            complete = False
            statuses[name] = "UNAVAILABLE"
            warnings.append(f"EXPOSURE_SOURCE_{name.upper()}_UNAVAILABLE:{type(exc).__name__}")

    return ExposureCollectionResult(
        exposures=tuple(collected),
        coverage_complete=complete,
        source_status=statuses,
        warnings=tuple(warnings),
    )


def collect_exposures(
    *,
    decision_at: datetime,
    identity_registry: Path = ASSET_IDENTITY_REGISTRY,
    paper_state_file: Path | None = None,
    include_paper: bool = True,
    verify_active_real: bool = False,
    verifier_factory: VerifierFactory | None = None,
) -> tuple[ExposureView, ...]:
    """Compatibility helper; live shield uses collect_exposures_with_status."""
    return collect_exposures_with_status(
        decision_at=decision_at,
        identity_registry=identity_registry,
        paper_state_file=paper_state_file,
        include_paper=include_paper,
        verify_active_real=verify_active_real,
        verifier_factory=verifier_factory,
    ).exposures
