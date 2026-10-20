"""R4 L-02: independently grounded release provenance for a Git-free extraction.

The expected inventory is built from a trusted checkout of the pinned commit and
the actual inventory is never trusted by itself. These tests use disposable Git
repositories, so they exercise the real trust rules without the certified tree.
"""

from __future__ import annotations

import io
import json
import stat
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

import opip_release_provenance as rp

FILES = {
    "app/main.py": "print('main')\n",
    "app/services/engine.py": "VALUE = 1\n",
    "docker-compose.yml": "services: {}\n",
    "deploy/nginx/dashboard-sidecar.conf": "worker_processes 1;\n",
}


def _repo(tmp_path: Path, files: dict[str, str] | None = None) -> tuple[Path, str]:
    root = tmp_path / "repo"
    root.mkdir()
    for relative, text in (files or FILES).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "r4@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "R4"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    return root, head


def _expected(root: Path, head: str) -> dict:
    return rp.build_expected(
        root=root,
        certified_sha=head,
        provenance="git-checkout",
        archive=None,
        archive_sha256=None,
        allow_test_sha=True,
    )


def _actual(root: Path) -> dict:
    return rp.build_actual(
        root=root, host="TEST", droplet_id="0", privilege="none", observed_at=None
    )


def _tar(tmp_path: Path, files: dict[str, str], *, name: str = "cert.tar", extra=None) -> Path:
    path = tmp_path / name
    with tarfile.open(path, "w") as handle:
        for relative, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(relative)
            info.size = len(data)
            handle.addfile(info, io.BytesIO(data))
        for info in extra or ():
            handle.addfile(info)
    return path


def _zip(tmp_path: Path, files: dict[str, str], *, name: str = "cert.zip", symlink=None) -> Path:
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as handle:
        for relative, text in files.items():
            handle.writestr(relative, text)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            handle.writestr(info, "../../etc/passwd")
    return path


def test_expected_inventory_records_sha_digest_count_and_provenance(tmp_path):
    root, head = _repo(tmp_path)
    expected = _expected(root, head)
    assert expected["kind"] == "expected"
    assert expected["certified_sha"] == head
    assert expected["counts"]["source_files"] == len(FILES)
    assert expected["manifest_digest"] == rp._manifest_digest(expected["source_files"])
    assert expected["provenance"] == {"kind": "git-checkout", "head": head, "clean": True}
    assert expected["source_authenticity"] == "trusted"


def test_identical_tree_passes_the_full_inventory_comparison(tmp_path):
    root, head = _repo(tmp_path)
    result = rp.compare_inventories(_expected(root, head), _actual(root), allow_test_sha=True)
    assert result["verdict"] == "PASS"
    assert result["missing"] == [] and result["changed"] == []
    assert result["added_unexpected"] == []
    assert result["expected_file_count"] == result["actual_file_count"] == len(FILES)
    assert result["expected_manifest_digest"]


def test_missing_changed_and_added_files_are_detected(tmp_path):
    root, head = _repo(tmp_path)
    expected = _expected(root, head)

    missing_root = tmp_path / "missing"
    missing_root.mkdir()
    for relative, text in FILES.items():
        if relative == "app/main.py":
            continue
        target = missing_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    result = rp.compare_inventories(expected, _actual(missing_root), allow_test_sha=True)
    assert result["verdict"] == "FAIL"
    assert result["missing"] == ["app/main.py"]

    changed_root = tmp_path / "changed"
    changed_root.mkdir()
    for relative, text in FILES.items():
        target = changed_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "# drift\n", encoding="utf-8")
    result = rp.compare_inventories(expected, _actual(changed_root), allow_test_sha=True)
    assert result["verdict"] == "FAIL"
    assert set(result["changed"]) == set(FILES)

    added_root = tmp_path / "added"
    added_root.mkdir()
    for relative, text in FILES.items():
        target = added_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")
    (added_root / "app/backdoor.py").write_text("BAIL = True\n", encoding="utf-8")
    result = rp.compare_inventories(expected, _actual(added_root), allow_test_sha=True)
    assert result["verdict"] == "FAIL"
    assert result["added_unexpected"] == ["app/backdoor.py"]


