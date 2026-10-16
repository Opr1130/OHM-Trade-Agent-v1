"""B1 RED: the admitted-execution path must carry the committed decision context."""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.services.paper_v2_execution import run_paper_v2_opportunity  # noqa: E402

ADMISSION_MODULE = APP_ROOT / "app" / "services" / "paper_v2_capital_admission_execution.py"


def test_b1_producer_accepts_a_committed_context_identity() -> None:
    """I1: the producer must be able to receive the already-committed context id."""

    params = set(inspect.signature(run_paper_v2_opportunity).parameters)
    carrier = {
        name
        for name in params
        if "decision_context" in name or "committed_admission" in name or name == "admission"
    }
    assert carrier, (
        "the producer cannot carry the committed decision context; it therefore "
        "commits its own context and cannot reuse the admitted identity "
        f"(parameters={sorted(params)})"
    )


def test_b1_dispatch_passes_the_committed_context_identity() -> None:
    """I1/I2: the admission dispatch must hand the committed identity to the producer."""

    tree = ast.parse(ADMISSION_MODULE.read_text(encoding="utf-8"))
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "run_paper_v2_opportunity":
                calls.append(node)
    assert calls, "the admission module no longer dispatches to the producer producer"
    keywords = {kw.arg for call in calls for kw in call.keywords if kw.arg}
    assert any("decision_context" in kw or kw in {"admission", "committed_admission"} for kw in keywords), (
        "the dispatch omits the committed decision context, so an already-admitted "
        f"opportunity causes a second context commit and a second admission (keywords={sorted(keywords)})"
    )
