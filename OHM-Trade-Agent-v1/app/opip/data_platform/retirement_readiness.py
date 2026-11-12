"""Read-only O'Pip R2 retirement readiness report (v6 R2 retirement slice).

The report answers one question without touching anything: for each module on
the audit's R2 retirement list (the ten single-file ``app/opip/*`` modules plus
the ``app/backtesting`` package), which source files import it, which test files
reference it and which documents mention it.

It is a static reader. It walks the repository, parses Python files with
:mod:`ast` to find real imports (including relative imports) and searches docs
for the module's dotted name or source path. It only reads files: it never
creates, changes, renames, truncates, compresses or removes a file, opens no
database, starts no background work and prints a report. It has no trading,
exchange, network, Telegram or learning authority.

Run it from the application root::

    python -m app.opip.data_platform.retirement_readiness

A "reference" is deliberately precise so the report is not swamped by prose
that merely contains the word *forecast*:

- an **importer** is a non-test Python file that imports the module;
- a **test** is a test file that imports the module or names its dotted module
  path or source path;
- a **doc** is a markdown or reStructuredText file that names the module's
  dotted module path or source path.

A module is never listed as its own importer: files inside the ``app/backtesting``
package do not count as external importers of ``app.backtesting``.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
from typing import Iterable, Iterator

#: Repo root inferred from this file (``app/opip/data_platform/<this file>``).
DEFAULT_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

#: Only these suffixes are read. Secrets, configuration and data are not opened.
SOURCE_SUFFIXES: tuple[str, ...] = (".py",)
DOC_SUFFIXES: tuple[str, ...] = (".md", ".rst")

#: Directories that are never walked. Dot-directories are skipped separately.
SKIP_DIRECTORY_NAMES = frozenset(
    {
        "__pycache__",
        "node_modules",
        "venv",
        "env",
        "build",
        "dist",
        "site-packages",
        "data",
    }
)


@dataclass(frozen=True)
class R2Module:
    """One retirement candidate: its audit name, dotted module and source path."""

    name: str
    module: str
    path: str
    is_package: bool = False


#: The audit's R2 list, in audit order. Do not reorder without a new contract.
R2_MODULES: tuple[R2Module, ...] = (
    R2Module("forecast", "app.opip.forecast", "app/opip/forecast.py"),
    R2Module(
        "forecast_evaluation",
        "app.opip.forecast_evaluation",
        "app/opip/forecast_evaluation.py",
    ),
    R2Module(
        "forecast_labels",
        "app.opip.forecast_labels",
        "app/opip/forecast_labels.py",
    ),
    R2Module(
        "forecast_qualification",
        "app.opip.forecast_qualification",
        "app/opip/forecast_qualification.py",
    ),
    R2Module(
        "portfolio_selector",
        "app.opip.portfolio_selector",
        "app/opip/portfolio_selector.py",
    ),
    R2Module(
        "portfolio_comparator",
        "app.opip.portfolio_comparator",
        "app/opip/portfolio_comparator.py",
    ),
    R2Module(
        "opportunity_lifecycle",
        "app.opip.opportunity_lifecycle",
        "app/opip/opportunity_lifecycle.py",
    ),
    R2Module(
        "opportunity_persistence",
        "app.opip.opportunity_persistence",
        "app/opip/opportunity_persistence.py",
    ),
    R2Module("feasibility", "app.opip.feasibility", "app/opip/feasibility.py"),
    R2Module(
        "fev_evidence_reader",
        "app.opip.fev_evidence_reader",
        "app/opip/fev_evidence_reader.py",
    ),
    R2Module(
        "backtesting",
        "app.backtesting",
        "app/backtesting",
        is_package=True,
    ),
)


@dataclass(frozen=True)
class ModuleReadiness:
    """Every importer, test and doc reference observed for one R2 module."""

    name: str
    module: str
    path: str
    importers: tuple[str, ...]
    tests: tuple[str, ...]
    docs: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "module": self.module,
            "path": self.path,
            "importers": list(self.importers),
            "tests": list(self.tests),
            "docs": list(self.docs),
            "importer_count": len(self.importers),
            "test_count": len(self.tests),
            "doc_count": len(self.docs),
        }


@dataclass(frozen=True)
class RetirementReadinessReport:
    """Whole-repository readiness report. Nothing reported here was changed."""

    root: str
    scanned_files: int
    modules: tuple[ModuleReadiness, ...]
    read_only: bool = True
    written_files: int = 0

    @property
    def total_importers(self) -> int:
        return sum(len(item.importers) for item in self.modules)

    @property
    def total_tests(self) -> int:
        return sum(len(item.tests) for item in self.modules)

    @property
    def total_docs(self) -> int:
        return sum(len(item.docs) for item in self.modules)

    def as_dict(self) -> dict[str, object]:
        return {
            "root": self.root,
            "read_only": self.read_only,
            "written_files": self.written_files,
            "scanned_files": self.scanned_files,
            "module_count": len(self.modules),
            "totals": {
                "importers": self.total_importers,
                "tests": self.total_tests,
                "docs": self.total_docs,
            },
            "modules": [item.as_dict() for item in self.modules],
        }


def _iter_files(root: Path) -> Iterator[Path]:
    """Yield source and doc files under ``root`` in a deterministic order."""

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and name not in SKIP_DIRECTORY_NAMES
        )
        for filename in sorted(filenames):
            suffix = Path(filename).suffix.lower()
            if suffix in SOURCE_SUFFIXES or suffix in DOC_SUFFIXES:
                yield Path(dirpath) / filename


def _package_parts(relative: str) -> tuple[str, ...]:
    """Return the dotted-package parts of a repo-relative Python file path."""

    parts = relative.split("/")
    return tuple(parts[:-1])


def _resolve_relative(
    package: tuple[str, ...],
    level: int,
    module: str | None,
) -> str:
    """Resolve a relative import (``level`` dots) against a file's package."""

    keep = len(package) - (level - 1)
    if keep < 0:
        return module or ""
    base = list(package[:keep])
    if module:
        base.extend(module.split("."))
    return ".".join(base)


