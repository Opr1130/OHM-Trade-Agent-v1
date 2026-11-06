"""ATDD — the configured replacement budget must reach the isolated worker.

ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-013.

P2 review finding on PR #505: the bounded replacement budget existed as a
setting, but the isolated ``opip-scan-worker`` service declares its environment
explicitly and inherits no environment file (that is what keeps exchange, AI and
Telegram credentials out of the acquisition container). An operator-configured
budget therefore could never arrive, and the worker always ran with the default
25.

The correction propagates only this one non-secret, bounded acquisition control,
using the same Compose interpolation pattern the other isolated workers already
use: ``KEY: "${KEY:-default}"`` resolved from the owner's ``.env`` (the single
source of truth). ``${VAR:-default}`` substitutes the default only when the
variable is unset or empty, so an explicit ``0`` survives; nothing here depends
on shell or Python truthiness.

These tests assert the production-equivalent Compose configuration, the resolved
container environment (when a Compose CLI is available) and the scanner's
interpretation of the value. No secret value is read, printed or asserted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from app.core import config as app_config
from app.core.config import Settings
from app.scanner import market_scanner
from app.scanner.universe import UniverseAsset, UniverseBuildResult

INCREMENT = "ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1"
APP_ROOT = Path(__file__).resolve().parents[1]
COMPOSE = APP_ROOT / "docker-compose.yml"
WORKER = "opip-scan-worker"
KEY = "OPIP_SCAN_REPLACEMENT_CANDIDATES"
TARGET = 200

#: Bounded Docker interaction. These checks run inside the ordinary suite, so an
#: unavailable or unresponsive Docker client may cost seconds, never minutes.
DOCKER_PROBE_TIMEOUT_SECONDS = 20
COMPOSE_CONFIG_TIMEOUT_SECONDS = 45
_DOCKER_CLI: str | None = None
_DOCKER_PROBED = False

#: Credentials and provider secrets the acquisition container must never receive.
FORBIDDEN_WORKER_ENV = {
    "KRAKEN_API_KEY",
    "KRAKEN_API_SECRET",
    "KRAKEN_PRIVATE_KEY",
    "LIVE_TRADING_KEY",
    "TELEGRAM_BOT_TOKEN",
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "STRATEGY_ADMIN_SECRET",
    "KRKN_API_KEY",
}


def _settings(**overrides) -> Settings:
    values = {"webhook_secret": "test-operator-credential"}  # gitleaks:allow - fixture
    values.update(overrides)
    return Settings(**values)


def _worker_service() -> dict:
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    return services[WORKER]


def _docker_compose() -> str | None:
    """Return the docker CLI path when a Compose v2 subcommand is available.

    Probed once per session and bounded, because this runs inside the ordinary
    test suite: an unavailable or unresponsive Docker client must cost seconds,
    not minutes.
    """
    global _DOCKER_CLI, _DOCKER_PROBED
    if _DOCKER_PROBED:
        return _DOCKER_CLI
    _DOCKER_PROBED = True
    docker = shutil.which("docker")
    if docker is None:
        return None
    try:
        probe = subprocess.run(
            [docker, "compose", "version"],
            capture_output=True,
            text=True,
            timeout=DOCKER_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _DOCKER_CLI = None
        return None
    _DOCKER_CLI = docker if probe.returncode == 0 else None
    return _DOCKER_CLI


def _resolved_worker_env(docker: str, project: Path, overrides: dict[str, str]) -> dict[str, str] | None:
    """Resolve a copy of the production Compose file and read the worker environment.

    The fixture is a self-contained project directory: the real Compose document
    plus a minimal stand-in for the controller-generated ``.env.core`` that the
    core service references (resolution requires the file to exist; the worker
    never reads it). Nothing in the repository is written, and only non-secret
    strings are asserted. A bounded timeout returns None so an unresponsive CLI
    cannot stall the suite.
    """
    try:
        completed = subprocess.run(
            [
                docker,
                "compose",
                "--project-directory",
                str(project),
                "-f",
                str(project / "docker-compose.yml"),
                "--profile",
                "scanner",
                "config",
                "--format",
                "json",
            ],
            capture_output=True,
            text=True,
            timeout=COMPOSE_CONFIG_TIMEOUT_SECONDS,
            check=False,
            env=overrides,
            cwd=str(project),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    assert completed.returncode == 0, (
        "compose could not resolve the service configuration: "
        + (completed.stderr or completed.stdout).strip().splitlines()[0][:200]
    )
    payload = json.loads(completed.stdout)
    return _normalised_env(payload["services"][WORKER])


def _normalised_env(service: dict) -> dict[str, str]:
    """Return a service's resolved environment as a plain mapping.

    Compose emits either a mapping or a ``KEY=value`` list depending on version;
    both mean the same resolved container environment.
    """
    environment = service.get("environment")
    if environment is None:
        return {}
    if isinstance(environment, dict):
        return {str(key): "" if value is None else str(value) for key, value in environment.items()}
    resolved: dict[str, str] = {}
    for entry in environment:
        key, _, value = str(entry).partition("=")
        resolved[key] = value
    return resolved


def _compose_project(tmp_path: Path) -> Path:
    project = tmp_path / "compose-project"
    project.mkdir()
    (project / "docker-compose.yml").write_text(
        COMPOSE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (project / ".env.core").write_text("OPIP_RELEASE_PROFILE=SAFE_BASELINE\n", encoding="utf-8")
    return project


def _compose_env(overrides: dict[str, str]) -> dict[str, str]:
    """A minimal, secret-free process environment for Compose interpolation."""
    inherited = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(
            ("OPIP_", "KRAKEN_", "KRKN_", "TELEGRAM_", "OPENAI_", "DEEPSEEK_", "STRATEGY_")
        )
    }
    inherited.update(overrides)
    return inherited


# --- resolved-environment parsing (both Compose output shapes) --------------


def test_resolved_environment_parsing_handles_both_compose_shapes():
    """Either Compose output shape describes the same resolved container environment."""
    mapping = _normalised_env({"environment": {KEY: "0", "OPIP_SCAN_EXECUTION_MODE": "isolated"}})
    listing = _normalised_env(
        {"environment": [f"{KEY}=0", "OPIP_SCAN_EXECUTION_MODE=isolated"]}
    )

    assert mapping == listing == {KEY: "0", "OPIP_SCAN_EXECUTION_MODE": "isolated"}
    assert _normalised_env({}) == {}


# --- the declared, production-equivalent Compose configuration --------------


@pytest.mark.acceptance(f"{INCREMENT}/AC-013")
def test_worker_service_declares_the_bounded_replacement_budget():
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-013 the worker declares the budget it uses."""
    worker = _worker_service()
    environment = worker["environment"]

    assert KEY in environment, (
        "the isolated worker declares its environment explicitly and inherits no "
        "environment file, so the budget must be mapped here"
    )
    declared = environment[KEY]
    # Compose semantics: `${VAR:-default}` keeps an explicit value (including 0)
    # and only substitutes when the variable is unset or empty.
    assert declared == "${OPIP_SCAN_REPLACEMENT_CANDIDATES:-25}"
    assert ":-" in declared

    # The interpolation default and the application default cannot drift apart.
    default = _settings().opip_scan_replacement_candidates
    assert declared.endswith(f":-{default}}}")
    assert (default, market_scanner.DEFAULT_REPLACEMENT_CANDIDATES) == (25, 25)
    assert market_scanner.MAX_REPLACEMENT_CANDIDATES == 100

    # Isolation is unchanged by the mapping.
    assert "env_file" not in worker
    assert worker["read_only"] is True
    assert not set(environment) & FORBIDDEN_WORKER_ENV


