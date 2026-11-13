"""Focused acceptance of the independent UI-only configuration boundary."""

from pathlib import Path

import pytest

INCREMENT = "ATDD-OPIP-UI-CONFIG-ISOLATION-M01"
SOURCE = (Path(__file__).resolve().parents[1] / "control-center-v2/src/main.jsx")


@pytest.mark.acceptance(f"{INCREMENT}/AC-001")
def test_legacy_failure_keeps_registry_and_safe_retry():
    """ATDD-OPIP-UI-CONFIG-ISOLATION-M01/AC-001: No legacy failure hides the separate registry."""
    source = SOURCE.read_text(encoding="utf-8")
    unavailable = source.split("if(availability!=='READY')return", 1)[1].split(
        'return <div className="stack">', 1
    )[0]
    assert "<RegistryConsole config={config}" in unavailable
    assert "Retry legacy administration" in unavailable
    assert 'type="password"' in unavailable
    assert "onClick={()=>activate(" not in unavailable
    assert "staging is inert" in unavailable


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_async_domain_and_rejected_stage_preserve_work():
    """ATDD-OPIP-UI-CONFIG-ISOLATION-M01/AC-002: Async domains and failed stage preserve edits."""
    source = SOURCE.read_text(encoding="utf-8")
    registry = source.split("function RegistryConsole(", 1)[1].split(
        "function ProvenanceNote(", 1
    )[0]
    assert "setDomain(current=>domains.some(item=>item.domain===current)?current:" in registry
    assert "[initialDomain,config?.domains]" in registry
    assert "setDomain(item.domain);setValues({});setErrors({});setPreview(null)" in registry
    assert "if(outcome.state==='STAGED'){" in registry
    assert "setValues({});setErrors({});setPreview(null)" in registry
