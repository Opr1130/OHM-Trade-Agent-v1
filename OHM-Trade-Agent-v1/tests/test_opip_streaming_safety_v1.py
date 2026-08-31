"""BUILD 4.1 — architectural safety tests for app/opip/streaming.

Sequence 4 is market evidence infrastructure. It must never gain exchange
execution capability, must not be wired into Sequence 3 or the Decision
Engine, and its deterministic modules must never read the wall clock.
"""

from __future__ import annotations

import ast
from pathlib import Path

STREAMING_PACKAGE = Path("app/opip/streaming")


def _all_py_files() -> list[Path]:
    return sorted(STREAMING_PACKAGE.rglob("*.py"))


def test_streaming_package_exists_and_is_nonempty():
    files = _all_py_files()
    assert files, "app/opip/streaming contains no modules"


def test_no_exchange_or_execution_imports_anywhere_in_streaming():
    banned_prefixes = ("app.exchanges",)
    banned_modules = {
        "app.services.confirm_entry",
        "app.services.register_trade",
        "app.services.order_intent_registry",
        "app.services.kraken_transport",
        "app.services.trade_cli",
        "app.services.active_trade_registry",
        "app.services.pending_setup_registry",
        "app.services.paper_trade_registry",
    }
    for path in _all_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not name.startswith(banned_prefixes), (
                    f"{path} imports exchange module {name}"
                )
                assert name not in banned_modules, (
                    f"{path} imports a trading/order/lifecycle module {name}"
                )


def test_no_order_placement_identifiers_anywhere_in_streaming():
    forbidden = (
        "place_order",
        "cancel_order",
        "modify_order",
        "confirm_entry",
        "close_trade",
        "add_trade",
        "kraken_transport",
    )
    for path in _all_py_files():
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path} references order-authority token {token}"


def test_no_ml_dependency_imports():
    banned_modules = {
        "pandas",
        "numpy",
        "sklearn",
        "torch",
        "tensorflow",
        "openai",
        "anthropic",
    }
    for path in _all_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                assert top not in banned_modules, f"{path} imports banned dependency {name}"


def test_network_imports_are_limited_to_public_provider_adapters():
    network_modules = {"websockets", "aiohttp", "socket", "requests", "httpx"}
    allowed = {"binance.py", "bybit.py"}
    for path in _all_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top in network_modules:
                    assert path.name in allowed, (
                        f"{path} imports networking module {name}; only public "
                        "provider adapters may own transport"
                    )
                    assert top == "websockets", (
                        f"{path} uses unapproved network dependency {name}"
                    )


def test_no_telegram_or_notification_identifiers():
    forbidden = ("telegram", "send_message", "notifier", "chief_alert", "emergency_alert")
    for path in _all_py_files():
        source = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in source, f"{path} references notification token {token}"


def test_no_sequence3_or_decision_engine_consumption():
    banned_modules = {
        "app.opip.risk.observer",
        "app.opip.risk.notifier",
        "app.opip.risk.alert_state",
        "app.opip.decision.engine",
    }
    for path in _all_py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert name not in banned_modules, (
                    f"{path} imports {name}, coupling Sequence 4 evidence to a "
                    "decision/protection authority"
                )


def test_no_wall_clock_reads_in_deterministic_modules():
    deterministic_modules = (
        "contract.py",
        "envelope.py",
        "sequencing.py",
        "windows.py",
        "quality.py",
        "features.py",
    )
    forbidden = ("datetime.now(", "utcnow(", "time.time(")
    for name in deterministic_modules:
        source = (STREAMING_PACKAGE / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{name} reads the wall clock via {token}"


def test_no_unbounded_or_raw_event_list_fields_on_window_accumulator():
    """WindowAccumulator must stay O(1) per window; guard against a future
    edit accidentally reintroducing raw-event buffering."""
    from app.opip.streaming.windows import WindowAccumulator

    field_names = set(WindowAccumulator.__dataclass_fields__)
    banned = {"raw_events", "events", "observations", "trades"}
    assert not (field_names & banned), field_names & banned


def test_only_canonical_worker_entrypoint_is_allowed():
    forbidden_names = {"service.py", "binance_client.py", "bybit_client.py"}
    present = {path.name for path in _all_py_files()}
    assert not (present & forbidden_names), present & forbidden_names
    workers = [path for path in _all_py_files() if path.name == "worker.py"]
    assert [path.as_posix() for path in workers] == [
        "app/opip/streaming/worker.py"
    ]


def test_stream_worker_has_no_exchange_authority_imports():
    path = STREAMING_PACKAGE / "worker.py"
    source = path.read_text(encoding="utf-8").lower()
    forbidden = (
        "app.exchanges",
        "kraken",
        "place_order",
        "cancel_order",
        "modify_order",
        "telegram",
        "app.opip.decision",
        "app.opip.risk",
    )
    for token in forbidden:
        assert token not in source


def test_build42_declares_only_approved_streaming_dependencies():
    requirements = Path("requirements.txt").read_text(encoding="utf-8").lower()
    assert "websockets" in requirements
    assert "orjson" in requirements
    for banned in ("aiohttp", "requests"):
        assert banned not in requirements


def test_no_kraken_private_api_reference():
    for path in _all_py_files():
        source = path.read_text(encoding="utf-8").lower()
        assert "kraken" not in source, f"{path} references Kraken; streaming must stay venue-neutral"


def test_production_compose_stream_worker_is_isolated():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    assert "opip-stream-worker:" in compose
    assert 'container_name: opip-stream-worker' in compose
    assert 'mem_limit: 192m' in compose
    assert 'cpus: "0.35"' in compose
    assert './data:/app/data:ro' in compose
    assert './data/opip/streaming:/app/data/opip/streaming:rw' in compose
    assert './data/opip/streaming:/app/data/opip/streaming:ro' in compose
    worker = compose.split("  opip-stream-worker:", 1)[1]
    assert "\n    ports:" not in worker
    assert "\n    env_file:" not in worker
    assert 'OPIP_STREAMING_ENABLED: "true"' in worker
    assert "app.opip.streaming.worker" in worker


def test_streaming_has_no_private_credentials_or_order_endpoints():
    forbidden = (
        "api_key",
        "api_secret",
        "listenkey",
        "/private",
        "/v5/trade",
        "ws-fapi.binance.com",
    )
    for path in _all_py_files():
        source = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            assert token not in source, f"{path} contains private/trading token {token}"


def test_first_stream_worker_deploy_uses_existing_trusted_reconcile_hook():
    deploy = Path("deploy/remote/ohm-deploy").read_text(encoding="utf-8")
    scheduler = Path("deploy/remote/reconcile-scheduler.sh").read_text(
        encoding="utf-8"
    )
    worker = Path("deploy/remote/reconcile-stream-worker.sh").read_text(
        encoding="utf-8"
    )
    workflow = Path("../.github/workflows/deploy-production.yml").read_text(
        encoding="utf-8"
    )
    assert 'bash "$SCHEDULER_RECONCILE"' in deploy
    assert 'bash "$STREAM_RECONCILE"' in scheduler
    assert "app.opip.streaming.activation_check" in worker
    assert "OPIP stream worker reconciliation: OK" in worker
    assert "O'Pip scheduler reconciliation: OK" in workflow
    assert "O'Pip deployment succeeded" in workflow
    assert "/diagnose-learning" in workflow
    assert "&& grep -q 'OPIP stream worker reconciliation: OK' deploy.log" not in workflow
    assert "--remove-orphans ohm-trade-agent" in deploy
