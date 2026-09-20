"""B/C-4 Cockpit v1 UI structural tests.

These assert the properties of the served page that must not regress: it reuses the
already-approved O'Pip mascot byte-for-byte, it states PAPER MODE, it offers no
write or trading control, and it performs no P&L / drawdown arithmetic of its own.

The last point is the one that matters most architecturally. A presentation layer
that recomputes economics becomes a second source of P&L truth, which the B/C-4
contract forbids. These tests pin that the page only formats what the projection
supplies.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.api import cockpit

REPO = Path(__file__).resolve().parents[1]
DASHBOARD_HTML = REPO / "app" / "api" / "dashboard.html"
COCKPIT_HTML = REPO / "app" / "api" / "cockpit.html"

_MASCOT_RE = re.compile(r'src="(data:image/[a-z]+;base64,[A-Za-z0-9+/=]+)"')


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _script_section(html: str) -> str:
    match = re.search(r"<script>(.*?)</script>", html, re.DOTALL)
    assert match, "cockpit page has no script section"
    return match.group(1)


def test_cockpit_page_route_is_registered():
    methods = cockpit.route_methods()
    assert "/cockpit" in methods
    assert methods["/cockpit"] == {"GET"}


def test_cockpit_page_is_served_from_the_module_directory():
    assert cockpit.COCKPIT_FILE == COCKPIT_HTML
    assert COCKPIT_HTML.is_file()


def test_cockpit_page_is_served_as_a_static_file():
    """The page must be streamed as a fixed asset, not returned as computed HTML.

    Returning file text through an ``HTMLResponse`` is a reflected-XSS pattern when
    the text could contain input; streaming a constant asset keeps request data
    structurally unable to reach the response body.
    """
    from fastapi.responses import FileResponse

    response = cockpit.cockpit_page()
    assert isinstance(response, FileResponse)
    assert Path(response.path) == COCKPIT_HTML
    assert response.media_type == "text/html"


def test_cockpit_page_route_does_not_declare_an_html_response_class():
    """No route may declare ``HTMLResponse`` or a ``response_class``.

    Checked via the AST so the docstring that *explains* why this pattern is
    avoided is not mistaken for the pattern itself.
    """
    import ast

    tree = ast.parse(
        __import__("pathlib").Path(cockpit.__file__).read_text(encoding="utf-8")
    )

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert "HTMLResponse" not in imported

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            for keyword in decorator.keywords:
                assert keyword.arg != "response_class", (
                    f"{node.name} declares response_class={keyword.arg}"
                )


def test_cockpit_reuses_the_approved_mascot_byte_for_byte():
    """The approved asset must be reused exactly - no re-drawn or new logo."""
    approved = _MASCOT_RE.search(_read(DASHBOARD_HTML))
    assert approved, "existing dashboard no longer carries the approved mascot"

    served = _read(COCKPIT_HTML)
    assert approved.group(1) in served, (
        "cockpit does not reuse the approved O'Pip mascot bytes"
    )
    assert "approved bird mascot" in served
    assert "__MASCOT_DATA_URI__" not in served, "mascot placeholder was left unsubstituted"


def test_page_states_paper_mode_prominently():
    served = _read(COCKPIT_HTML)
    assert "PAPER MODE" in served
    assert "NO LIVE ORDERS" in served


def test_page_declares_read_only_and_no_funded_authority():
    served = _read(COCKPIT_HTML)
    assert "Read-only" in served or "read-only" in served
    lowered = served.lower()
    for forbidden in ("place order", "cancel order", "close position", "promote strategy"):
        assert forbidden not in lowered, f"cockpit page offers {forbidden!r}"


def test_page_issues_only_get_requests():
    """Every request the page can make must be a GET."""
    script = _script_section(_read(COCKPIT_HTML))
    for token in ("method:'POST'", 'method: "POST"', "method:'PUT'", "method:'DELETE'"):
        assert token not in script, f"cockpit page issues a mutating request: {token}"


def test_page_targets_only_cockpit_read_endpoints():
    """Every request target must be a cockpit read endpoint."""
    script = _script_section(_read(COCKPIT_HTML))
    literal_targets = set(re.findall(r'request\("([^"]+)"', script))
    # The trade-detail view builds its path from an id, so record it explicitly.
    if '/api/cockpit/trades/" + encodeURIComponent' in script:
        literal_targets.add("/api/cockpit/trades/{paper_trade_id}")
    assert literal_targets, "cockpit page performs no requests"
    for target in literal_targets:
        assert target.startswith("/api/cockpit/"), f"unexpected request target {target}"


def test_page_does_not_recompute_pnl_or_drawdown():
    """No arithmetic may be applied to economics in the browser.

    This is the guard against a second P&L truth. Formatting (rounding, currency
    suffix) is allowed; arithmetic on economic fields is not.
    """
    script = _script_section(_read(COCKPIT_HTML))

    economic_fields = (
        "net_pnl",
        "gross_pnl",
        "execution_costs",
        "realized_net_pnl",
        "expectancy_quote_currency",
        "cumulative_net_pnl",
        "drawdown_quote_currency",
        "realized_gross_pnl",
    )
    for field in economic_fields:
        for pattern in (
            rf"{field}\s*[+\-*/]",
            rf"[+\-*/]\s*{field}",
            rf"{field}\s*\*",
        ):
            assert not re.search(pattern, script), (
                f"cockpit page performs arithmetic on {field!r}: "
                f"presentation must not recompute economics"
            )


def test_page_does_not_derive_drawdown_from_the_equity_series():
    """Drawdown arrives computed; the page must not re-derive it."""
    script = _script_section(_read(COCKPIT_HTML))
    for pattern in (
        r"peak\s*-\s*",
        r"-\s*peak",
        r"cumulative_net_pnl\s*-\s*",
        r"-\s*cumulative_net_pnl",
    ):
        assert not re.search(pattern, script), (
            f"cockpit page re-derives drawdown: {pattern!r}"
        )


def test_page_renders_unknown_explicitly():
    """UNKNOWN must be a first-class rendered state, never blank or zero."""
    served = _read(COCKPIT_HTML)
    assert "UNKNOWN" in served
    assert "marked" in served.lower()


def test_page_has_no_secret_persistence():
    """The page must not store credentials."""
    script = _script_section(_read(COCKPIT_HTML))
    for forbidden in ("localStorage", "sessionStorage", "document.cookie"):
        assert forbidden not in script, f"cockpit page persists state via {forbidden}"


def test_page_does_not_hardcode_illustrative_numbers():
    """No fixture/sample values may be presented as real economics."""
    served = _read(COCKPIT_HTML)
    lowered = served.lower()
    for marker in ("sample data", "demo data", "fixture", "lorem", "placeholder value"):
        assert marker not in lowered, f"cockpit page mentions {marker!r}"


@pytest.mark.parametrize("token", ["--good", "--bad", "--warn"])
def test_page_reuses_existing_design_tokens(token: str):
    """Cockpit v1 must follow the existing dashboard's dark design language."""
    served = _read(COCKPIT_HTML)
    dashboard = _read(DASHBOARD_HTML)
    assert token in dashboard
    assert token in served
