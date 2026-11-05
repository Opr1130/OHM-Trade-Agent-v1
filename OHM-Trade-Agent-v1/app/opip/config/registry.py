"""Schema-driven configuration registry for the Control Center V2 Admin Console.

The registry declares *metadata*: what may be changed, in which domain, within
which bounds, by which authority. It never declares the effective value of a
runtime setting, and it never becomes the source of truth for one.

Two hard rules are encoded here rather than enforced by the UI:

* a definition whose ``activation_authority`` is ``DEPLOYMENT_OPERATOR`` cannot
  be activated from a console - only staged and reviewed;
* nothing in the F11 domain may be edited into a state that widens trading
  authority, because the validator rejects the value, not just the button.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

DOMAIN_V2_SCORING = "V2_SCORING"
DOMAIN_ATTRIBUTION = "V1_V2_ATTRIBUTION"
DOMAIN_SCANNER = "SCANNER"
DOMAIN_F11 = "F11_SAFETY"
DOMAIN_AI = "AI_ROUTING"
DOMAIN_RETENTION = "RETENTION_RECONCILIATION"
DOMAIN_RESOURCES = "SQLITE_RESOURCE_BUDGETS"
DOMAIN_DASHBOARD = "DASHBOARD"

DOMAINS: tuple[str, ...] = (
    DOMAIN_V2_SCORING,
    DOMAIN_ATTRIBUTION,
    DOMAIN_SCANNER,
    DOMAIN_F11,
    DOMAIN_AI,
    DOMAIN_RETENTION,
    DOMAIN_RESOURCES,
    DOMAIN_DASHBOARD,
)

TYPE_NUMBER = "NUMBER"
TYPE_INTEGER = "INTEGER"
TYPE_BOOLEAN = "BOOLEAN"
TYPE_ENUM = "ENUM"
TYPE_STRING = "STRING"
TYPE_SECRET_REFERENCE = "SECRET_REFERENCE"  # nosec B105 - a classification label, not a credential
TYPE_ARRAY = "ARRAY"
TYPE_DERIVED = "DERIVED"

SENSITIVITY_NONE = "NONE"
SENSITIVITY_INTERNAL = "INTERNAL"
SENSITIVITY_SECRET_REFERENCE = "SECRET_REFERENCE"  # nosec B105 - a classification label, not a credential

EDIT_READ_ONLY = "READ_ONLY"
EDIT_STAGE_ONLY = "STAGE_ONLY"
EDIT_STAGE_AND_ACTIVATE = "STAGE_AND_ACTIVATE"

AUTHORITY_NONE = "NONE"
AUTHORITY_ADVISORY_PROFILE = "ADVISORY_PROFILE"
AUTHORITY_DEPLOYMENT_OPERATOR = "DEPLOYMENT_OPERATOR"

#: V2 rubric component weights as declared by the versioned scoring profile.
V2_COMPONENTS: tuple[str, ...] = (
    "higher_timeframe_adx",
    "ema_stack_extension",
    "relative_volume",
    "rsi",
    "macd_acceleration",
    "volatility_percentile",
)


@dataclass(frozen=True)
class ConfigDefinition:
    key: str
    domain: str
    display_name: str
    data_type: str
    description: str
    default_value: Any = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    step: float | None = None
    unit: str | None = None
    allowed: tuple[str, ...] = ()
    sensitivity: str = SENSITIVITY_NONE
    editability: str = EDIT_READ_ONLY
    activation_authority: str = AUTHORITY_NONE
    read_only_reason: str | None = None
    source_of_truth: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "domain": self.domain,
            "display_name": self.display_name,
            "data_type": self.data_type,
            "description": self.description,
            "default_value": self.default_value,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "step": self.step,
            "unit": self.unit,
            "allowed": list(self.allowed),
            "sensitivity": self.sensitivity,
            "editability": self.editability,
            "activation_authority": self.activation_authority,
            "read_only_reason": self.read_only_reason,
            "source_of_truth": self.source_of_truth,
            "tags": list(self.tags),
            "registry_version": 1,
        }


def _d(**kwargs: Any) -> ConfigDefinition:
    return ConfigDefinition(**kwargs)



_DEFINITIONS_V2: tuple[ConfigDefinition, ...] = (
    *tuple(
        _d(
            key=f"v2.weights.{component}",
            domain=DOMAIN_V2_SCORING,
            display_name=f"V2 weight · {component.replace('_', ' ')}",
            data_type=TYPE_NUMBER,
            description=(
                "Rubric weight for one V2 component. The six weights must total "
                "exactly 100; the validator rejects any other sum instead of "
                "renormalising it."
            ),
            minimum=0,
            maximum=100,
            step=1,
            unit="points",
            editability=EDIT_STAGE_ONLY,
            activation_authority=AUTHORITY_ADVISORY_PROFILE,
            source_of_truth="app/scanner/technical_score_v2.json",
            tags=("rubric", "weights"),
        )
        for component in V2_COMPONENTS
    ),
    _d(
        key="v2.threshold",
        domain=DOMAIN_V2_SCORING,
        display_name="V2 qualification threshold",
        data_type=TYPE_NUMBER,
        description=(
            "Score a V2 directional candidate must reach. Staging it changes no "
            "runtime scoring until the advisory profile is separately activated."
        ),
        minimum=50,
        maximum=95,
        step=1,
        unit="points",
        editability=EDIT_STAGE_AND_ACTIVATE,
        activation_authority=AUTHORITY_ADVISORY_PROFILE,
        source_of_truth="app/core/strategy_policy.json#scoring.v2_threshold",
        tags=("threshold",),
    ),
    _d(
        key="v2.adx_min",
        domain=DOMAIN_V2_SCORING,
        display_name="ADX regime floor",
        data_type=TYPE_NUMBER,
        minimum=0,
        maximum=60,
        step=1,
        description="Higher-timeframe ADX below this value scores zero ADX points.",
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_ADVISORY_PROFILE,
        source_of_truth="app/scanner/technical_score_v2.json#parameters.adx_min",
    ),
    _d(
        key="v2.rvol_zero",
        domain=DOMAIN_V2_SCORING,
        display_name="Relative-volume zero point",
        data_type=TYPE_NUMBER,
        minimum=0.1,
        maximum=5,
        step=0.1,
        description="Relative volume at or below this value earns no volume points.",
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_ADVISORY_PROFILE,
        source_of_truth="app/scanner/technical_score_v2.json#parameters.rvol_zero",
    ),
    _d(
        key="v2.rvol_full",
        domain=DOMAIN_V2_SCORING,
        display_name="Relative-volume full point",
        data_type=TYPE_NUMBER,
        minimum=0.2,
        maximum=10,
        step=0.1,
        description="Relative volume at or above this value earns full volume points.",
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_ADVISORY_PROFILE,
        source_of_truth="app/scanner/technical_score_v2.json#parameters.rvol_full",
    ),
    _d(
        key="v2.profile_mode",
        domain=DOMAIN_V2_SCORING,
        display_name="Scorer profile mode",
        data_type=TYPE_DERIVED,
        description=(
            "Declared mode of the scoring profile file. This is NOT the production "
            "selector: OPIP_PRODUCTION_SCORING_VERSION decides authority at runtime, "
            "and the two are shown separately so they are never conflated."
        ),
        editability=EDIT_READ_ONLY,
        read_only_reason="DECLARED_BY_SCORING_PROFILE_FILE",
        source_of_truth="app/scanner/technical_score_v2.json#mode",
    ),
    _d(
        key="v2.production_selector",
        domain=DOMAIN_V2_SCORING,
        display_name="Production scoring selector",
        data_type=TYPE_DERIVED,
        description=(
            "Runtime scoring authority selected by deployment configuration. It "
            "cannot be changed from this console."
        ),
        editability=EDIT_READ_ONLY,
        read_only_reason="DEPLOYMENT_ENVIRONMENT_SETTING",
        source_of_truth="ENV:OPIP_PRODUCTION_SCORING_VERSION",
    ),
)


_DEFINITIONS_ATTRIBUTION_SCANNER: tuple[ConfigDefinition, ...] = (
    _d(
        key="attribution.outcome_maturity_hours",
        domain=DOMAIN_ATTRIBUTION,
        display_name="Outcome maturity window",
        data_type=TYPE_INTEGER,
        minimum=1,
        maximum=168,
        step=1,
        unit="hours",
        description=(
            "How long a signal must be observed before its outcome may be called "
            "matured. Below this window every attribution figure stays "
            "OUTCOME NOT MATURED."
        ),
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_ADVISORY_PROFILE,
        source_of_truth="OPIP_ANALYTICS (staged only)",
    ),
    _d(
        key="attribution.min_sample_size",
        domain=DOMAIN_ATTRIBUTION,
        display_name="Minimum attribution sample",
        data_type=TYPE_INTEGER,
        minimum=10,
        maximum=1000,
        step=5,
        description=(
            "Matured outcomes required before an avoided-loss or missed-winner "
            "claim may be displayed at all."
        ),
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_ADVISORY_PROFILE,
    ),
    _d(
        key="attribution.v1_threshold",  # gitleaks:allow - a registry identifier, not a credential
        domain=DOMAIN_ATTRIBUTION,
        display_name="V1 comparison threshold",
        data_type=TYPE_NUMBER,
        minimum=50,
        maximum=100,
        step=1,
        unit="points",
        default_value=80,
        description=(
            "V1 score used only to classify a recorded candidate for comparison. "
            "It does not change V1 selection."
        ),
        editability=EDIT_READ_ONLY,
        read_only_reason="FIXED_COMPARISON_CONTRACT",
        source_of_truth="app/services/technical_scoring_comparison.py",
    ),
    _d(
        key="attribution.round_trip_cost_pct",
        domain=DOMAIN_ATTRIBUTION,
        display_name="Assumed round-trip cost",
        data_type=TYPE_NUMBER,
        minimum=0,
        maximum=5,
        step=0.01,
        unit="percent",
        description=(
            "Transaction-cost assumption applied to hypothetical attribution. "
            "Displayed with every economic estimate."
        ),
        editability=EDIT_STAGE_AND_ACTIVATE,
        activation_authority=AUTHORITY_ADVISORY_PROFILE,
        source_of_truth=(
            "app/core/strategy_policy.json#advisory_economics."
            "estimated_round_trip_cost_pct"
        ),
    ),
    _d(
        key="scanner.execution_mode",
        domain=DOMAIN_SCANNER,
        display_name="Scanner execution mode",
        data_type=TYPE_ENUM,
        allowed=("legacy_inprocess", "isolated", "suspended"),
        description=(
            "Which acquisition path is permitted. Changing it is a deployment "
            "decision, not a dashboard setting."
        ),
        editability=EDIT_READ_ONLY,
        read_only_reason="DEPLOYMENT_ENVIRONMENT_SETTING",
        source_of_truth="ENV:OPIP_SCAN_EXECUTION_MODE",
    ),
    _d(
        key="scanner.dispatch_token_seconds",
        domain=DOMAIN_SCANNER,
        display_name="Dispatch token lifetime",
        data_type=TYPE_INTEGER,
        minimum=30,
        maximum=600,
        step=10,
        unit="seconds",
        description="How long a written admission token stays actionable.",
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_DEPLOYMENT_OPERATOR,
        source_of_truth="ENV:OPIP_SCAN_DISPATCH_TOKEN_SECONDS",
    ),
    _d(
        key="scanner.core_memory_ceiling_pct",
        domain=DOMAIN_SCANNER,
        display_name="Core cgroup ceiling",
        data_type=TYPE_NUMBER,
        minimum=1,
        maximum=100,
        step=1,
        unit="percent",
        description=(
            "Core-cgroup utilisation above which discovery is not admitted. "
            "Lowering this never increases discovery."
        ),
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_DEPLOYMENT_OPERATOR,
        source_of_truth="ENV:OPIP_SCAN_CORE_MEMORY_CEILING_PCT",
    ),
    _d(
        key="scanner.max_candidates",
        domain=DOMAIN_SCANNER,
        display_name="Candidate cap",
        data_type=TYPE_INTEGER,
        minimum=1,
        maximum=8,
        step=1,
        default_value=8,
        description=(
            "Maximum promoted candidates. The production cap is fixed at 8 and any "
            "other value is refused as production cap drift."
        ),
        editability=EDIT_READ_ONLY,
        read_only_reason="PRODUCTION_CAP_FIXED",
        source_of_truth="app/core/strategy_policy.json#scoring.max_candidates",
    ),
)



_DEFINITIONS_SAFETY_AI: tuple[ConfigDefinition, ...] = (
    _d(
        key="f11.state",
        domain=DOMAIN_F11,
        display_name="Protection state",
        data_type=TYPE_DERIVED,
        description=(
            "Canonical F11 protection state, read from the protection artifact. "
            "Unknown values are displayed as unknown and are never treated as safe."
        ),
        allowed=("ARMED", "TRIGGERED_HALT", "OVERRIDE_SHADOW"),
        editability=EDIT_READ_ONLY,
        read_only_reason="PROTECTION_IS_INDEPENDENT",
        source_of_truth="OPIP_F11_STATE_PATH",
    ),
    _d(
        key="f11.new_admissions_allowed",
        domain=DOMAIN_F11,
        display_name="New-entry admission",
        data_type=TYPE_DERIVED,
        description=(
            "Whether new entries are admitted. Viewing or staging this value never "
            "changes it: admission is owned by the protection and incident "
            "lifecycle, not by this console."
        ),
        editability=EDIT_READ_ONLY,
        read_only_reason="PROTECTION_IS_INDEPENDENT",
        source_of_truth="app/services/protection_health.py",
    ),
    _d(
        key="f11.allow_funded_orders",  # gitleaks:allow - a registry identifier, not a credential
        domain=DOMAIN_F11,
        display_name="Funded order permission",
        data_type=TYPE_BOOLEAN,
        default_value=False,
        description=(
            "Funded order authority is absent by architecture. The validator "
            "rejects any attempt to stage it as true."
        ),
        editability=EDIT_READ_ONLY,
        read_only_reason="FUNDED_TRADING_ABSENT",
        source_of_truth="app/core/strategy_policy.json#protection",
    ),
    _d(
        key="ai.provider",
        domain=DOMAIN_AI,
        display_name="Advisory provider",
        data_type=TYPE_ENUM,
        allowed=("openai", "none"),
        description=(
            "Which advisory provider may be called. Selecting a provider does not "
            "enable it and never spends credits from this console."
        ),
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_DEPLOYMENT_OPERATOR,
        source_of_truth="ENV:AI_ENABLED / ENV:OPENAI_MODEL",
    ),
    _d(
        key="ai.model",
        domain=DOMAIN_AI,
        display_name="Advisory model identifier",
        data_type=TYPE_STRING,
        description="Model identifier used when advisory calls are enabled.",
        editability=EDIT_READ_ONLY,
        read_only_reason="DEPLOYMENT_ENVIRONMENT_SETTING",
        source_of_truth="ENV:OPENAI_MODEL",
    ),
    _d(
        key="ai.mode",
        domain=DOMAIN_AI,
        display_name="Advisory mode",
        data_type=TYPE_ENUM,
        allowed=("advisory_only",),
        default_value="advisory_only",
        description=(
            "AI output is advisory evidence. The validator rejects any other mode "
            "because it would imply execution authority."
        ),
        editability=EDIT_READ_ONLY,
        read_only_reason="ARCHITECTURE_INVARIANT",
        source_of_truth="app/core/strategy_policy.json#ai_advisory.mode",
    ),
    _d(
        key="ai.daily_budget_usd",
        domain=DOMAIN_AI,
        display_name="Daily advisory budget",
        data_type=TYPE_NUMBER,
        minimum=0,
        maximum=100,
        step=0.5,
        unit="USD",
        description=(
            "Bounded daily spend for advisory calls. Zero means no paid call may be "
            "attempted."
        ),
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_DEPLOYMENT_OPERATOR,
    ),
    _d(
        key="ai.timeout_seconds",
        domain=DOMAIN_AI,
        display_name="Advisory timeout",
        data_type=TYPE_INTEGER,
        minimum=1,
        maximum=60,
        step=1,
        unit="seconds",
        description="Per-call timeout for an advisory request.",
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_DEPLOYMENT_OPERATOR,
    ),
    _d(
        key="ai.api_key",
        domain=DOMAIN_AI,
        display_name="Provider credential reference",
        data_type=TYPE_SECRET_REFERENCE,
        sensitivity=SENSITIVITY_SECRET_REFERENCE,
        description=(
            "A reference to the approved secret store. Secret values are never "
            "returned by this API, never displayable and never storable here."
        ),
        editability=EDIT_READ_ONLY,
        read_only_reason="SECRETS_OUT_OF_BAND",
        source_of_truth="APPROVED_SECRET_MANAGEMENT",
    ),
)



_DEFINITIONS_PLATFORM: tuple[ConfigDefinition, ...] = (
    _d(
        key="retention.days",
        domain=DOMAIN_RETENTION,
        display_name="Analytical retention window",
        data_type=TYPE_ENUM,
        allowed=("7", "15", "30", "60", "90"),
        default_value="30",
        description=(
            "Retention for verified derived analytical rows only. Canonical JSONL, "
            "quarantine evidence, reconciliation and configuration audit are never "
            "purged by this policy."
        ),
        editability=EDIT_STAGE_AND_ACTIVATE,
        activation_authority=AUTHORITY_ADVISORY_PROFILE,
        source_of_truth="OPIP_ANALYTICS_RETENTION_DAYS",
    ),
    _d(
        key="retention.delete_enabled",
        domain=DOMAIN_RETENTION,
        display_name="Retention deletion enabled",
        data_type=TYPE_DERIVED,
        default_value=False,
        description=(
            "Reported from OPIP_ENABLE_RETENTION_DELETE. Housekeeping runs in "
            "dry-run unless deletion is separately authorized."
        ),
        editability=EDIT_READ_ONLY,
        read_only_reason="REQUIRES_SEPARATE_OWNER_AUTHORIZATION",
        source_of_truth="ENV:OPIP_ENABLE_RETENTION_DELETE",
    ),
    _d(
        key="ingestion.batch_records",
        domain=DOMAIN_RETENTION,
        display_name="Ingestion batch size",
        data_type=TYPE_INTEGER,
        minimum=1,
        maximum=5000,
        step=50,
        unit="records",
        description="Records projected per bounded ingestion transaction.",
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_DEPLOYMENT_OPERATOR,
        source_of_truth="ENV:OPIP_ANALYTICS_INGEST_BATCH_RECORDS",
    ),
    _d(
        key="ingestion.max_bytes",
        domain=DOMAIN_RETENTION,
        display_name="Ingestion byte budget",
        data_type=TYPE_INTEGER,
        minimum=65536,
        maximum=33554432,
        step=65536,
        unit="bytes",
        description="Bytes read per bounded ingestion run.",
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_DEPLOYMENT_OPERATOR,
        source_of_truth="ENV:OPIP_ANALYTICS_INGEST_MAX_BYTES",
    ),
    _d(
        key="reconciliation.sample_records",
        domain=DOMAIN_RETENTION,
        display_name="Reconciliation re-hash sample",
        data_type=TYPE_INTEGER,
        minimum=1,
        maximum=50,
        step=1,
        unit="records",
        description="How many stored records are re-hashed against the source per run.",
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_DEPLOYMENT_OPERATOR,
    ),
    _d(
        key="sqlite.busy_timeout_ms",
        domain=DOMAIN_RESOURCES,
        display_name="SQLite busy timeout",
        data_type=TYPE_INTEGER,
        minimum=100,
        maximum=60000,
        step=100,
        unit="ms",
        description=(
            "How long a connection waits for the write lock. Applied at connection "
            "time from deployment configuration."
        ),
        editability=EDIT_READ_ONLY,
        read_only_reason="CONNECTION_TIME_SETTING",
        source_of_truth="ENV:OPIP_ANALYTICS_BUSY_TIMEOUT_MS",
    ),
    _d(
        key="sqlite.query_timeout_seconds",
        domain=DOMAIN_RESOURCES,
        display_name="Analytical query timeout",
        data_type=TYPE_NUMBER,
        minimum=0.5,
        maximum=30,
        step=0.5,
        unit="seconds",
        description=(
            "Upper bound for one dashboard analytical query. Heavy analytics belong "
            "on the analytical host, not on the trading host."
        ),
        editability=EDIT_READ_ONLY,
        read_only_reason="CONNECTION_TIME_SETTING",
        source_of_truth="ENV:OPIP_ANALYTICS_QUERY_TIMEOUT_SECONDS",
    ),
    _d(
        key="sqlite.page_size_max",
        domain=DOMAIN_RESOURCES,
        display_name="Maximum page size",
        data_type=TYPE_INTEGER,
        minimum=10,
        maximum=1000,
        step=10,
        unit="rows",
        description="Hard ceiling on rows returned by one analytical page.",
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_DEPLOYMENT_OPERATOR,
        source_of_truth="ENV:OPIP_ANALYTICS_PAGE_SIZE_MAX",
    ),
)



_DEFINITIONS_DASHBOARD: tuple[ConfigDefinition, ...] = (
    _d(
        key="dashboard.refresh_seconds",
        domain=DOMAIN_DASHBOARD,
        display_name="Refresh interval",
        data_type=TYPE_INTEGER,
        minimum=15,
        maximum=3600,
        step=15,
        unit="seconds",
        description="How often an open console re-reads its evidence.",
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_ADVISORY_PROFILE,
    ),
    _d(
        key="dashboard.stale_after_seconds",
        domain=DOMAIN_DASHBOARD,
        display_name="Stale threshold",
        data_type=TYPE_INTEGER,
        minimum=30,
        maximum=86400,
        step=30,
        unit="seconds",
        description=(
            "Age at which a panel stops presenting itself as current and must show "
            "an explicit stale warning."
        ),
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_ADVISORY_PROFILE,
    ),
    _d(
        key="dashboard.default_window_days",
        domain=DOMAIN_DASHBOARD,
        display_name="Default window",
        data_type=TYPE_ENUM,
        allowed=("1", "7", "30", "90"),
        default_value="7",
        description=(
            "Default observation window for the signal workspace. It is clamped to "
            "the active retention policy so a preset can never promise evidence "
            "that has been purged."
        ),
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_ADVISORY_PROFILE,
    ),
    _d(
        key="dashboard.export_row_limit",
        domain=DOMAIN_DASHBOARD,
        display_name="Export row limit",
        data_type=TYPE_INTEGER,
        minimum=100,
        maximum=50000,
        step=100,
        unit="rows",
        description="Bounded export size for CSV and JSON downloads.",
        editability=EDIT_STAGE_ONLY,
        activation_authority=AUTHORITY_DEPLOYMENT_OPERATOR,
    ),
)


DEFINITIONS: tuple[ConfigDefinition, ...] = (
    _DEFINITIONS_V2
    + _DEFINITIONS_ATTRIBUTION_SCANNER
    + _DEFINITIONS_SAFETY_AI
    + _DEFINITIONS_PLATFORM
    + _DEFINITIONS_DASHBOARD
)

_BY_KEY: dict[str, ConfigDefinition] = {item.key: item for item in DEFINITIONS}


def definition(key: str) -> ConfigDefinition | None:
    return _BY_KEY.get(key)


def definitions_for(domain: str | None = None) -> list[ConfigDefinition]:
    if domain is None:
        return list(DEFINITIONS)
    return [item for item in DEFINITIONS if item.domain == domain]


def registry_document() -> list[dict[str, Any]]:
    return [item.as_dict() for item in DEFINITIONS]


def iter_keys() -> Iterable[str]:
    return (item.key for item in DEFINITIONS)

