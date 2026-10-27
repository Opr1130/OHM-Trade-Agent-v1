"""ATDD-RELEASE-TREE-DEPLOY-v1/AC-012 - core environment credential isolation.

The core container must never inherit a funded exchange credential. The deploy
controller therefore derives `.env.core` from the owner's `.env`, removing every
name in ``release_profiles.FUNDED_CREDENTIAL_KEYS``; the core service consumes
only that filtered file. The owner's `.env` is never modified, no value is ever
printed, and a missing or malformed source fails closed.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

APP = pathlib.Path(__file__).resolve().parents[1]
LIBRARY = APP / "deploy" / "remote" / "lib" / "opip_release_tree.sh"
DEPLOY = APP / "deploy" / "remote" / "ohm-deploy"
COMPOSE = APP / "docker-compose.yml"
DOCKERIGNORE = APP / ".dockerignore"
BASH = shutil.which("bash")

pytestmark = [
    pytest.mark.acceptance,
    pytest.mark.skipif(BASH is None, reason="bash is required"),
]

ROTATED = "rotated-secret-value-9f2c"


def _library_keys() -> tuple[str, ...]:
    text = LIBRARY.read_text(encoding="utf-8")
    match = re.search(r'^OPIP_FUNDED_CREDENTIAL_KEYS="([^"]*)"$', text, re.MULTILINE)
    assert match, "funded credential name literal is missing from the provider"
    return tuple(match.group(1).split())


def _run(app_root: pathlib.Path, state: pathlib.Path):
    script = f"""
set -Eeuo pipefail
APP_ROOT={app_root.as_posix()}
STATE_DIR={state.as_posix()}
source {LIBRARY.as_posix()}
opip_write_core_env
"""
    return subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, check=False
    )


@pytest.fixture()
def sandbox(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    app_root = tmp_path / "app"
    app_root.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (app_root / ".env").write_text(
        "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\n"
        "OPIP_FEATURE_BUS_MODE=shadow\n"
        "KRAKEN_API_KEY=fake-key-value\n"
        "KRAKEN_API_SECRET=fake-secret-value\n"
        "KRKN_API_KEY=fake-legacy-value\n"
        "KRAKEN_RECONCILIATION_ENABLED=false\n"
        "OPIP_TRADEVISOR_WEBHOOK_TOKEN=token-value\n",
        encoding="utf-8",
    )
    return app_root, state


def test_funded_credential_names_match_the_python_authority() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: the controller filter and the
    release-profile authority list exactly the same funded credential names."""
    from app.services.release_profiles import FUNDED_CREDENTIAL_KEYS

    assert _library_keys() == tuple(FUNDED_CREDENTIAL_KEYS)


def test_generator_removes_funded_names_and_keeps_the_rest(sandbox) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: only funded names are removed."""
    app_root, state = sandbox
    source_before = (app_root / ".env").read_bytes()

    result = _run(app_root, state)
    assert result.returncode == 0, result.stderr

    dest = app_root / ".env.core"
    assert dest.is_file()
    text = dest.read_text(encoding="utf-8")
    for key in _library_keys():
        assert key not in text
    assert "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW" in text
    assert "KRAKEN_RECONCILIATION_ENABLED=false" in text
    assert "OPIP_TRADEVISOR_WEBHOOK_TOKEN=token-value" in text

    assert (app_root / ".env").read_bytes() == source_before
    assert oct(dest.stat().st_mode & 0o777) == "0o600"
    assert "fake-key-value" not in result.stdout + result.stderr
    assert "fake-secret-value" not in result.stdout + result.stderr
    assert f"excluded_count={len(_library_keys())}" in result.stdout


def test_generator_is_idempotent_and_follows_rotation(sandbox) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: regeneration is stable, and a rotated
    source produces a new filtered file."""
    app_root, state = sandbox
    dest = app_root / ".env.core"

    assert _run(app_root, state).returncode == 0
    first = dest.read_bytes()
    assert _run(app_root, state).returncode == 0
    assert dest.read_bytes() == first

    source = app_root / ".env"
    source.write_text(
        source.read_text(encoding="utf-8")
        .replace("fake-key-value", ROTATED)
        .replace(
            "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW",
            "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\nOPIP_MARKER=after-rotation",
        ),
        encoding="utf-8",
    )
    assert _run(app_root, state).returncode == 0
    rotated = dest.read_text(encoding="utf-8")
    assert ROTATED not in rotated
    assert "OPIP_MARKER=after-rotation" in rotated
    assert rotated != first.decode("utf-8")



def test_missing_or_symlinked_source_fails_closed(tmp_path: pathlib.Path) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: no source, or a symlinked source,
    refuses and never leaves a filtered file behind."""
    app_root = tmp_path / "app"
    app_root.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    dest = app_root / ".env.core"

    missing = _run(app_root, state)
    assert missing.returncode != 0
    assert "missing, unreadable or a symlink" in missing.stderr
    assert not dest.exists()

    real = tmp_path / "real.env"
    real.write_text(
        "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\nKRAKEN_API_KEY=x\n", encoding="utf-8"
    )
    (app_root / ".env").symlink_to(real)
    linked = _run(app_root, state)
    assert linked.returncode != 0
    assert not dest.exists()


def test_core_compose_consumes_only_the_filtered_environment() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: the core service reads the filtered
    file, and that file can never enter the image build context."""
    compose = COMPOSE.read_text(encoding="utf-8")
    core = compose[compose.index("  ohm-trade-agent:"):compose.index("  opip-canonical-writer:")]
    assert "env_file:" in core
    assert "- .env.core" in core
    assert "- .env\n" not in core
    dockerignore = DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
    assert ".env.*" in dockerignore


def test_controller_generates_before_compose_and_on_rollback() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: generation runs after activation and
    before the first Compose call, and again on the rollback path."""
    deploy = DEPLOY.read_text(encoding="utf-8")
    deploy_call = deploy.index("opip_write_core_env || {")
    build = deploy.index('docker compose build --build-arg "OPIP_RELEASE_SHA=$TARGET_SHA"')
    assert deploy_call < build
    assert "refusing deploy: the filtered core environment could not be generated" in deploy
    rollback_call = deploy.index("opip_write_core_env \\")
    rollback_note = deploy.index("rollback: filtered core environment could not be regenerated")
    assert rollback_call < rollback_note
    assert rollback_note < deploy_call


def test_positions_fails_safe_when_the_credential_is_absent(monkeypatch) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-012: with the credential isolated, the
    operator view degrades to a bounded message and never invents data."""
    from app.exchanges.kraken_private import KrakenPrivateClient
    from app.services import telegram_command_center

    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    assert KrakenPrivateClient().enabled is False

    message = telegram_command_center.positions_report()
    assert message == "⚠️ POSITIONS — Kraken private account data is temporarily unavailable."
    assert "fake-key-value" not in message
    assert telegram_command_center.orders_report().startswith("⚠️ ORDERS")
