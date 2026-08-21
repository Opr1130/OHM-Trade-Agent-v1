from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone

from app.scanner.market_scanner import analyze_symbol
from app.services.explosion_learning import append_explosion_state, latest_state_by_symbol
from app.services.explosion_precursor import evaluate_explosion_precursor
from app.services.explosion_precursor_learning import append_precursor_observation
from app.services.explosion_state import ExplosionStateVector, build_explosion_state_vector
from app.services.movement_discovery_v2_2_shadow import discover_v22_shadow_candidates
from app.services.native_flow_evidence import evaluate_native_flow_shadow


MAX_NATIVE_FLOW_CANDIDATES = 12


def _restore_previous(row: dict | None) -> ExplosionStateVector | None:
    if not row:
        return None
    allowed = {field.name for field in fields(ExplosionStateVector)}
    payload = {key: value for key, value in row.items() if key in allowed}
    payload["reasons"] = tuple(payload.get("reasons") or ())
    payload["warnings"] = tuple(payload.get("warnings") or ())
    try:
        return ExplosionStateVector(**payload)
    except (TypeError, ValueError):
        return None


def main() -> None:
    candidates = discover_v22_shadow_candidates()
    previous = latest_state_by_symbol()
    captured = 0
    analyzed = 0
    flow_available = 0
    precursor_watches = 0
    phases: dict[str, int] = {}

    print("===== OHM WAVE 5.1 EXPLOSION LEARNING — SHADOW =====")
    print("Candidates selected:", len(candidates))
    print("Trade authority changed:", False)
    print("Production alert thresholds changed:", False)
    print("Latent precursor challenger changes production decisions:", False)

    for index, candidate in enumerate(candidates):
        status, snapshot, _ = analyze_symbol(candidate.mover.primary_pair)
        if status != "ok" or snapshot is None:
            continue
        analyzed += 1

        native_metrics = None
        if index < MAX_NATIVE_FLOW_CANDIDATES:
            flow = evaluate_native_flow_shadow(candidate.mover.kraken_public_symbol)
            native_metrics = flow.metrics
            if native_metrics is not None:
                flow_available += 1

        prior = _restore_previous(previous.get(snapshot.symbol.upper()))
        vector = build_explosion_state_vector(
            snapshot,
            native_flow=native_metrics,
            previous=prior,
        )
        precursor = evaluate_explosion_precursor(vector, previous=prior)
        observed_at = datetime.now(timezone.utc)
        observation_id = append_explosion_state(
            vector,
            observed_at=observed_at,
            source_cohort=candidate.cohort,
        )
        append_precursor_observation(
            precursor,
            observation_id=observation_id,
            observed_at=observed_at.isoformat(),
            phase=vector.phase,
            phase_score=vector.phase_score,
            reference_price=vector.reference_price,
            source_cohort=candidate.cohort,
        )
        captured += 1
        phases[vector.phase] = phases.get(vector.phase, 0) + 1
        precursor_watches += int(precursor.watch)
        print(
            f"{vector.symbol}: Phase={vector.phase} Score={vector.phase_score} "
            f"Precursor={precursor.score} Watch={precursor.watch} "
            f"1h={vector.momentum_1h_pct:+.2f}% Accel={vector.momentum_acceleration:+.2f} "
            f"Vol={vector.relative_volume:.2f}x TradeAccel={vector.trade_count_acceleration} "
            f"Persistence={vector.persistence_score} Cohort={candidate.cohort}"
        )

    print("Analyzed:", analyzed)
    print("Snapshots captured:", captured)
    print("Native flow available:", flow_available)
    print("Latent precursor watches:", precursor_watches)
    print("Phase counts:", phases)
    print("Shadow only:", True)


if __name__ == "__main__":
    main()
