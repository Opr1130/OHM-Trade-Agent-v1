"""Syntax-aware no-ML-dependency policy for the O'Pip package and its manifests.

Stdlib only. This module does not import application runtime code.

Root cause this module repairs (increment ``ATDD-ML-DEPENDENCY-POLICY-v1``):
the original guard asserted that the lowercased *text* of ``app/opip/**/*.py``
contained none of the ML library names. A substring test cannot tell a
dependency from prose, so the ordinary words "shape", "shaped" and "reshape"
(which contain ``shap``) failed the guard inside
``app/opip/contracts/paper_execution.py``, ``app/opip/contracts/portfolio.py``
and ``app/opip/learning/evaluation.py`` while no import existed anywhere.

Three syntax-aware checks replace the text scan:

* static imports are read from the module AST - ``import x``, ``import x.y``,
  ``import x as y`` and ``from x.y import z`` all resolve to their *import
  root*, so comments, docstrings, identifiers and unrelated string literals
  cannot trigger a violation;
* the supported dynamic-import calls are read from the AST too
  (``importlib.import_module``, ``__import__``, ``importlib.util.find_spec``),
  and a call whose target cannot be proven from a literal string fails closed;
* dependency manifests are parsed as requirements (comment, extras, version
  specifier, environment marker, ``-e``/``--editable`` target and
  ``-r``/``--requirement`` include handling) and compared by distribution name.

The prohibition itself is unchanged. Every ML library the guard forbade is
still forbidden, including submodules, aliases and same-family distributions
(``torchvision``, ``tensorflow.keras``, ``scikit-learn``). The only names that
stop failing are those that merely *contain* a forbidden token while naming
something else (``shapefile-tools``, ``shapely``) - the proven false positive
this increment repairs. Anything this policy cannot prove safe is reported,
never silently accepted.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

#: Import roots that are prohibited in the guarded package. ``shap`` is
#: exact-match only: ``shape``, ``shaped``, ``reshape`` and ``shapely`` share
#: its prefix and are not the ML library.
PROHIBITED_IMPORT_ROOTS: frozenset[str] = frozenset(
    {
        "xgboost",
        "lightgbm",
        "shap",
        "sklearn",
        "scikit",
        "scikit_learn",
        "torch",
        "tensorflow",
    }
)

#: Prohibited families matched by prefix on an import root or distribution
#: name. Prefix matching is used only where the family cannot collide with an
#: ordinary English word or an unrelated distribution, so ``torchvision`` and
#: ``scikit-image`` stay prohibited while ``shapefile`` and ``shapely`` pass.
PROHIBITED_IMPORT_FAMILIES: frozenset[str] = frozenset(
    {"xgboost", "lightgbm", "sklearn", "scikit", "torch", "tensorflow"}
)

#: Prohibited distributions, compared after PEP 503 style normalization.
PROHIBITED_DISTRIBUTIONS: frozenset[str] = frozenset(
    {
        "xgboost",
        "lightgbm",
        "shap",
        "sklearn",
        "scikit",
        "scikit-learn",
        "torch",
        "tensorflow",
    }
)

#: Dynamic-import paths this policy resolves from a literal target.
SUPPORTED_DYNAMIC_IMPORT_CALLS: frozenset[str] = frozenset(
    {"importlib.import_module", "__import__", "importlib.util.find_spec"}
)

#: Dependency manifests relevant to the guarded package.
RELEVANT_MANIFESTS: tuple[str, ...] = ("requirements.txt", "requirements-dev.txt")

_NORMALIZE_SEPARATORS = re.compile(r"[-_.]+")
_REQUIREMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
_INCLUDE = re.compile(r"^(?:-r|--requirement)[=\s]+(?P<target>\S+)$")
_EDITABLE = re.compile(r"^(?:-e|--editable)[=\s]+(?P<target>\S+)$")
_UNRESOLVABLE_TARGET_PREFIXES = (
    "git+",
    "file:",
    "./",
    "../",
    "/",
    ".\\",
    "..\\",
    "~",
)


@dataclass(frozen=True)
class Violation:
    """One proven (or unprovable) policy problem, with its location."""

    kind: str
    location: str
    detail: str

    def render(self) -> str:
        return f"{self.kind} at {self.location}: {self.detail}"

    def __str__(self) -> str:
        return self.render()


def import_root(module: str | None) -> str | None:
    """Return the root import name of a dotted module path, or ``None``."""
    if not module:
        return None
    root = module.split(".", 1)[0]
    return root or None


def prohibited_root(root: str) -> bool:
    """Return whether an import root is prohibited by name or by family."""
    if root in PROHIBITED_IMPORT_ROOTS:
        return True
    return any(root.startswith(family) for family in PROHIBITED_IMPORT_FAMILIES)


def normalize_distribution(name: str) -> str:
    """Normalize a distribution name the way a package index compares it."""
    return _NORMALIZE_SEPARATORS.sub("-", name).strip().lower()


def prohibited_distribution(name: str) -> bool:
    """Return whether a declared distribution is prohibited."""
    normalized = normalize_distribution(name)
    if normalized in PROHIBITED_DISTRIBUTIONS:
        return True
    return any(
        normalized.startswith(family) for family in PROHIBITED_IMPORT_FAMILIES
    )


def source_violations(path: Path | str, source: str) -> tuple[Violation, ...]:
    """Return the policy violations of one Python module source.

    Only imports are inspected, so prose and identifiers can never trigger a
    violation. A module that cannot be parsed fails closed: its imports cannot
    be proven free of ML dependencies.
    """
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return (
            Violation(
                "unparseable-module",
                str(path),
                f"module could not be parsed, so its imports are unproven: {exc}",
            ),
        )
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.extend(_import_violations(path, node))
        elif isinstance(node, ast.ImportFrom):
            violations.extend(_import_from_violations(path, node))
        elif isinstance(node, ast.Call):
            violations.extend(_dynamic_import_violations(path, node))
    return tuple(violations)


def package_import_violations(root: Path | str) -> tuple[Violation, ...]:
    """Return the policy violations of every module under a package root."""
    package = Path(root)
    if not package.is_dir():
        return (
            Violation(
                "missing-package",
                str(package),
                "the guarded package is missing, so its imports are unproven",
            ),
        )
    found: list[Violation] = []
    for path in sorted(package.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        found.extend(source_violations(path, path.read_text(encoding="utf-8")))
    return tuple(found)


def manifest_file_violations(path: Path | str) -> tuple[Violation, ...]:
    """Return the policy violations of one requirement manifest file."""
    manifest = Path(path)
    if not manifest.is_file():
        return (
            Violation(
                "missing-manifest",
                str(manifest),
                "the declared dependency manifest is missing, so its "
                "dependencies are unproven",
            ),
        )
    return manifest_violations(manifest, manifest.read_text(encoding="utf-8"))


def manifest_violations(
    path: Path | str,
    text: str,
    *,
    root: Path | None = None,
    seen: tuple[Path, ...] = (),
) -> tuple[Violation, ...]:
    """Return the policy violations of one manifest's requirement text.

    Requirements are parsed, not text-matched, so a comment or a lookalike
    distribution name cannot trigger a violation. ``-r``/``--requirement``
    includes are followed relative to ``root`` and must resolve; a target this
    policy cannot resolve reports a violation instead of being skipped.
    """
    manifest = Path(path)
    base = root if root is not None else manifest.parent
    resolved = manifest.resolve()
    if resolved in seen:
        return (
            Violation(
                "requirement-include-cycle",
                str(manifest),
                "the manifest includes itself, so its requirements are unproven",
            ),
        )
    visited = seen + (resolved,)
    found: list[Violation] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if line == "":
            continue
        location = f"{manifest}:{number}"
        include = _INCLUDE.match(line)
        if include is not None:
            found.extend(
                _include_violations(base, include.group("target"), location, visited)
            )
            continue
        editable = _EDITABLE.match(line)
        if editable is not None:
            found.extend(_editable_violations(editable.group("target"), location))
            continue
        if line.startswith("-"):
            # A pip option line (index, hash, constraint, ...) declares no
            # distribution name this policy evaluates.
            continue
        matched = _REQUIREMENT_NAME.match(line)
        if matched is None:
            found.append(
                Violation(
                    "unparseable-requirement",
                    location,
                    f"`{line}` is not a requirement this policy can read",
                )
            )
            continue
        name = matched.group(0)
        if prohibited_distribution(name):
            found.append(
                Violation(
                    "prohibited-requirement",
                    location,
                    f"`{line}` declares the prohibited ML dependency `{name}`",
                )
            )
    return tuple(found)


def _import_violations(path: Path | str, node: ast.Import) -> list[Violation]:
    found: list[Violation] = []
    for alias in node.names:
        root = import_root(alias.name)
        if root is not None and prohibited_root(root):
            found.append(
                Violation(
                    "prohibited-import",
                    f"{path}:{node.lineno}",
                    f"`import {alias.name}` reaches the prohibited ML "
                    f"dependency `{root}`",
                )
            )
    return found


def _import_from_violations(path: Path | str, node: ast.ImportFrom) -> list[Violation]:
    if node.level:
        # A relative import stays inside this package.
        return []
    root = import_root(node.module)
    if root is None or not prohibited_root(root):
        return []
    return [
        Violation(
            "prohibited-import",
            f"{path}:{node.lineno}",
            f"`from {node.module} import ...` reaches the prohibited ML "
            f"dependency `{root}`",
        )
    ]


def _dynamic_import_violations(path: Path | str, node: ast.Call) -> list[Violation]:
    call = _dotted_callable(node.func)
    if call not in SUPPORTED_DYNAMIC_IMPORT_CALLS:
        return []
    target = _literal_call_argument(node, "name")
    if target is None:
        return [
            Violation(
                "unevaluable-dynamic-import",
                f"{path}:{node.lineno}",
                f"`{call}(...)` does not name its target with a literal "
                "string, so the target cannot be proven safe",
            )
        ]
    root = import_root(target)
    if root is not None and prohibited_root(root):
        return [
            Violation(
                "prohibited-dynamic-import",
                f"{path}:{node.lineno}",
                f'`{call}("{target}")` reaches the prohibited ML '
                f"dependency `{root}`",
            )
        ]
    return []


def _dotted_callable(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _literal_call_argument(node: ast.Call, keyword: str) -> str | None:
    """Return the literal string target of a supported dynamic-import call."""
    candidate: ast.expr | None = None
    if node.args:
        candidate = node.args[0]
    else:
        for item in node.keywords:
            if item.arg == keyword:
                candidate = item.value
                break
    if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
        return candidate.value
    return None


def _include_violations(
    base: Path,
    target: str,
    location: str,
    seen: tuple[Path, ...],
) -> list[Violation]:
    if _is_unresolvable_target(target):
        return [
            Violation(
                "unevaluable-requirement-include",
                location,
                f"`-r {target}` is a local path or URL, so the included "
                "requirements are unproven",
            )
        ]
    included = base / target
    if not included.is_file():
        return [
            Violation(
                "unresolved-requirement-include",
                location,
                f"`-r {target}` does not resolve to a readable manifest",
            )
        ]
    return list(
        manifest_violations(
            included,
            included.read_text(encoding="utf-8"),
            root=included.parent,
            seen=seen,
        )
    )


def _editable_violations(target: str, location: str) -> list[Violation]:
    if _is_unresolvable_target(target):
        return [
            Violation(
                "unevaluable-editable-requirement",
                location,
                f"`-e {target}` is a local path or URL, so the installed "
                "distribution cannot be checked by name",
            )
        ]
    matched = _REQUIREMENT_NAME.match(target)
    if matched is None:
        return [
            Violation(
                "unparseable-requirement",
                location,
                f"`-e {target}` is not a requirement this policy can read",
            )
        ]
    name = matched.group(0)
    if prohibited_distribution(name):
        return [
            Violation(
                "prohibited-requirement",
                location,
                f"`-e {target}` declares the prohibited ML dependency `{name}`",
            )
        ]
    return []


def _is_unresolvable_target(target: str) -> bool:
    return any(
        target.startswith(prefix) for prefix in _UNRESOLVABLE_TARGET_PREFIXES
    ) or "://" in target
