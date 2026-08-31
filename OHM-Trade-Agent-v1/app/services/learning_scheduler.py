from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.services.explosion_learning import observe_due_explosion_outcomes
from app.services.freqtrade_result_ingest import ingest_freqtrade_dry_run
from app.services.intelligence_learning_profile import build_intelligence_learning_profile
from app.services.movement_discovery_outcomes import observe_due_movement_discovery_outcomes
from app.services.profitability_learning import build_profitability_profile
from app.services.price_movement_learning import observe_due_price_movements
from app.services.registry_io import load_json, registry_lock, save_json_atomic
from app.services.shadow_learning import observe_due_shadows


STATE_FILE = Path("/app/data/learning_scheduler_state.json")
LOCK_FILE = STATE_FILE.parent / ".learning_scheduler_state.lock"
PROFILE_INTERVAL = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def run_learning_cycle(*, now: datetime | None = None) -> dict[str, Any]:
    """Run free/local learning work with no paid AI calls.

    Every observer is telemetry-only and fail-open. Wave 5 explosion-state
    outcomes are labeled prospectively after their fixed horizon is due; they
    never feed live qualification, execution, risk or notification authority.
    """
    now = now or _now()
    shadow = observe_due_shadows(now=now)
    try:
        movement = observe_due_price_movements(now=now)
    except Exception as exc:
        movement = {
            "status": "UNAVAILABLE",
            "observations_added": 0,
            "reason": f"movement observation failed open: {type(exc).__name__}: {exc}",
        }
    try:
        discovery = observe_due_movement_discovery_outcomes(now=now)
    except Exception as exc:
        discovery = {
            "status": "UNAVAILABLE",
            "observations_added": 0,
            "reason": f"v2.1 movement outcome observation failed open: {type(exc).__name__}: {exc}",
        }
    try:
        explosion = observe_due_explosion_outcomes(now=now)
    except Exception as exc:
        explosion = {
            "status": "UNAVAILABLE",
            "outcomes_added": 0,
            "reason": f"Wave 5 explosion outcome observation failed open: {type(exc).__name__}: {exc}",
        }

    try:
        freqtrade = ingest_freqtrade_dry_run()
    except Exception as exc:
        freqtrade = {
            "status": "UNAVAILABLE",
            "outcomes_added": 0,
            "reason": f"Freqtrade dry-run ingest failed open: {type(exc).__name__}: {exc}",
        }
    try:
        intelligence_profile = build_intelligence_learning_profile(persist=True)
    except Exception as exc:
        intelligence_profile = {
            "population": "FREQTRADE_DRY_RUN_V1",
            "status": "UNAVAILABLE",
            "reason": f"intelligence journey profile failed open: {type(exc).__name__}: {exc}",
        }

    profile_refreshed = False
    profile_status = "NOT_DUE"
    with registry_lock(LOCK_FILE):
        state = load_json(STATE_FILE)
        last = _parse(state.get("last_profile_refresh_at"))
        if last is None or now - last >= PROFILE_INTERVAL:
            profile = build_profitability_profile(persist=True)
            state["last_profile_refresh_at"] = now.isoformat()
            state["last_profile_status"] = str((profile.get("trade_calibration") or {}).get("status") or "UNKNOWN")
            state["paid_ai_calls"] = 0
            save_json_atomic(STATE_FILE, state)
            profile_refreshed = True
            profile_status = state["last_profile_status"]

    return {
        "status": "OK",
        "paid_ai_calls": 0,
        "shadow": shadow,
        "price_movement": movement,
        "movement_discovery_v2_1": discovery,
        "wave5_explosion_learning": explosion,
        "freqtrade_dry_run": freqtrade,
        "intelligence_journey_profile": intelligence_profile,
        "profile_refreshed": profile_refreshed,
        "profile_status": profile_status,
    }
