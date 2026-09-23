"""Approved IC-008 configuration values and the credential-surface template.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests hold the values OWNER approved, so a later edit that changes a price,
a ceiling, or a per-attempt bound has to change a test too rather than slipping
through. They also assert the credential template carries no real credential.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.opip.committee.registry import (
    APPROVED_DEADLINE_SECONDS,
    APPROVED_MAX_CASE_COST_MICROUNITS,
    APPROVED_MAX_DAILY_COST_MICROUNITS,
    APPROVED_MAX_OUTPUT_TOKENS,
    APPROVED_PRICE_BOOK_SPEC,
    APPROVED_SHADOW_MODELS,
    APPROVED_SHADOW_REASONING_MODE,
    APPROVED_SHADOW_ROLE_PRIMARIES,
    CACHE_SAVINGS_CREDITED,
    ReasoningMode,
    approved_price_book,
    default_shadow_registry,
)
from app.opip.committee.contracts import ProviderFamily
from app.opip.committee.roles import CommitteeRole

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2027, 9, 23, 12, 0, tzinfo=timezone.utc)
DEPLOY_DIR = Path(__file__).resolve().parents[1] / "deploy" / "committee"


# ------------------------------------------------------------ price book


def test_the_approved_price_book_rates_are_exact():
    book = approved_price_book()
    terra = book.price_for("openai", APPROVED_SHADOW_MODELS[ProviderFamily.OPENAI])
    sonnet = book.price_for("anthropic", APPROVED_SHADOW_MODELS[ProviderFamily.ANTHROPIC])
    assert terra is not None and sonnet is not None
    # $2.00 / 1M input, $12.00 / 1M output for Terra.
    assert terra.microunits_per_million_input_tokens == 2_000_000
    assert terra.microunits_per_million_output_tokens == 12_000_000
    # $2.00 / 1M input, $10.00 / 1M output for Sonnet 5.
    assert sonnet.microunits_per_million_input_tokens == 2_000_000
    assert sonnet.microunits_per_million_output_tokens == 10_000_000


def test_cost_uses_the_declared_rates():
    book = approved_price_book()
    # 1M input + 1M output on Terra = $2 + $12 = $14 = 14_000_000 microunits.
    assert (
        book.cost_microunits(
            provider="openai",
            model="gpt-5.6-terra",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        == 14_000_000
    )
    # 1M input + 1M output on Sonnet 5 = $2 + $10 = $12 = 12_000_000 microunits.
    assert (
        book.cost_microunits(
            provider="anthropic",
            model="claude-sonnet-5",
            input_tokens=1_000_000,
            output_tokens=1_000_000,
        )
        == 12_000_000
    )


def test_an_unmatched_price_is_unknown_not_free():
    book = approved_price_book()
    assert (
        book.cost_microunits(
            provider="openai", model="some-other-model", input_tokens=1, output_tokens=1
        )
        is None
    )


def test_an_unknown_token_count_makes_cost_unknown():
    """A missing token count must not be treated as zero tokens."""
    book = approved_price_book()
    assert (
        book.cost_microunits(
            provider="openai", model="gpt-5.6-terra", input_tokens=None, output_tokens=5
        )
        is None
    )


def test_prompt_cache_savings_are_not_credited_yet():
    """Cache cost is unmeasured, so spend is accounted at uncached rates."""
    assert CACHE_SAVINGS_CREDITED is False
    # The spec carries no cache rate, so no rate can be applied even accidentally.
    assert "cache" not in APPROVED_PRICE_BOOK_SPEC.lower()
    book = approved_price_book()
    price = book.price_for("openai", "gpt-5.6-terra")
    assert not hasattr(price, "cached_input_microunits")
    assert not hasattr(price, "microunits_per_million_cached_input_tokens")


# ------------------------------------------------------ ceilings and bounds


def test_the_approved_ceilings_are_the_owner_values():
    assert APPROVED_MAX_CASE_COST_MICROUNITS == 500_000  # $0.50
    assert APPROVED_MAX_DAILY_COST_MICROUNITS == 10_000_000  # $10


def test_the_approved_per_attempt_bounds_are_the_owner_values():
    assert APPROVED_DEADLINE_SECONDS == 45
    assert APPROVED_MAX_OUTPUT_TOKENS == 1_200
    assert APPROVED_SHADOW_REASONING_MODE is ReasoningMode.LOW


# ---------------------------------------------------------- route table


def test_the_registry_applies_every_approved_value():
    registry = default_shadow_registry(effective_from=NOW, review_by=LATER)
    assert registry.entries
    for entry in registry.entries:
        assert entry.deadline_seconds == APPROVED_DEADLINE_SECONDS
        assert entry.max_output_tokens == APPROVED_MAX_OUTPUT_TOKENS
        assert entry.max_cost_microunits == APPROVED_MAX_CASE_COST_MICROUNITS
        assert entry.reasoning_mode is APPROVED_SHADOW_REASONING_MODE
        assert entry.model_id in APPROVED_SHADOW_MODELS.values()


def test_the_cross_vendor_primary_distribution_is_preserved():
    """Both vendors must answer real roles, so the disagreement has independent evidence."""
    primaries = set(APPROVED_SHADOW_ROLE_PRIMARIES.values())
    assert primaries == {ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC}
    for family in primaries:
        assert (
            sum(
                1
                for value in APPROVED_SHADOW_ROLE_PRIMARIES.values()
                if value is family
            )
            >= 2
        )
    registry = default_shadow_registry(effective_from=NOW, review_by=LATER)
    for role in CommitteeRole:
        primary_id, fallback_id = registry.routes[role]
        primary = registry.entry(primary_id)
        fallback = registry.entry(fallback_id)
        # Exactly one fallback, from the other vendor, sharing the reservation.
        assert primary.provider_family is not fallback.provider_family
        assert primary.max_output_tokens == fallback.max_output_tokens
        assert primary.deadline_seconds == fallback.deadline_seconds
        assert primary.max_cost_microunits == fallback.max_cost_microunits


# ------------------------------------------------ credential-surface template


def test_the_credential_template_contains_only_placeholders():
    """A real credential must never be committed, so every value is a placeholder."""
    template = (DEPLOY_DIR / "committee-credentials.env.example").read_text(
        encoding="utf-8"
    )
    values = [
        line.split("=", 1)[1].strip()
        for line in template.splitlines()
        if line.strip() and not line.startswith("#") and "=" in line
    ]
    assert values, "the template must declare its variables"
    for credential in (
        "OPIP_COMMITTEE_OPENAI_API_KEY",
        "OPIP_COMMITTEE_ANTHROPIC_API_KEY",
    ):
        line = next(
            item for item in template.splitlines() if item.startswith(credential)
        )
        assert line.split("=", 1)[1].strip() == "CHANGEME", line


def test_the_template_declares_mode_off_and_the_approved_non_secrets():
    template = (DEPLOY_DIR / "committee-credentials.env.example").read_text(
        encoding="utf-8"
    )
    assert "OPIP_COMMITTEE_MODE=off" in template
    assert APPROVED_PRICE_BOOK_SPEC in template
    assert "OPIP_COMMITTEE_MAX_ESTIMATED_COST_MICROUNITS=500000" in template
    assert "OPIP_COMMITTEE_RELEASE_SHA=CHANGEME" in template


def test_the_template_names_the_credential_variables_the_adapter_reads():
    from app.opip.committee.transports import CREDENTIAL_ENV_NAMES

    template = (DEPLOY_DIR / "committee-credentials.env.example").read_text(
        encoding="utf-8"
    )
    for variable in CREDENTIAL_ENV_NAMES.values():
        assert variable in template, variable


def test_the_template_carries_no_trading_credential_name():
    template = (DEPLOY_DIR / "committee-credentials.env.example").read_text(
        encoding="utf-8"
    ).lower()
    for forbidden in ("kraken", "telegram", "webhook", "ssh_private", "private_key"):
        assert forbidden not in template, forbidden


def test_the_deployment_artifacts_exist_and_stay_dark():
    """The prepared change must be reviewable and inert."""
    for name in (
        "opip-committee-shadow.service",
        "opip-committee-shadow.timer",
        "run-committee-shadow-cycle.sh",
        "bootstrap-opip-committee-worker.sh",
        "verify-committee-isolation.sh",
        "committee-credentials.env.example",
        "README.md",
    ):
        assert (DEPLOY_DIR / name).exists(), name

    service = (DEPLOY_DIR / "opip-committee-shadow.service").read_text(encoding="utf-8")
    assert "Environment=OPIP_COMMITTEE_MODE=off" in service
    # No trading credential is referenced, and hardening is present.
    assert "kraken" not in service.lower()
    assert "telegram" not in service.lower()
    for directive in (
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "MemoryMax=",
        "CPUQuota=",
        "RestrictAddressFamilies=",
        "IPAddressDeny=",
        "ReadOnlyPaths=",
    ):
        assert directive in service, directive

    timer = (DEPLOY_DIR / "opip-committee-shadow.timer").read_text(encoding="utf-8")
    # The timer must not backfill missed cycles.
    assert "Persistent=false" in timer


def test_the_worker_script_requires_an_exact_release_and_records_off():
    script = (DEPLOY_DIR / "run-committee-shadow-cycle.sh").read_text(encoding="utf-8")
    assert "OPIP_COMMITTEE_RELEASE_SHA" in script
    assert "SKIPPED_MODE_OFF" in script
    assert 'MODE="${OPIP_COMMITTEE_MODE:-off}"' in script


def test_the_verify_script_checks_every_isolation_claim():
    script = (DEPLOY_DIR / "verify-committee-isolation.sh").read_text(encoding="utf-8")
    for claim in (
        "release identity is an exact 40-character SHA",
        "mode is off in the environment file",
        "no committee work",
        "ReadOnlyPaths",
        "ReadWritePaths",
        "trading-credential names",
        "no listening socket",
        "IPAddressDeny",
        "memory limit applied",
        "disable path is available",
        "no committee container is running",
        "ISOLATION_PROOF=",
    ):
        assert claim in script, claim
