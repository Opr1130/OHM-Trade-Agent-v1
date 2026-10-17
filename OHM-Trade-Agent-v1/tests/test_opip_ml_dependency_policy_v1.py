"""ML dependency policy: the no-ML-dependency guard is syntax-aware.

Increment: `ATDD-ML-DEPENDENCY-POLICY-v1`. The authority for what each assertion
means is `docs/atdd/scope-contracts/ATDD-ML-DEPENDENCY-POLICY-v1.md`.

Root cause this increment repairs. The guard
`tests/test_opip_decision_safety_v1.py::test_no_ml_dependency_is_introduced`
asserted that the lowercased *text* of `app/opip/**/*.py` contained none of the
ML library names. A substring test cannot tell a dependency from prose: the
ordinary words "shape", "shaped" and "reshape" contain `shap`, so
`app/opip/contracts/paper_execution.py`, `app/opip/contracts/portfolio.py` and
`app/opip/learning/evaluation.py` failed the guard while no import existed
anywhere in `app/opip`. The prohibition itself is unchanged and is proven here
not to be weakened: static imports, supported dynamic imports and declared
dependencies are all still checked, and anything the policy cannot prove fails
closed.
"""

from __future__ import annotations

import ast
import pathlib
import sys

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import parse_scope_contract  # noqa: E402
from tests.ml_dependency_policy import (  # noqa: E402
    PROHIBITED_DISTRIBUTIONS,
    PROHIBITED_IMPORT_ROOTS,
    RELEVANT_MANIFESTS,
    SUPPORTED_DYNAMIC_IMPORT_CALLS,
    manifest_file_violations,
    manifest_violations,
    package_import_violations,
    prohibited_distribution,
    prohibited_root,
    source_violations,
)

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-ML-DEPENDENCY-POLICY-v1"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"

GUARDED_PACKAGE = APP_ROOT / "app" / "opip"
GUARD_MODULE = APP_ROOT / "tests" / "test_opip_decision_safety_v1.py"
GUARD_TEST_NAME = "test_no_ml_dependency_is_introduced"

#: The text scan failed on every real module whose prose contains `shap`. That
#: population is discovered, never pinned: another increment may legitimately
#: rewrite a module's prose, and a pinned list would then assert a fact about a
#: file this increment does not own. What must stay true is that the guarded
#: package really does contain such prosaic collisions *and* still imports
#: nothing - which is what makes the syntax-aware repair meaningful.
def _text_scan_collisions() -> tuple[str, ...]:
    return tuple(
        sorted(
            path.relative_to(APP_ROOT).as_posix()
            for path in GUARDED_PACKAGE.rglob("*.py")
            if "__pycache__" not in path.parts
            and "shap" in path.read_text(encoding="utf-8").lower()
        )
    )

CHANGED_SURFACES = frozenset(
    {
        "OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT",
        "OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-ML-DEPENDENCY-POLICY-v1.md",
        "OHM-Trade-Agent-v1/tests/ml_dependency_policy.py",
        "OHM-Trade-Agent-v1/tests/test_opip_decision_safety_v1.py",
        "OHM-Trade-Agent-v1/tests/test_opip_ml_dependency_policy_v1.py",
    }
)

#: Ordinary words and unrelated names that merely contain a prohibited token.
SHAPE_WORDS = (
    '"""The payload shape, once shaped, survives a reshape."""\n\nSHAPE = "shape"\n',
    'def reshape(shape, shaped=False):\n    """Return the shaped shape."""\n'
    "    return shape\n",
    "class Shaper:\n    def shaped(self):\n        return {\"shape\": \"reshape\"}\n",
    "# shape shap shaped reshape shapely\nVALUE = 1\n",
    "MESSAGE = 'the shape is shaped by a later reshape'\n",
)


