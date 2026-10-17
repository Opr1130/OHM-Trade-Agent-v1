"""C1 RED/GREEN (review evidence): the F11 caller census must detect every
statically identifiable mutator invocation form, not only direct/qualified calls.

Covers: direct calls, qualified module calls, imported names, aliased module
imports, aliased imported names, scheduled-job callbacks, same-name negative
controls, and explicitly-unsupported dynamic resolution.
"""

from __future__ import annotations

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.test_opip_f11_precutover_protection import _source_calls_tokens  # noqa: E402

TOKENS = frozenset({"admit_paper_opportunity", "trigger_paper_protection_action"})


def _detects(source: str) -> bool:
    return _source_calls_tokens(source, TOKENS)


def test_c1_direct_call_is_detected():
    assert _detects("admit_paper_opportunity(req)\n")


def test_c1_qualified_module_call_is_detected():
    assert _detects("writer.admit_paper_opportunity(req)\n")


def test_c1_imported_name_call_is_detected():
    assert _detects(
        "from app.x import admit_paper_opportunity\nadmit_paper_opportunity(req)\n"
    )


def test_c1_aliased_module_call_is_detected():
    assert _detects("import app.x as w\nw.admit_paper_opportunity(req)\n")


def test_c1_aliased_imported_name_call_is_detected():
    assert _detects(
        "from app.x import admit_paper_opportunity as admit\nadmit(req)\n"
    )


def test_c1_scheduled_job_callback_is_detected():
    assert _detects("scheduler.add_job(admit_paper_opportunity, 'x')\n")
    assert _detects("scheduler.add_job(func=trigger_paper_protection_action)\n")


def test_c1_same_name_negative_controls_are_not_detected():
    # An unrelated name and a superstring of a token must not be flagged.
    assert not _detects("close_trade(req)\n")
    assert not _detects("admit_paper_opportunity_extra(req)\n")


def test_c1_unsupported_dynamic_resolution_is_not_detected():
    # Dynamically resolved calls are outside the static census.
    assert not _detects("getattr(obj, 'admit_paper_opportunity')(req)\n")
    assert not _detects("fn = getattr(obj, name)\nfn(req)\n")