@pytest.mark.acceptance(f"{INCREMENT}/AC-013")
def test_resolved_worker_environment_carries_the_configured_value(tmp_path):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-013 Compose resolves the configured budget for the worker.

    One resolved invocation proves the mapping (using ``0``, the value most at
    risk of being swallowed by a default). The remaining values are covered by
    the declaration test and by the scanner-level budget test, so the suite never
    pays for a Compose start-up per value.
    """
    docker = _docker_compose()
    if docker is None:
        pytest.skip("no Compose CLI on this host; the declaration test still runs everywhere")

    resolved = _resolved_worker_env(
        docker, _compose_project(tmp_path), _compose_env({KEY: "0"})
    )
    if resolved is None:
        pytest.skip("Compose did not resolve within the bounded timeout")

    assert resolved[KEY] == "0"
    assert not set(resolved) & FORBIDDEN_WORKER_ENV


@pytest.mark.acceptance(f"{INCREMENT}/AC-013")
def test_resolved_worker_environment_defaults_when_the_budget_is_unset(tmp_path):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-013 an unset budget resolves to the documented default."""
    docker = _docker_compose()
    if docker is None:
        pytest.skip("no Compose CLI on this host; the declaration test still runs everywhere")

    resolved = _resolved_worker_env(docker, _compose_project(tmp_path), _compose_env({}))
    if resolved is None:
        pytest.skip("Compose did not resolve within the bounded timeout")

    assert resolved[KEY] == "25"
    assert not set(resolved) & FORBIDDEN_WORKER_ENV


