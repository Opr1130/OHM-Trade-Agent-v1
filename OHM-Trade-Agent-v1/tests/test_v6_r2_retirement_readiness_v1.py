"""Acceptance checks for the O'Pip R2 retirement readiness report (v6 R2 slice).

The report is the measurement half of the R2 retirement decision: for each R2
module it names the importers, the tests and the documents that still refer to
it, so an owner can see what a later retirement would have to touch. It is a
read-only reader and these tests prove both the information and the safety.

The tool is developer tooling at tools/retirement_readiness.py, deliberately
outside the ``app/`` runtime package: the audit's R2 list has to name the shadow
seams, and the R3 isolation guards forbid ``app/`` modules from naming them.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from tools.retirement_readiness import build_report, main

APP_ROOT = Path(__file__).resolve().parents[1]

#: The audit's R2 list: (report name, dotted module, source path), audit order.
EXPECTED_MODULES: tuple[tuple[str, str, str], ...] = (
    ("forecast", "app.opip.forecast", "app/opip/forecast.py"),
    (
        "forecast_evaluation",
        "app.opip.forecast_evaluation",
        "app/opip/forecast_evaluation.py",
    ),
    ("forecast_labels", "app.opip.forecast_labels", "app/opip/forecast_labels.py"),
    (
        "forecast_qualification",
        "app.opip.forecast_qualification",
        "app/opip/forecast_qualification.py",
    ),
    (
        "portfolio_selector",
        "app.opip.portfolio_selector",
        "app/opip/portfolio_selector.py",
    ),
    (
        "portfolio_comparator",
        "app.opip.portfolio_comparator",
        "app/opip/portfolio_comparator.py",
    ),
    (
        "opportunity_lifecycle",
        "app.opip.opportunity_lifecycle",
        "app/opip/opportunity_lifecycle.py",
    ),
    (
        "opportunity_persistence",
        "app.opip.opportunity_persistence",
        "app/opip/opportunity_persistence.py",
    ),
    ("feasibility", "app.opip.feasibility", "app/opip/feasibility.py"),
    (
        "fev_evidence_reader",
        "app.opip.fev_evidence_reader",
        "app/opip/fev_evidence_reader.py",
    ),
    ("backtesting", "app.backtesting", "app/backtesting"),
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _seed_synthetic(root: Path) -> Path:
    """Build a tiny repository with one external importer per reference kind."""

    _write(root / "app" / "opip" / "__init__.py", "")
    _write(root / "app" / "opip" / "forecast.py", "VALUE = 1\n")
    _write(root / "app" / "backtesting" / "__init__.py", "")
    _write(
        root / "app" / "backtesting" / "engine.py",
        "from app.opip import forecast\n\nENGINE = forecast.VALUE\n",
    )
    _write(root / "app" / "consumer.py", "from app.opip import forecast\n")
    _write(root / "tests" / "test_forecast_consumer.py", "import app.opip.forecast\n")
    _write(root / "docs" / "notes.md", "See app.opip.forecast for details.\n")
    # Not a source or doc suffix: it must never be opened or counted.
    _write(root / "config.env", "api_key=not-read\n")
    return root


def _snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    """Record every file's size, mtime and content hash before the tool runs."""

    snapshot: dict[str, tuple[int, int, str]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        stat_result = path.stat()
        snapshot[path.relative_to(root).as_posix()] = (
            int(stat_result.st_size),
            int(stat_result.st_mtime_ns),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return snapshot


def _forbid_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every file or directory mutation raise instead of succeeding."""

    def _forbidden(label: str):
        def _fail(*_args: object, **_kwargs: object) -> None:
            raise AssertionError(f"retirement readiness must not call {label}")

        return _fail

    for name in (
        "chmod",
        "hardlink_to",
        "link_to",
        "mkdir",
        "rename",
        "replace",
        "rmdir",
        "symlink_to",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    ):
        if hasattr(Path, name):
            monkeypatch.setattr(Path, name, _forbidden(f"Path.{name}"))
    for name in (
        "chmod",
        "makedirs",
        "mkdir",
        "remove",
        "removedirs",
        "rename",
        "replace",
        "rmdir",
        "truncate",
        "unlink",
        "utime",
    ):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, _forbidden(f"os.{name}"))
    for name in ("copy", "copy2", "copytree", "move", "rmtree"):
        if hasattr(shutil, name):
            monkeypatch.setattr(shutil, name, _forbidden(f"shutil.{name}"))


@pytest.mark.acceptance
def test_report_lists_every_r2_module_with_importers_tests_and_docs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ATDD-OPIP-V6-R2-RETIREMENT-READINESS-v1/AC-001"""
    report = build_report(APP_ROOT)

    # The report is read-only by construction and walks real source and docs.
    assert report.read_only is True
    assert report.written_files == 0
    assert report.scanned_files > 0

    # Every audit module appears exactly once, with its dotted name and path.
    observed = [(item.name, item.module, item.path) for item in report.modules]
    assert observed == list(EXPECTED_MODULES)

    by_name = {item.name: item for item in report.modules}
    for name, _module, path in EXPECTED_MODULES:
        assert (APP_ROOT / path).exists(), path
        # The audit itself documents every R2 module.
        assert "docs/V6_AUDIT.md" in by_name[name].docs, name
        # A module is never reported as its own importer or test.
        assert path not in by_name[name].importers, name
        assert path not in by_name[name].tests, name

    # Real references are classified: tests are tests, never importers.
    assert "tests/test_opip_r3_f6_forecast_engine.py" in by_name["forecast"].tests
    assert "tests/test_opip_r3_f5_feasibility.py" in by_name["feasibility"].tests
    assert "tests/test_wave2_backtesting.py" in by_name["backtesting"].tests
    assert "tests/test_opip_r3_f6_forecast_engine.py" not in by_name["forecast"].importers

    # Package-internal files never count as external importers of the package.
    for importer in by_name["backtesting"].importers:
        assert not importer.startswith("app/backtesting/"), importer

    # The default text rendering names every module and its reference counts.
    assert main(["--root", str(APP_ROOT)]) == 0
    output = capsys.readouterr().out
    assert "R2 retirement readiness" in output
    assert "nothing is created, changed or deleted" in output
    for name, _module, path in EXPECTED_MODULES:
        assert f"module={name} path={path} importers=" in output, name
    assert f"totals modules={len(EXPECTED_MODULES)}" in output

    # The JSON rendering carries the same report with zero writes recorded.
    assert main(["--root", str(APP_ROOT), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["read_only"] is True
    assert payload["written_files"] == 0
    assert payload["module_count"] == len(EXPECTED_MODULES)
    assert [item["name"] for item in payload["modules"]] == [
        name for name, _module, _path in EXPECTED_MODULES
    ]

    # A repository that does import these modules proves the importer column.
    synthetic_root = _seed_synthetic(tmp_path / "repo")
    synthetic_report = build_report(synthetic_root)
    synthetic = {item.name: item for item in synthetic_report.modules}
    assert synthetic["forecast"].importers == (
        "app/backtesting/engine.py",
        "app/consumer.py",
    )
    assert synthetic["forecast"].tests == ("tests/test_forecast_consumer.py",)
    assert synthetic["forecast"].docs == ("docs/notes.md",)
    # The package's own files are not its external importers.
    assert synthetic["backtesting"].importers == ()
    # Six Python files plus one doc are read; the non-source file is not.
    assert synthetic_report.scanned_files == 7


@pytest.mark.acceptance
def test_report_never_creates_changes_or_removes_a_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """ATDD-OPIP-V6-R2-RETIREMENT-READINESS-v1/AC-002"""
    root = _seed_synthetic(tmp_path / "repo")
    before = _snapshot(root)

    _forbid_mutation(monkeypatch)

    # Text, JSON and an invalid root all run with every mutation path blocked.
    assert main(["--root", str(root)]) == 0
    capsys.readouterr()
    assert main(["--root", str(root), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["read_only"] is True
    assert payload["written_files"] == 0

    missing = tmp_path / "absent-root"
    assert main(["--root", str(missing)]) == 64
    capsys.readouterr()
    assert not missing.exists()

    # The on-disk state is byte-for-byte identical before and after.
    assert _snapshot(root) == before
