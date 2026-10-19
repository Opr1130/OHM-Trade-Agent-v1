"""R1 test configuration: isolated correction tree + certified source on sys.path."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

R1 = Path(__file__).resolve().parents[1]
SCRIPTS = R1 / "scripts"
_env_cert = os.environ.get("OPIP_CERTIFIED_SRC")
CERT_SRC = (Path(_env_cert) / "OHM-Trade-Agent-v1") if _env_cert else R1.parents[1]
FIXTURE = R1 / "evidence" / "fixtures" / "v1"

for entry in (str(SCRIPTS), str(CERT_SRC), str(R1 / "tests")):
    if entry not in sys.path:
        sys.path.insert(0, entry)


def pytest_report_header(config):  # noqa: ARG001
    return [f"r1_root={R1}", f"certified_src={CERT_SRC}", f"certified_present={CERT_SRC.is_dir()}"]


@pytest.fixture(scope="session")
def spec() -> dict:
    return json.loads((R1 / "drafts" / "v1-dataset-spec.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def source_tree(spec) -> Path:
    if not (FIXTURE / "SYNTHETIC-FIXTURE.json").is_file():
        from make_fixture import build

        build(FIXTURE, R1 / "drafts" / "v1-dataset-spec.json")
    return FIXTURE


@pytest.fixture(scope="session")
def canonical_dir(source_tree: Path) -> Path:
    return source_tree / "data" / "opip" / "canonical" / "backups"


@pytest.fixture(scope="session")
def exported_bundle(source_tree, canonical_dir, spec, tmp_path_factory) -> Path:
    import opip_bundle as bundle

    out = tmp_path_factory.mktemp("bundles")
    result = bundle.export(
        source_root=source_tree,
        spec=spec,
        out_dir=out,
        bundle_name="r1-bundle",
        canonical_dir=canonical_dir,
        cutoff_utc="2026-10-19T06:00:00Z",
        source_identity={"droplet_id": "589794133", "ip": "161.35.106.207", "identity_proof": "FIXTURE"},
        chunk_size_mb=1,
        allow_synthetic_canonical=True,
    )
    assert result.manifest["totals"]["files"] > 2600
    return result.bundle_dir
