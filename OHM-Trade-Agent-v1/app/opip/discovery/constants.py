"""Versioned constants for Discovery V2-01 evaluation labels.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

These numbers define a retrospective evaluation label. They do not gate
admission, alerts, paper enrollment, or live trading.
"""

from __future__ import annotations

from datetime import timedelta

# Admission evidence captured beside ScreeningEvaluation.metadata.
DISCOVERY_ADMISSION_SCHEMA_VERSION = 1
DISCOVERY_FEATURE_SCHEMA_VERSION = "OPIP-DISCOVERY-FEATURES-V1"
PRODUCTION_SELECTOR_VERSION = "PRODUCTION_COARSE_V2_1"
DISCOVERY_VENUE = "KRAKEN"

# Offline forward-outcome payload (structurally separate from decision metadata).
DISCOVERY_FORWARD_OUTCOME_SCHEMA_VERSION = 1
DISCOVERY_ATTRIBUTION_SCHEMA_VERSION = 1
DISCOVERY_ATTRIBUTION_TAXONOMY_VERSION = "DISCOVERY_ATTRIBUTION_V1"
DISCOVERY_EARLINESS_SCHEMA_VERSION = 1

DISCOVERY_OUTCOME_DEFINITION = "DISCOVERY_OUTCOME_V1"
DISCOVERY_MARKET_OPPORTUNITY_DEFINITION = "DISCOVERY_MARKET_OPPORTUNITY_V1"
DISCOVERY_OUTCOME_LABEL_SCHEMA_VERSION = 1

# Internal callback status. Never a terminal Stage-0 admission classification.
PENDING_FINALIZATION = "PENDING_FINALIZATION"

# Explanatory production-selector exclusion reasons. Not ranking authority.
EXCLUSION_GLOBAL_CAP = "GLOBAL_CAP"
EXCLUSION_PER_DIRECTION_CAP = "PER_DIRECTION_CAP"
EXCLUSION_UNDERLYING_DEDUP = "UNDERLYING_DEDUP"

# Catch-up batch: Broad Search ~200 rows / 5 min; outcomes worker every 10 min
# arrives ~400 rows/cycle. 1000 exceeds one skipped cycle (800) with headroom.
DISCOVERY_BOUNDED_MAX_ROWS = 1000
DISCOVERY_BOUNDED_RETRY_DELAY = timedelta(hours=1)
DISCOVERY_BOUNDED_CHECKPOINT_ANCHOR_BYTES = 4096
DISCOVERY_MATURATION_MILESTONES = (
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(minutes=30),
    timedelta(hours=1),
    timedelta(hours=4),
    timedelta(hours=8),
    timedelta(hours=12),
    timedelta(hours=13),
)

# Initial horizons for V2-01. Labels are explicit durations, not scan counts.
DISCOVERY_HORIZONS = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "12h": timedelta(hours=12),
}
DISCOVERY_PRIMARY_HORIZON = "12h"
# Offline ingestion window after the latest decision time. Must exceed the
# primary horizon so MFE/MAE can be computed; it is not a production delay.
DISCOVERY_FORWARD_READ_GRACE = DISCOVERY_HORIZONS[DISCOVERY_PRIMARY_HORIZON] + timedelta(
    hours=1
)

# Volatility-normalized winner barriers for DISCOVERY_OUTCOME_V1.
# Favorable barrier = max(min percent, ATR% * multiple).
# Adverse barrier = -max(min percent, ATR% * multiple).
DISCOVERY_V1_ATR_FAVORABLE_MULTIPLE = 1.5
DISCOVERY_V1_ATR_ADVERSE_MULTIPLE = 1.0
DISCOVERY_V1_MIN_FAVORABLE_PCT = 3.0
DISCOVERY_V1_MIN_ADVERSE_PCT = 2.0

# Exclusive Stage-0 attribution categories. Later V2 PRs add forecasting /
# pair-selection / AI / execution regret without reusing these names.
ATTRIBUTION_NOT_OBSERVED = "NOT_OBSERVED"
ATTRIBUTION_DATA_UNAVAILABLE = "DATA_UNAVAILABLE"
ATTRIBUTION_EXCLUDED_MARKET = "EXCLUDED_MARKET"
ATTRIBUTION_BELOW_THRESHOLD = "BELOW_THRESHOLD"
ATTRIBUTION_RANKED_OUTSIDE_BUDGET = "RANKED_OUTSIDE_BUDGET"
ATTRIBUTION_ADMITTED = "ADMITTED"

STAGE0_ATTRIBUTION_CATEGORIES = (
    ATTRIBUTION_NOT_OBSERVED,
    ATTRIBUTION_DATA_UNAVAILABLE,
    ATTRIBUTION_EXCLUDED_MARKET,
    ATTRIBUTION_BELOW_THRESHOLD,
    ATTRIBUTION_RANKED_OUTSIDE_BUDGET,
    ATTRIBUTION_ADMITTED,
)

# ScreeningOutcome → exclusive discovery attribution for an observed row.
# Downstream funnel/risk/execution rejections stay ADMITTED at Stage-0.
SCREENING_TO_ATTRIBUTION = {
    "ADVANCED": ATTRIBUTION_ADMITTED,
    "COARSE_RANK_LIMIT": ATTRIBUTION_RANKED_OUTSIDE_BUDGET,
    "BELOW_THRESHOLD": ATTRIBUTION_BELOW_THRESHOLD,
    "BELOW_COARSE_THRESHOLD": ATTRIBUTION_BELOW_THRESHOLD,
    "DATA_UNAVAILABLE": ATTRIBUTION_DATA_UNAVAILABLE,
    "EXCLUDED_MARKET": ATTRIBUTION_EXCLUDED_MARKET,
}

COMPUTE_MEASUREMENT_SCOPE = "BROAD_DISCOVERY_AND_SELECTION"

WINNER_INCOMPLETE = "INCOMPLETE"
WINNER_WINNER = "WINNER"
WINNER_NON_WINNER = "NON_WINNER"

MATURATION_NO_FORWARD_DATA = "NO_FORWARD_DATA"
MATURATION_INCOMPLETE = "INCOMPLETE_HORIZON"
MATURATION_PARTIAL = "PARTIAL_FORWARD_WINDOW"
MATURATION_COMPLETE = "COMPLETE_HORIZON"
