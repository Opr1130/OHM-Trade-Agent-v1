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


def test_no_network_or_websocket_imports():
    banned_modules = {"websockets", "aiohttp", "socket", "requests", "httpx"}
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
                assert top not in banned_modules, f"{path} imports networking module {name}"


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


def test_no_docker_or_worker_service_files_added():
    forbidden_names = {"worker.py", "service.py", "binance_client.py", "bybit_client.py"}
    present = {path.name for path in _all_py_files()}
    assert not (present & forbidden_names), present & forbidden_names


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