def _imported_modules(tree: ast.AST, package: tuple[str, ...]) -> set[str]:
    """Collect every absolute module name a parsed file imports."""

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(package, node.level, node.module)
            else:
                base = node.module or ""
            if base:
                imported.add(base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                if base:
                    imported.add(f"{base}.{alias.name}")
    return imported


def _imports_target(imported: set[str], target: R2Module) -> bool:
    """Return whether one of the imported module names is the target module."""

    prefix = target.module + "."
    return any(name == target.module or name.startswith(prefix) for name in imported)


def _dotted_pattern(target: R2Module) -> re.Pattern[str]:
    """Match the dotted module name without matching longer module names."""

    return re.compile(
        rf"(?<![A-Za-z0-9_.]){re.escape(target.module)}(?![A-Za-z0-9_])"
    )


def _path_pattern(target: R2Module) -> re.Pattern[str]:
    """Match the source path without matching longer paths."""

    return re.compile(
        rf"(?<![A-Za-z0-9_./]){re.escape(target.path)}(?![A-Za-z0-9_])"
    )


def _is_test_path(relative: str) -> bool:
    """Classify a repo-relative path as a test file."""

    parts = relative.split("/")
    if parts[0] == "tests":
        return True
    name = parts[-1]
    return name.endswith(".py") and (
        name.startswith("test_") or name.endswith("_test.py")
    )


def _is_self_reference(relative: str, target: R2Module) -> bool:
    """A module never counts as its own importer or reference."""

    if target.is_package:
        return relative == target.path or relative.startswith(target.path + "/")
    return relative == target.path


def build_report(root: Path) -> RetirementReadinessReport:
    """Read ``root`` and report the R2 references it contains. Read-only."""

    repository = Path(root)
    if not repository.is_dir():
        raise NotADirectoryError(f"repository root is not a directory: {repository}")

    dotted = {target.name: _dotted_pattern(target) for target in R2_MODULES}
    paths = {target.name: _path_pattern(target) for target in R2_MODULES}
    targets = {target.name: target for target in R2_MODULES}
    leaves = {
        target.name: target.module.rsplit(".", 1)[-1] for target in R2_MODULES
    }

    importers: dict[str, set[str]] = {target.name: set() for target in R2_MODULES}
    tests: dict[str, set[str]] = {target.name: set() for target in R2_MODULES}
    docs: dict[str, set[str]] = {target.name: set() for target in R2_MODULES}
    scanned_files = 0

    for path in _iter_files(repository):
        relative = path.relative_to(repository).as_posix()
        suffix = path.suffix.lower()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned_files += 1

        # A real reference always contains the module's leaf name. This cheap
        # filter keeps the AST parse and the precise regexes for the few files
        # that can possibly mention an R2 module.
        present = [name for name, leaf in leaves.items() if leaf in text]
        if not present:
            continue

        if suffix in DOC_SUFFIXES:
            for name in present:
                if dotted[name].search(text) or paths[name].search(text):
                    docs[name].add(relative)
            continue

        is_test = _is_test_path(relative)
        package = _package_parts(relative)
        parsed = True
        try:
            imported = _imported_modules(ast.parse(text, filename=relative), package)
        except SyntaxError:
            parsed = False
            imported = set()

        for name in present:
            target = targets[name]
            if _is_self_reference(relative, target):
                continue
            token = dotted[name].search(text) or paths[name].search(text)
            imported_target = parsed and _imports_target(imported, target)
            if is_test:
                if imported_target or token:
                    tests[name].add(relative)
            elif imported_target or (not parsed and token):
                importers[name].add(relative)

    modules = tuple(
        ModuleReadiness(
            name=target.name,
            module=target.module,
            path=target.path,
            importers=tuple(sorted(importers[target.name])),
            tests=tuple(sorted(tests[target.name])),
            docs=tuple(sorted(docs[target.name])),
        )
        for target in R2_MODULES
    )
    return RetirementReadinessReport(
        root=str(repository),
        scanned_files=scanned_files,
        modules=modules,
    )


def render_text(report: RetirementReadinessReport) -> str:
    """Render the human-readable report. Printing is the only side effect."""

    lines = [
        "O'Pip R2 retirement readiness (read-only; nothing is created, changed or deleted)",
        (
            f"root={report.root} modules={len(report.modules)} "
            f"scanned_files={report.scanned_files}"
        ),
    ]
    for item in report.modules:
        lines.append(
            f"module={item.name} path={item.path} "
            f"importers={len(item.importers)} tests={len(item.tests)} "
            f"docs={len(item.docs)}"
        )
        for importer in item.importers:
            lines.append(f"  importer={importer}")
        for test in item.tests:
            lines.append(f"  test={test}")
        for doc in item.docs:
            lines.append(f"  doc={doc}")
    lines.append(
        f"totals modules={len(report.modules)} importers={report.total_importers} "
        f"tests={report.total_tests} docs={report.total_docs}"
    )
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    """Print the readiness report. Returns a process exit code."""

    parser = argparse.ArgumentParser(
        description=(
            "Report importers, tests and docs for each R2 retirement "
            "candidate. Read-only: never creates, changes or deletes."
        )
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_REPOSITORY_ROOT),
        help=f"Repository root to scan (default {DEFAULT_REPOSITORY_ROOT})",
    )
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"repository root must be an existing directory: {root}", file=sys.stderr)
        return 64

    report = build_report(root)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
