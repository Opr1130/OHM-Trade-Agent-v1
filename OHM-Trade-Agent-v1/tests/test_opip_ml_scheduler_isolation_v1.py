import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ml_evidence_cron_is_independent_from_unified_cycle_lock():
    cron = (ROOT / "deploy" / "cron.d" / "opip-ml-evidence").read_text(
        encoding="utf-8"
    )
    assert "app.jobs.run_opip_ml_capture" in cron
    assert "app.jobs.build_phase3c_forward_outcomes" in cron
    assert "/var/run/opip-ml-capture.lock" in cron
    assert "/var/run/opip-ml-outcomes.lock" in cron
    executable = "\n".join(
        line for line in cron.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert "/var/run/ohm-unified-cycle.lock" not in executable
    assert "* * * * * root" in cron
    assert "*/10 * * * * root" in cron


def _module_path(module: str) -> Path | None:
    candidate = ROOT / Path(*module.split("."))
    file_path = candidate.with_suffix(".py")
    if file_path.is_file():
        return file_path
    package_path = candidate / "__init__.py"
    if package_path.is_file():
        return package_path
    return None


def _first_party_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(
                alias.name for alias in node.names if alias.name.startswith("app.")
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "app" or node.module.startswith("app."):
                names.add(node.module)
    return names


def test_unified_cycle_does_not_import_or_run_ml_capture():
    seen: set[str] = set()
    pending = ["app.jobs.run_cycle"]
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        path = _module_path(module)
        if path is None:
            continue
        pending.extend(_first_party_imports(path) - seen)

    assert "app.services.opip_ml_evidence_capture" not in seen
    assert "app.jobs.run_opip_ml_capture" not in seen


def test_scheduler_reconcile_installs_and_validates_ml_evidence_cron():
    source = (
        ROOT / "deploy" / "remote" / "reconcile-scheduler.sh"
    ).read_text(encoding="utf-8")
    assert 'ML_EVIDENCE_DST="/etc/cron.d/opip-ml-evidence"' in source
    assert 'install -o root -g root -m 0644 "$ML_EVIDENCE_SRC" "$ML_EVIDENCE_DST"' in source
    assert "app.jobs.run_opip_ml_capture" in source
    assert "app.jobs.build_phase3c_forward_outcomes" in source


def test_deploy_probes_ml_capture_without_making_it_authoritative():
    source = (ROOT / "deploy" / "remote" / "ohm-deploy").read_text(
        encoding="utf-8"
    )
    assert "python -m app.jobs.run_opip_ml_capture" in source
    assert "flock -n /var/run/opip-ml-capture.lock" in source
    assert "if flock -n /var/run/opip-ml-capture.lock" in source
    assert "production unaffected" in source


def test_scheduler_validator_ignores_comment_only_unified_lock_mentions():
    source = (
        ROOT / "deploy" / "remote" / "reconcile-scheduler.sh"
    ).read_text(encoding="utf-8")
    cron = (ROOT / "deploy" / "cron.d" / "opip-ml-evidence").read_text(
        encoding="utf-8"
    )

    assert "/var/run/ohm-unified-cycle.lock" in cron
    executable = "\n".join(
        line for line in cron.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    assert "/var/run/ohm-unified-cycle.lock" not in executable
    assert "for cmd in install crontab grep awk mktemp cp rm" in source
    assert "!/^[[:space:]]*#/" in source
    assert "/\\/var\\/run\\/ohm-unified-cycle\\.lock/" in source
    assert "grep -v -E '^[[:space:]]*#' \"$ML_EVIDENCE_DST\" | grep -q" not in source