@pytest.mark.acceptance
def test_ac_001_the_real_package_passes_where_the_text_guard_failed() -> None:
    """ATDD-ML-DEPENDENCY-POLICY-v1/AC-001: the guarded package is provably clean.

    GIVEN the real `app/opip` package, whose source contains the ordinary words
    "shape", "shaped" and "reshape" in at least one real module (the population
    is discovered at test time, not pinned to a file list another increment owns)
    WHEN the repaired guard evaluates its static and dynamic imports
    THEN the package is reported clean, while those modules still contain the
    token that made the previous text scan fail
    """
    collisions = _text_scan_collisions()
    assert collisions, "no real module carries the prosaic token any more"
    scanned = [
        path
        for path in GUARDED_PACKAGE.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert len(scanned) >= 50, len(scanned)
    assert package_import_violations(GUARDED_PACKAGE) == ()


@pytest.mark.acceptance
@pytest.mark.parametrize("source", SHAPE_WORDS)
def test_ac_001_ordinary_words_never_trigger_the_policy(source: str) -> None:
    """ATDD-ML-DEPENDENCY-POLICY-v1/AC-001: prose and identifiers are not dependencies.

    GIVEN a module whose docstring, comment, identifier, class or string
    constant contains "shape", "shaped" or "reshape"
    WHEN the policy evaluates that module
    THEN no violation is reported, even though the text contains the token the
    previous substring scan rejected
    """
    assert "shap" in source.lower()
    assert source_violations("<synthetic>", source) == ()


#: The exact token list the original text-scanning guard forbade in the package
#: source, and in `requirements.txt`. Both surfaces must survive the repair.
ORIGINAL_SOURCE_TOKENS = (
    "xgboost",
    "lightgbm",
    "shap",
    "sklearn",
    "scikit",
    "torch",
    "tensorflow",
)
ORIGINAL_MANIFEST_TOKENS = (
    "xgboost",
    "lightgbm",
    "shap",
    "scikit-learn",
    "torch",
    "tensorflow",
)

PROHIBITED_IMPORTS = (
    "import shap\n",
    "import xgboost as xgb\n",
    "import lightgbm\n",
    "import torch\nimport torch.nn as nn\n",
    "import tensorflow as tf\n",
    "import torchvision\n",
    "from sklearn.ensemble import RandomForestClassifier\n",
    "from shap.explainers import TreeExplainer\n",
    "from tensorflow.keras import layers\n",
    "from scikit_learn.linear_model import LinearRegression\n",
)

#: Ordinary names that merely contain a prohibited token.
ALLOWED_IMPORTS = (
    "import shapefile\n",
    "import shapely.geometry\n",
    "from shapefile import Reader\n",
    "import shaper\n",
    "import os\nimport json\nfrom app.opip.decision import engine\n",
)

PROHIBITED_REQUIREMENTS = (
    "shap==0.44.0\n",
    "scikit-learn>=1.4\n",
    "xgboost~=2.0\n",
    "torch\n",
    "tensorflow[and-cuda]==2.16\n",
    "torchvision==0.19\n",
    "scikit_image==0.23\n",
    "-e lightgbm\n",
)

ALLOWED_REQUIREMENTS = (
    "fastapi>=0.115,<1\n",
    "uvicorn[standard]>=0.34,<1\n",
    "psycopg[binary]>=3.2,<4\n",
    "shapefile-tools==2.3\n",
    "shapely>=2.0\n",
    "python-dotenv>=1.0,<2 ; python_version >= '3.12'\n",
)


@pytest.mark.acceptance
def test_ac_002_every_prohibited_import_form_still_fails() -> None:
    """ATDD-ML-DEPENDENCY-POLICY-v1/AC-002: the prohibition is not weakened.

    GIVEN the token list the original guard forbade and every static import form
    that reaches an ML library (plain, aliased, submodule, from-import,
    same-family distribution)
    WHEN the policy evaluates each form
    THEN every one of them is still reported, while a lookalike name that is not
    the ML library is not
    """
    assert set(ORIGINAL_SOURCE_TOKENS) <= PROHIBITED_IMPORT_ROOTS
    assert prohibited_root("torchvision") is True
    assert prohibited_root("shapefile") is False
    assert prohibited_root("shapely") is False
    for source in PROHIBITED_IMPORTS:
        kinds = {item.kind for item in source_violations("<synthetic>", source)}
        assert kinds == {"prohibited-import"}, source
    for source in ALLOWED_IMPORTS:
        assert source_violations("<synthetic>", source) == (), source


@pytest.mark.acceptance
def test_ac_002_every_prohibited_declared_dependency_still_fails() -> None:
    """ATDD-ML-DEPENDENCY-POLICY-v1/AC-002: declared ML dependencies still fail.

    GIVEN a manifest line that declares a prohibited ML distribution, including
    an editable requirement and a same-family distribution
    WHEN the policy evaluates that manifest
    THEN the line is reported as a prohibited requirement, while an ordinary
    dependency whose name merely contains a token is not
    """
    for name in ORIGINAL_MANIFEST_TOKENS:
        assert prohibited_distribution(name), name
    for line in PROHIBITED_REQUIREMENTS:
        kinds = {item.kind for item in manifest_violations("<synthetic>", line)}
        assert kinds == {"prohibited-requirement"}, line
    for line in ALLOWED_REQUIREMENTS:
        assert manifest_violations("<synthetic>", line) == (), line


#: A manifest whose *text* contains the token but whose requirements do not.
MANIFEST_TEXT_SAMPLE = (
    "# shap is not a dependency of O'Pip\n"
    "fastapi>=0.115,<1\n"
    "pydantic[email]>=2.10,<3  # extras and trailing comment\n"
    "shapefile-tools==2.3\n"
    "--hash=sha256:0f0f0f0f\n"
    "\n"
)


@pytest.mark.acceptance
def test_ac_003_manifests_are_read_as_requirements_not_text() -> None:
    """ATDD-ML-DEPENDENCY-POLICY-v1/AC-003: manifests are parsed, not matched.

    GIVEN the relevant dependency manifests and a manifest whose comment names a
    prohibited library
    WHEN each manifest is parsed as requirements
    THEN comments, extras, specifiers and markers cannot trigger a violation, the
    real `requirements.txt` and `requirements-dev.txt` are clean, a prohibited
    declared dependency is reported, and a manifest that cannot be read fails
    closed instead of being skipped
    """
    assert manifest_violations("<synthetic>", MANIFEST_TEXT_SAMPLE) == ()
    assert manifest_violations("<synthetic>", "shap>=0.40\n") != ()
    for relative in RELEVANT_MANIFESTS:
        assert manifest_file_violations(APP_ROOT / relative) == (), relative
    absent = manifest_file_violations(APP_ROOT / "requirements-absent.txt")
    assert [item.kind for item in absent] == ["missing-manifest"]


@pytest.mark.acceptance
def test_ac_003_manifest_includes_are_followed_and_fail_closed(
    tmp_path: pathlib.Path,
) -> None:
    """ATDD-ML-DEPENDENCY-POLICY-v1/AC-003: includes are followed, never skipped.

    GIVEN a manifest that includes another manifest by `-r`
    WHEN the include resolves, is missing, loops, or is remote
    THEN an included prohibited dependency is still reported, a missing or
    remote include reports a violation, and a cycle stops without recursing
    forever
    """
    (tmp_path / "included.txt").write_text("shap==0.44.0\n", encoding="utf-8")
    root_manifest = tmp_path / "requirements.txt"
    root_manifest.write_text("-r included.txt\nfastapi\n", encoding="utf-8")
    assert [item.kind for item in manifest_file_violations(root_manifest)] == [
        "prohibited-requirement"
    ]
    missing = tmp_path / "missing.txt"
    missing.write_text("-r absent.txt\n", encoding="utf-8")
    assert [item.kind for item in manifest_file_violations(missing)] == [
        "unresolved-requirement-include"
    ]
    loop_a = tmp_path / "a.txt"
    loop_b = tmp_path / "b.txt"
    loop_a.write_text("-r b.txt\n", encoding="utf-8")
    loop_b.write_text("-r a.txt\n", encoding="utf-8")
    assert [item.kind for item in manifest_file_violations(loop_a)] == [
        "requirement-include-cycle"
    ]
    remote = tmp_path / "remote.txt"
    remote.write_text(
        "-r https://example.invalid/requirements.txt\n", encoding="utf-8"
    )
    assert [item.kind for item in manifest_file_violations(remote)] == [
        "unevaluable-requirement-include"
    ]


PROHIBITED_DYNAMIC_IMPORTS = (
    'import importlib\nimportlib.import_module("shap")\n',
    '__import__("torch")\n',
    'import importlib.util\nimportlib.util.find_spec(name="sklearn")\n',
    'import importlib\nimportlib.import_module("tensorflow.keras")\n',
)

ALLOWED_DYNAMIC_IMPORTS = (
    'import importlib\nimportlib.import_module("app.opip.decision.engine")\n',
    '__import__("json")\n',
    'import importlib.util\nimportlib.util.find_spec("shapefile")\n',
)

UNPROVABLE_DYNAMIC_IMPORTS = (
    'import importlib\nimportlib.import_module(module_name)\n',
    'import importlib\nimportlib.import_module("shap" if enabled else "json")\n',
    "__import__(name)\n",
)


@pytest.mark.acceptance
def test_ac_004_supported_dynamic_imports_are_evaluated() -> None:
    """ATDD-ML-DEPENDENCY-POLICY-v1/AC-004: supported dynamic imports are checked.

    GIVEN the supported dynamic-import paths and a call that reaches a prohibited
    ML library through a literal target
    WHEN the policy evaluates that source
    THEN the call is reported as a prohibited dynamic import, an ordinary dynamic
    import of a project module or a lookalike package is not, and the supported
    path set is exactly the declared one
    """
    assert SUPPORTED_DYNAMIC_IMPORT_CALLS == {
        "importlib.import_module",
        "__import__",
        "importlib.util.find_spec",
    }
    for source in PROHIBITED_DYNAMIC_IMPORTS:
        kinds = {item.kind for item in source_violations("<synthetic>", source)}
        assert kinds == {"prohibited-dynamic-import"}, source
    for source in ALLOWED_DYNAMIC_IMPORTS:
        assert source_violations("<synthetic>", source) == (), source


@pytest.mark.acceptance
def test_ac_004_unprovable_input_fails_closed() -> None:
    """ATDD-ML-DEPENDENCY-POLICY-v1/AC-004: unprovable input is never accepted.

    GIVEN a supported dynamic-import call whose target is not a literal string
    and a module that cannot be parsed
    WHEN the policy evaluates that input
    THEN each is reported rather than skipped, so an unproven module or import
    cannot pass the guard
    """
    for source in UNPROVABLE_DYNAMIC_IMPORTS:
        kinds = {item.kind for item in source_violations("<synthetic>", source)}
        assert kinds == {"unevaluable-dynamic-import"}, source
    unparseable = source_violations("<synthetic>", "def broken(:\n")
    assert [item.kind for item in unparseable] == ["unparseable-module"]
    assert package_import_violations(GUARDED_PACKAGE) == ()


@pytest.mark.acceptance
def test_ac_005_the_repair_keeps_the_guard_identity_and_its_package() -> None:
    """ATDD-ML-DEPENDENCY-POLICY-v1/AC-005: the guard is repaired, not removed.

    GIVEN the existing decision-safety guard module and the repaired guard
    WHEN its source is inspected
    THEN the guard keeps its node name and its `app/opip` package, delegates to
    the shared policy instead of scanning text, and carries no skip or xfail
    marker that could silence it
    """
    source = GUARD_MODULE.read_text(encoding="utf-8")
    assert 'OPIP_PACKAGE = Path("app/opip")' in source
    assert "from tests.ml_dependency_policy import" in source
    guards = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == GUARD_TEST_NAME
    ]
    assert len(guards) == 1
    guard = guards[0]
    body = ast.get_source_segment(source, guard) or ""
    assert "package_import_violations(OPIP_PACKAGE)" in body
    assert "manifest_file_violations" in body
    assert ".lower()" not in body
    for decorator in guard.decorator_list:
        rendered = ast.unparse(decorator)
        assert "skip" not in rendered and "xfail" not in rendered


@pytest.mark.acceptance
def test_ac_005_the_repair_touches_no_runtime_gate_or_authority_surface() -> None:
    """ATDD-ML-DEPENDENCY-POLICY-v1/AC-005: no runtime, gate or authority change.

    GIVEN this increment's scope contract
    WHEN its implementation map is inspected
    THEN it authorizes exactly the declared guard, policy, test and scope-control
    paths, no application, deployment or workflow path is authorized, and no
    unapproved scope change is recorded
    """
    contract = parse_scope_contract(CONTRACT.read_text(encoding="utf-8"))
    assert contract.increment == INCREMENT
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    allowed = {
        path for paths in contract.implementation_map.values() for path in paths
    }
    assert allowed == set(CHANGED_SURFACES)
    for path in sorted(allowed):
        assert not path.startswith(
            ("OHM-Trade-Agent-v1/app/", "OHM-Trade-Agent-v1/deploy/", ".github/")
        ), path
    assert PROHIBITED_DISTRIBUTIONS
