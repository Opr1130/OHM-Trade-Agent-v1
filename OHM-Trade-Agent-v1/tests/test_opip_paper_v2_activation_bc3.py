"""B/C-3 Paper v2 activation-gate tests.

These prove the activation gate itself, which is the only part of B/C-3 that is
independent of the producer work: Paper v2 must be inactive by default, an
unrecognised configuration must never activate it, and activation must carry
paper-only authority with no reachable funded/live surface.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import Settings, get_settings
from app.services.paper_v2_activation import (
    PAPER_V2_MODE_ACTIVE,
    PAPER_V2_MODE_OFF,
    PAPER_V2_MODES,
    paper_v2_active,
    resolve_paper_v2_mode,
)

APP_ROOT = Path(__file__).resolve().parents[1] / "app"
ACTIVATION_MODULE = APP_ROOT / "services" / "paper_v2_activation.py"

#: Import roots that must never be reachable from the activation gate. Any of
#: these would mean activation had gained funded/live or exchange authority.
FORBIDDEN_IMPORT_ROOTS = (
    "app.exchanges",
    "app.scanner",
    "ccxt",
    "krakenex",
    "requests",
    "httpx",
)


BASE_SETTINGS = {"webhook_secret": "test-webhook-secret"}


def _settings(**overrides):
    return Settings(**BASE_SETTINGS, **overrides)


@dataclass(frozen=True)
class _BareSettings:
    """A settings-like object with no Paper v2 field at all (legacy callers)."""

    some_other_field: str = "value"


def test_paper_v2_is_inactive_by_default():
    """The shipped default must be off, and default settings must report inactive."""
    assert _settings().opip_paper_v2_mode == PAPER_V2_MODE_OFF
    assert paper_v2_active(_settings()) is False
    assert resolve_paper_v2_mode(_settings()) == PAPER_V2_MODE_OFF


def test_paper_v2_active_requires_the_explicit_opt_in_value():
    assert resolve_paper_v2_mode(_settings(opip_paper_v2_mode="active")) == (
        PAPER_V2_MODE_ACTIVE
    )
    assert paper_v2_active(_settings(opip_paper_v2_mode="active")) is True


def test_only_supported_modes_are_recognised():
    assert PAPER_V2_MODES == {"off", "active"}


@pytest.mark.parametrize(
    "value",
    [
        "on",
        "true",
        "True",
        "yes",
        "enabled",
        "enable",
        "shadow",
        "live",
        "funded",
        "real",
        "paper",
        "1",
        "activ",
        "activex",
        "offf",
        "",
        "   ",
        None,
        42,
        object(),
    ],
)
def test_unrecognised_configuration_cannot_activate_paper_v2(value):
    """An unknown or malformed value resolves to inactive, never to active."""
    settings = type("S", (), {"opip_paper_v2_mode": value})()
    assert resolve_paper_v2_mode(settings) == PAPER_V2_MODE_OFF
    assert paper_v2_active(settings) is False


def test_missing_field_cannot_activate_paper_v2():
    """Legacy callers without the field stay inactive rather than defaulting on."""
    assert resolve_paper_v2_mode(_BareSettings()) == PAPER_V2_MODE_OFF
    assert paper_v2_active(_BareSettings()) is False


def test_unreadable_process_configuration_cannot_activate_paper_v2(monkeypatch):
    """With no explicit settings, an unreadable process config stays inactive."""
    import app.services.paper_v2_activation as activation

    def _boom():
        raise RuntimeError("configuration unreadable")

    monkeypatch.setattr("app.core.config.get_settings", _boom)
    assert activation.resolve_paper_v2_mode(None) == PAPER_V2_MODE_OFF
    assert activation.paper_v2_active(None) is False


@pytest.mark.parametrize("value", ["on", "true", "live", "funded", "shadow", "ACTIVE "])
def test_malformed_environment_value_fails_settings_parsing(value, monkeypatch):
    """A typo in the environment must fail loudly, not silently activate."""
    monkeypatch.setenv("OPIP_PAPER_V2_MODE", value)
    with pytest.raises(Exception):
        Settings(**BASE_SETTINGS)


def test_settings_still_parse_with_the_activation_field(monkeypatch):
    """The new field is additive: a valid environment still parses."""
    monkeypatch.delenv("OPIP_PAPER_V2_MODE", raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-webhook-secret")
    assert _settings().opip_paper_v2_mode == PAPER_V2_MODE_OFF
    assert get_settings().opip_paper_v2_mode == PAPER_V2_MODE_OFF


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("active", PAPER_V2_MODE_ACTIVE),
        ("off", PAPER_V2_MODE_OFF),
        # Only the exact canonical value activates. Anything else - including a
        # near-miss that a normalizing parser would have accepted - is off.
        ("Active", PAPER_V2_MODE_OFF),
        ("ACTIVE", PAPER_V2_MODE_OFF),
        (" active ", PAPER_V2_MODE_OFF),
        ("Active ", PAPER_V2_MODE_OFF),
        ("", PAPER_V2_MODE_OFF),
        (None, PAPER_V2_MODE_OFF),
        ("unexpected", PAPER_V2_MODE_OFF),
        (1, PAPER_V2_MODE_OFF),
        (True, PAPER_V2_MODE_OFF),
    ],
)
def test_only_the_exact_canonical_value_activates(raw, expected):
    """Activation parsing is exact and fail-closed, never normalized."""
    holder = SimpleNamespace(opip_paper_v2_mode=raw)
    assert resolve_paper_v2_mode(holder) == expected
    assert paper_v2_active(holder) is (expected == PAPER_V2_MODE_ACTIVE)


def test_a_missing_activation_field_is_off():
    assert resolve_paper_v2_mode(SimpleNamespace()) == PAPER_V2_MODE_OFF


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)
    return roots


def test_activation_gate_has_no_funded_or_exchange_surface():
    """Activation cannot place funded/live orders: no such surface is reachable.

    The gate is asserted to import only its own settings accessor, so there is no
    import path from activation to an exchange client, an order-placement API, or
    any live/funded authority.
    """
    roots = _imported_roots(ACTIVATION_MODULE)
    for forbidden in FORBIDDEN_IMPORT_ROOTS:
        assert not any(
            root == forbidden or root.startswith(forbidden + ".")
            for root in roots
        ), f"activation gate must not import {forbidden}"

    # The only application import is the settings accessor, and it is lazy.
    app_imports = {root for root in roots if root.startswith("app.")}
    assert app_imports == {"app.core.config"}


def test_activation_gate_does_not_mention_order_placement():
    """No order-placement vocabulary exists in the gate at all."""
    source = ACTIVATION_MODULE.read_text(encoding="utf-8").lower()
    for token in (
        "place_order",
        "create_order",
        "submit_order",
        "kraken_private",
        "api_secret",
        "api_key",
        "withdraw",
        "transfer",
    ):
        assert token not in source, f"activation gate must not reference {token}"