# --- how the scanner interprets the value it is given -----------------------


def _asset(base: str, rank: int) -> UniverseAsset:
    notional = 1_000.0 - rank
    return UniverseAsset(
        base_asset=base,
        usd_pair=f"{base}USD",
        primary_pair=f"{base}USD",
        primary_quote_currency="USD",
        primary_kraken_symbol=f"{base}/USD",
        usd_24h_notional_usd=notional,
        combined_24h_notional_usd=notional,
        liquidity_rank=rank,
    )


def _snapshot(symbol: str, item: UniverseAsset | None) -> "object":
    from app.scanner.models import MarketSnapshot

    base = item.base_asset if item is not None else symbol.removesuffix("USD")
    return MarketSnapshot(
        symbol=symbol,
        last_price=100.0,
        ema20=99.0,
        ema50=98.0,
        ema200=90.0,
        rsi=55.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.5,
        technical_score=80,
        trend="UP",
        underlying_asset=base,
        primary_pair=symbol,
        primary_quote_currency="USD",
        kraken_public_symbol=f"{base}/USD",
    )


def _install(monkeypatch, universe, outcomes, *, settings=None):
    calls: list[str] = []
    table = dict(outcomes)
    configured = settings if settings is not None else _settings()
    monkeypatch.setattr(app_config, "get_settings", lambda: configured)
    monkeypatch.setattr(market_scanner, "KrakenClient", lambda: object())
    monkeypatch.setattr(
        market_scanner, "build_kraken_asset_universe", lambda client, limit: universe
    )

    def fake_analyze(symbol, universe_asset=None):
        calls.append(symbol)
        status, reason = table.get(symbol, ("ok", None))
        if status != "ok":
            return status, None, reason
        return "ok", _snapshot(symbol, universe_asset), None

    monkeypatch.setattr(market_scanner, "analyze_symbol", fake_analyze)
    return calls


def _universe(selected, pool):
    return UniverseBuildResult(
        assets=list(selected),
        eligible_usd_markets=len(selected) + len(pool),
        eligible_usdt_markets=0,
        unique_underlying_assets=len(selected) + len(pool),
        selected_liquid_assets=len(selected),
        usdt_usd_rate=1.0,
        warnings=[],
        ticker_batch_requests=1,
        ranked_assets=list(selected) + list(pool),
    )