def test_permitted_runtime_files_do_not_fail_source_equivalence(tmp_path):
    root, head = _repo(tmp_path)
    expected = _expected(root, head)
    (root / "data" / "opip" / "canonical").mkdir(parents=True)
    (root / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3").write_bytes(b"SQLite")
    (root / ".env").write_text("KEY=value\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "main.pyc").write_bytes(b"pyc")
    result = rp.compare_inventories(expected, _actual(root), allow_test_sha=True)
    assert result["verdict"] == "PASS"
    assert set(result["added_permitted_runtime"]) >= {
        "data/opip/canonical/opip_canonical_v1.sqlite3", ".env", "__pycache__/main.pyc"
    }


def test_a_candidate_inventory_cannot_authenticate_itself(tmp_path):
    root, head = _repo(tmp_path)
    with pytest.raises(PermissionError):
        rp.build_expected(
            root=root, certified_sha=head, provenance="candidate",
            archive=None, archive_sha256=None, allow_test_sha=True,
        )
    candidate = _actual(root)
    candidate["kind"] = "expected"
    candidate["source_authenticity"] = "trusted"
    candidate["provenance"] = {"kind": "candidate"}
    with pytest.raises(PermissionError):
        rp.compare_inventories(candidate, _actual(root), allow_test_sha=True)


def test_an_untrusted_or_tampered_expected_inventory_is_refused(tmp_path):
    root, head = _repo(tmp_path)
    expected = _expected(root, head)
    actual = _actual(root)

    tampered = json.loads(json.dumps(expected))
    tampered["source_files"][0]["sha256"] = "0" * 64
    with pytest.raises(PermissionError):
        rp.compare_inventories(tampered, actual, allow_test_sha=True)

    unproven = json.loads(json.dumps(expected))
    unproven["source_authenticity"] = "UNPROVEN"
    with pytest.raises(PermissionError):
        rp.compare_inventories(unproven, actual, allow_test_sha=True)

    claiming = json.loads(json.dumps(actual))
    claiming["source_authenticity"] = "trusted"
    with pytest.raises(PermissionError):
        rp.compare_inventories(expected, claiming, allow_test_sha=True)


def test_the_pinned_sha_cannot_be_overridden_in_production(tmp_path):
    root, head = _repo(tmp_path)
    with pytest.raises(PermissionError):
        rp.build_expected(
            root=root, certified_sha=head, provenance="git-checkout",
            archive=None, archive_sha256=None, allow_test_sha=False,
        )


def test_a_dirty_or_non_git_expected_source_is_refused(tmp_path):
    root, head = _repo(tmp_path)
    (root / "app/main.py").write_text("print('dirty')\n", encoding="utf-8")
    with pytest.raises(ValueError, match="uncommitted"):
        _expected(root, head)

    plain = tmp_path / "plain"
    (plain / "app").mkdir(parents=True)
    (plain / "app/main.py").write_text("x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not a git checkout"):
        _expected(plain, head)


def test_archive_provenance_derives_the_inventory_from_verified_bytes(tmp_path):
    root, head = _repo(tmp_path)
    archive = _tar(tmp_path, FILES)
    digest = rp._sha256_file(archive)
    expected = rp.build_expected(
        root=None, certified_sha=head, provenance="git-archive",
        archive=archive, archive_sha256=digest, allow_test_sha=True,
    )
    assert expected["counts"]["source_files"] == len(FILES)
    provenance = expected["provenance"]
    assert provenance["kind"] == "git-archive"
    assert provenance["archive_sha256"] == digest
    assert provenance["derived_from"] == "archive-bytes"
    assert provenance["extraction"] == "isolated-tempdir"
    assert provenance["archive_format"] == "tar"
    assert provenance["member_files"] == len(FILES)
    # The candidate root is never an input for archive provenance.
    with pytest.raises(ValueError, match="not used with git-archive"):
        rp.build_expected(
            root=root, certified_sha=head, provenance="git-archive",
            archive=archive, archive_sha256=digest, allow_test_sha=True,
        )
    with pytest.raises(ValueError, match="does not match"):
        rp.build_expected(
            root=None, certified_sha=head, provenance="git-archive",
            archive=archive, archive_sha256="f" * 64, allow_test_sha=True,
        )


def test_a_candidate_cannot_authenticate_against_an_unrelated_approved_archive(tmp_path):
    _trusted_root, head = _repo(tmp_path)
    archive = _tar(tmp_path, FILES)
    expected = rp.build_expected(
        root=None, certified_sha=head, provenance="git-archive",
        archive=archive, archive_sha256=rp._sha256_file(archive), allow_test_sha=True,
    )
    candidate = tmp_path / "candidate"
    for relative, text in FILES.items():
        target = candidate / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "# candidate drift\n", encoding="utf-8", newline="\n")
    result = rp.compare_inventories(expected, _actual(candidate), allow_test_sha=True)
    assert result["verdict"] == "FAIL"
    assert set(result["changed"]) == set(FILES)
    assert result["expected_provenance"]["derived_from"] == "archive-bytes"


def test_archive_members_with_traversal_absolute_or_links_are_refused(tmp_path):
    _root, head = _repo(tmp_path)

    def build(extra, name):
        archive = _tar(tmp_path, FILES, name=name, extra=extra)
        digest = rp._sha256_file(archive)
        with pytest.raises(ValueError):
            rp.build_expected(
                root=None, certified_sha=head, provenance="git-archive",
                archive=archive, archive_sha256=digest, allow_test_sha=True,
            )

    traversal = tarfile.TarInfo("../evil")
    traversal.size = 0
    absolute = tarfile.TarInfo("/etc/passwd")
    absolute.size = 0
    symlink = tarfile.TarInfo("app/link")
    symlink.type = tarfile.SYMTYPE
    symlink.linkname = "../../etc/passwd"
    hardlink = tarfile.TarInfo("app/hard")
    hardlink.type = tarfile.LNKTYPE
    hardlink.linkname = "app/main.py"
    duplicate = tarfile.TarInfo("app/main.py")
    duplicate.size = 0

    build([traversal], "traversal.tar")
    build([absolute], "absolute.tar")
    build([symlink], "symlink.tar")
    build([hardlink], "hardlink.tar")
    build([duplicate], "duplicate.tar")

    zip_path = _zip(tmp_path, FILES, symlink="app/link")
    with pytest.raises(ValueError, match="symlink"):
        rp.build_expected(
            root=None, certified_sha=head, provenance="git-archive",
            archive=zip_path, archive_sha256=rp._sha256_file(zip_path),
            allow_test_sha=True,
        )


def test_a_zip_archive_is_extracted_from_verified_bytes_too(tmp_path):
    _root, head = _repo(tmp_path)
    archive = _zip(tmp_path, FILES)
    expected = rp.build_expected(
        root=None, certified_sha=head, provenance="git-archive",
        archive=archive, archive_sha256=rp._sha256_file(archive), allow_test_sha=True,
    )
    assert expected["provenance"]["archive_format"] == "zip"
    assert expected["counts"]["source_files"] == len(FILES)
    assert rp.compare_inventories(expected, _actual(tmp_path / "repo"), allow_test_sha=True)["verdict"] == "PASS"


def test_an_unrecognised_or_unbound_archive_is_refused(tmp_path):
    _root, head = _repo(tmp_path)
    plain = tmp_path / "plain.tar"
    plain.write_bytes(b"not an archive")
    with pytest.raises(ValueError, match="neither a tar nor a zip"):
        rp.build_expected(
            root=None, certified_sha=head, provenance="git-archive",
            archive=plain, archive_sha256=rp._sha256_file(plain), allow_test_sha=True,
        )

    archive = _tar(tmp_path, FILES)
    expected = rp.build_expected(
        root=None, certified_sha=head, provenance="git-archive",
        archive=archive, archive_sha256=rp._sha256_file(archive), allow_test_sha=True,
    )
    unbound = json.loads(json.dumps(expected))
    unbound["provenance"].pop("derived_from")
    with pytest.raises(PermissionError, match="not bound"):
        rp.compare_inventories(unbound, _actual(tmp_path / "repo"), allow_test_sha=True)


def test_cli_archive_build_requires_no_root_and_refuses_one(tmp_path):
    _root, head = _repo(tmp_path)
    archive = _tar(tmp_path, FILES)
    digest = rp._sha256_file(archive)
    out = tmp_path / "archive-expected.json"
    assert rp.main([
        "build", "--out", str(out), "--certified-sha", head,
        "--provenance", "git-archive", "--archive", str(archive),
        "--archive-sha256", digest, "--allow-test-sha",
    ]) == rp.EXIT_PASS
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["provenance"]["derived_from"] == "archive-bytes"
    assert payload["counts"]["source_files"] == len(FILES)
    assert rp.main([
        "build", "--root", str(tmp_path / "repo"), "--out", str(tmp_path / "x.json"),
        "--certified-sha", head, "--provenance", "git-archive",
        "--archive", str(archive), "--archive-sha256", digest, "--allow-test-sha",
    ]) == rp.EXIT_INSUFFICIENT


def test_cli_exit_codes_are_stable(tmp_path, capsys):
    root, head = _repo(tmp_path)
    expected_path = tmp_path / "expected.json"
    actual_path = tmp_path / "actual.json"
    assert rp.main(["build", "--root", str(root), "--out", str(expected_path),
                    "--certified-sha", head, "--allow-test-sha"]) == rp.EXIT_PASS
    assert rp.main(["inventory", "--root", str(root), "--out", str(actual_path)]) == rp.EXIT_PASS
    assert rp.main(["compare", "--expected", str(expected_path),
                    "--actual", str(actual_path), "--allow-test-sha"]) == rp.EXIT_PASS
    assert rp.main(["build", "--root", str(root), "--out", str(tmp_path / "bad.json"),
                    "--certified-sha", head, "--provenance", "candidate",
                    "--allow-test-sha"]) == rp.EXIT_REFUSED
    assert rp.main(["build", "--root", str(root), "--out", str(tmp_path / "bad2.json"),
                    "--certified-sha", head]) == rp.EXIT_REFUSED
    (root / "app/main.py").write_text("changed\n", encoding="utf-8")
    assert rp.main(["inventory", "--root", str(root), "--out", str(actual_path)]) == rp.EXIT_PASS
    assert rp.main(["compare", "--expected", str(expected_path),
                    "--actual", str(actual_path), "--allow-test-sha"]) == rp.EXIT_FAIL
    capsys.readouterr()