@pytest.mark.acceptance(f"{INCREMENT}/AC-013")
@pytest.mark.parametrize(
    "configured,expected", [(0, 0), (1, 1), (25, 25), (100, 30)]
)
def test_scanner_enforces_the_configured_budget(monkeypatch, configured, expected):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-013 the scanner spends exactly the configured budget."""
    selected = [_asset(f"A{index:03d}", index) for index in range(1, TARGET + 1)]
    selected[48] = _asset("NEW", 49)
    pool = [_asset(f"R{index:03d}", TARGET + index) for index in range(1, 31)]
    outcomes = {"NEWUSD": ("skip", "NEWUSD: insufficient history (39 candles)")}
    for index in range(1, 31):
        outcomes[f"R{index:03d}USD"] = (
            "skip",
            f"R{index:03d}USD: insufficient history (9 candles)",
        )
    calls = _install(
        monkeypatch,
        _universe(selected, pool),
        outcomes,
        settings=_settings(opip_scan_replacement_candidates=configured),
    )

    result = market_scanner.scan_market(limit=TARGET)

    budget = min(configured, len(pool))
    assert result.replacement_attempts == budget
    assert result.attempted == TARGET + budget
    assert len(calls) == result.attempted
    assert result.analyzed == TARGET - 1


@pytest.mark.acceptance(f"{INCREMENT}/AC-013")
def test_explicit_zero_disables_replacement(monkeypatch):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-013 an explicit 0 is honoured, not defaulted to 25."""
    selected = [_asset(f"A{index:03d}", index) for index in range(1, TARGET + 1)]
    selected[48] = _asset("NEW", 49)
    pool = [_asset("R001", TARGET + 1)]
    calls = _install(
        monkeypatch,
        _universe(selected, pool),
        {"NEWUSD": ("skip", "NEWUSD: insufficient history (39 candles)")},
        settings=_settings(opip_scan_replacement_candidates=0),
    )

    result = market_scanner.scan_market(limit=TARGET)

    assert result.replacement_attempts == 0, "an explicit 0 must not fall back to the default"
    assert result.analyzed == TARGET - 1
    assert "R001USD" not in calls


@pytest.mark.acceptance(f"{INCREMENT}/AC-013")
@pytest.mark.parametrize("invalid", [-1, 101, "not-a-number"])
def test_invalid_budget_is_rejected_by_validation(invalid):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-013 an out-of-range budget never becomes 25."""
    with pytest.raises(ValidationError):
        _settings(opip_scan_replacement_candidates=invalid)


@pytest.mark.acceptance(f"{INCREMENT}/AC-013")
def test_invalid_budget_prevents_settings_from_resolving(monkeypatch):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-013 an invalid budget stops the process before acquisition."""
    # The same resolution the worker performs on entry (environment source ->
    # Settings validation), asserted without touching the process-wide settings
    # cache: an invalid value is rejected, never silently coerced to the default.
    monkeypatch.setenv(KEY, "101")

    with pytest.raises(ValidationError):
        _settings()


@pytest.mark.acceptance(f"{INCREMENT}/AC-013")
def test_worker_never_starts_acquisition_with_an_invalid_budget(monkeypatch):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-013 the worker fails closed, it does not scan with 25."""
    from app.jobs import scan_worker

    def _reject():
        # The real Settings validator raises this for an invalid configured value.
        return _settings(opip_scan_replacement_candidates=101)

    monkeypatch.setattr(scan_worker, "get_settings", _reject)

    with pytest.raises(ValidationError):
        scan_worker.run(attempt_id="SCAN:BUDGET")


@pytest.mark.acceptance(f"{INCREMENT}/AC-013")
def test_missing_setting_uses_the_documented_default(monkeypatch):
    """ATDD-OPIP-P1-SCANNER-COVERAGE-RECOVERY-v1/AC-013 absent configuration means the documented 25."""
    assert _settings().opip_scan_replacement_candidates == 25

    # A settings object that predates the field still resolves to the documented
    # default rather than raising inside acquisition.
    monkeypatch.setattr(app_config, "get_settings", lambda: SimpleNamespace())
    assert market_scanner.configured_replacement_candidates() == 25
