"""Release artifact + receipt contract for the O'Pip deploy controller.

Why this module exists
-----------------------
`deploy/remote/ohm-deploy` historically derived release identity from a Git
worktree (`git rev-parse main`, reset --hard, `compose build`) and rolled back by
rebuilding source. On a host without `.git` that contract has no identity and no
rollback vehicle, and a rebuild cannot prove it restored what actually ran.

This module holds the *decision* logic for the artifact-based replacement in one
testable place (stdlib only, no Docker, no Git):

* an immutable, atomic, versioned release receipt;
* digest-pinned image identity for BOTH services (app and canonical writer);
* provenance classification, so a receipt can never claim recoverability that
  only a local image store provides;
* rollback eligibility that fails closed.

The shell controller keeps every safety barrier it has today and only *asks* this
module the questions that used to be asked of Git. Nothing here mutates state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from typing import Any

RECEIPT_VERSION = 1
SCHEMA_ID = "opip-release-receipt-v1"

# Deployment stages. APPLIED means images are running but not yet verified;
# only VERIFIED may become the last-good pair.
STAGES = ("PREPARED", "APPLIED", "VERIFIED", "ROLLED_BACK", "ROLLBACK_UNPROVEN")

PROVENANCE_KINDS = ("registry", "local_store", "unknown")

APP_SERVICE = "ohm-trade-agent"
WRITER_SERVICE = "opip-canonical-writer"

SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
DIGEST_REF = re.compile(r"@sha256:[0-9a-f]{64}$")


class ArtifactError(Exception):
    """Raised for any receipt/identity condition that must fail closed."""


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ArtifactError(message)


def _is_digest_pinned(ref: str) -> bool:
    """A reference is immutable only when it names a digest.

    Accepts `repo@sha256:...` and the bare `sha256:<id>` form.
    Rejects floating tags such as `ohm-trade-agent:latest`.
    """
    return bool(DIGEST_REF.search(ref) or IMAGE_ID.match(ref))


def classify_provenance(kind: str) -> str:
    _require(kind in PROVENANCE_KINDS, f"unknown provenance kind: {kind!r}")
    return kind


@dataclass(frozen=True)
class ServiceArtifact:
    service: str
    image_ref: str
    image_id: str
    revision_label: str
    provenance_kind: str
    provenance_reference: str

    def validate(self) -> None:
        _require(bool(self.service), "service artifact missing service name")
        _require(bool(self.image_ref), f"{self.service}: missing image_ref")
        _require(_is_digest_pinned(self.image_ref),
                 f"{self.service}: image_ref is not digest-pinned: {self.image_ref!r}")
        _require(bool(IMAGE_ID.match(self.image_id)),
                 f"{self.service}: image_id is not sha256:<64 hex>: {self.image_id!r}")
        _require(bool(SHA40.match(self.revision_label)),
                 f"{self.service}: revision_label is not a 40-char sha")
        classify_provenance(self.provenance_kind)
        if self.provenance_kind == "registry":
            _require(_is_digest_pinned(self.provenance_reference),
                     f"{self.service}: registry provenance must be digest-pinned")
        else:
            _require(bool(self.provenance_reference),
                     f"{self.service}: provenance reference is empty")

    def as_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "image_ref": self.image_ref,
            "image_id": self.image_id,
            "revision_label": self.revision_label,
            "provenance": {
                "kind": self.provenance_kind,
                "reference": self.provenance_reference,
            },
        }


@dataclass(frozen=True)
class Receipt:
    sequence: int
    release_id: str
    authorized_target_sha: str
    stage: str
    source_control_version: str
    app: ServiceArtifact
    writer: ServiceArtifact
    pair_id: str
    runtime: dict[str, Any]
    last_verified_good: dict[str, Any] | None
    health_evidence: dict[str, Any]

    def validate(self) -> None:
        _require(isinstance(self.sequence, int) and self.sequence >= 1,
                 "sequence must be a positive integer")
        _require(bool(SHA40.match(self.release_id)), "release_id is not a 40-char sha")
        _require(bool(SHA40.match(self.authorized_target_sha)),
                 "authorized_target_sha is not a 40-char sha")
        _require(self.stage in STAGES, f"invalid stage: {self.stage!r}")
        _require(bool(self.source_control_version), "source_control_version is empty")
        self.app.validate()
        self.writer.validate()
        _require(self.app.service == APP_SERVICE,
                 f"app service must be {APP_SERVICE}, got {self.app.service!r}")
        _require(self.writer.service == WRITER_SERVICE,
                 f"writer service must be {WRITER_SERVICE}, got {self.writer.service!r}")
        _require(bool(self.pair_id), "pair_id is empty")
        _require(isinstance(self.runtime, dict), "runtime must be an object")
        _require(isinstance(self.health_evidence, dict), "health_evidence must be an object")

    def offhost_recoverable(self) -> bool:
        """True only when BOTH images are attestable from off-host storage."""
        return (self.app.provenance_kind == "registry"
                and self.writer.provenance_kind == "registry")

    def rollback_eligibility(self) -> dict[str, Any]:
        """Rollback may only be claimed when it can actually be restored.

        A locally-present image is not recoverable: the local store shares the
        failure domain with the host. Such a receipt is recorded, never claimed.
        """
        if not self.offhost_recoverable():
            return {
                "eligible": False,
                "reason": "BLOCKED_STORAGE",
                "detail": "at least one image is not registry-attestable; a "
                          "local-only image shares the host failure domain",
                "requires_offhost": True,
            }
        if self.stage != "VERIFIED":
            return {
                "eligible": False,
                "reason": f"STAGE_NOT_VERIFIED:{self.stage}",
                "detail": "only a VERIFIED receipt may define a rollback target",
                "requires_offhost": True,
            }
        if not self.health_evidence.get("core_ok") \
                or not self.health_evidence.get("writer_ok"):
            return {
                "eligible": False,
                "reason": "HEALTH_UNPROVEN",
                "detail": "health evidence is missing or negative",
                "requires_offhost": True,
            }
        return {
            "eligible": True,
            "reason": "OK",
            "detail": "both images digest-pinned and registry-attestable",
            "requires_offhost": False,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_ID,
            "receipt_version": RECEIPT_VERSION,
            "sequence": self.sequence,
            "release_id": self.release_id,
            "authorized_target_sha": self.authorized_target_sha,
            "stage": self.stage,
            "source_control_version": self.source_control_version,
            "app": self.app.as_dict(),
            "writer": self.writer.as_dict(),
            "compatibility": {"pair_id": self.pair_id},
            "runtime": self.runtime,
            "last_verified_good": self.last_verified_good,
            "health_evidence": self.health_evidence,
            "rollback_eligibility": self.rollback_eligibility(),
        }


# --- I/O ------------------------------------------------------------------
def write_receipt_atomic(path: str, receipt: Receipt, mode: int = 0o640) -> str:
    """Write a receipt atomically: same-directory temp file, fsync, rename.

    A reader never observes a partial receipt, and a crash cannot leave a
    half-written file in place of a good one.
    """
    receipt.validate()
    payload = json.dumps(receipt.as_dict(), indent=2, sort_keys=True) + "\n"
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".receipt-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        if os.name == "posix":
            # Directory fsync makes the rename durable. It is a POSIX idiom and
            # is not possible on Windows, where the rename is already durable
            # enough for this controller (production is Linux).
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def load_receipt_dict(path: str) -> dict[str, Any]:
    """Load a receipt, refusing anything that is not a complete JSON object."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except FileNotFoundError as exc:
        raise ArtifactError(f"receipt missing: {path}") from exc
    if not raw.strip():
        raise ArtifactError(f"receipt is empty: {path}")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"receipt is not valid JSON: {path}: {exc}") from exc
    _require(isinstance(data, dict), "receipt root must be an object")
    _require(data.get("schema") == SCHEMA_ID,
             f"unexpected receipt schema: {data.get('schema')!r}")
    return data


def receipt_from_dict(data: dict[str, Any]) -> Receipt:
    def svc(key: str) -> ServiceArtifact:
        node = data.get(key) or {}
        prov = node.get("provenance") or {}
        return ServiceArtifact(
            service=str(node.get("service", "")),
            image_ref=str(node.get("image_ref", "")),
            image_id=str(node.get("image_id", "")),
            revision_label=str(node.get("revision_label", "")),
            provenance_kind=str(prov.get("kind", "unknown")),
            provenance_reference=str(prov.get("reference", "")),
        )

    receipt = Receipt(
        sequence=data.get("sequence", 0),
        release_id=str(data.get("release_id", "")),
        authorized_target_sha=str(data.get("authorized_target_sha", "")),
        stage=str(data.get("stage", "")),
        source_control_version=str(data.get("source_control_version", "")),
        app=svc("app"),
        writer=svc("writer"),
        pair_id=str((data.get("compatibility") or {}).get("pair_id", "")),
        runtime=data.get("runtime") or {},
        last_verified_good=data.get("last_verified_good"),
        health_evidence=data.get("health_evidence") or {},
    )
    receipt.validate()
    return receipt


def next_sequence(previous: dict[str, Any] | None) -> int:
    """Monotonic sequence; a receipt can never be silently overwritten."""
    if not previous:
        return 1
    prev = previous.get("sequence", 0)
    _require(isinstance(prev, int) and prev >= 0, "previous receipt sequence is invalid")
    return prev + 1


# --- release bundle (D9: host-executed scripts without /opt source) --------
BUNDLE_SCHEMA = "opip-release-bundle-v1"

# Host-executed operational inputs the controller reads today from $APP_ROOT.
# Container-internal code is NOT listed: it belongs in the image, not the bundle.
HOST_SCRIPT_ALLOWLIST = (
    "deploy/remote/reconcile-scheduler.sh",
    "deploy/remote/reconcile-stream-worker.sh",
    "deploy/remote/retire-p1-shadow-outbox.sh",
    "deploy/remote/export-opip-learning-evidence.sh",
    "deploy/remote/diagnose-opip-learning.sh",
    "deploy/remote/opip-learning-read-export.sh",
    "deploy/remote/configure-opip-learning-reader.sh",
    "deploy/remote/opip-learning-reader-permissions.sh",
)

# Inputs the controller reads from the release tree but does not execute.
# They stay in place (Compose project identity depends on the project directory)
# and are hash-verified against the bundle before use, so the dependency is
# explicit and provenance-checked instead of invisible.
VERIFIED_CONFIG_ALLOWLIST = (
    "docker-compose.yml",
    "docker-compose.paper.yml",
    "docker-compose.artifact.yml",
)

BUNDLE_ALLOWLIST = HOST_SCRIPT_ALLOWLIST + VERIFIED_CONFIG_ALLOWLIST

_REL_PATH = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9._/-]+$")


def verify_bundle(bundle_dir: str) -> dict[str, Any]:
    """Verify a versioned release bundle: checksums, allowlist, image refs.

    The bundle is the only source of host-executed scripts in artifact mode.
    Anything missing, extra, mis-checksummed or path-escaping fails closed.
    """
    manifest_path = os.path.join(bundle_dir, "release-bundle.json")
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise ArtifactError(f"release bundle manifest missing: {manifest_path}") from exc
    except json.JSONDecodeError as exc:
        raise ArtifactError(f"release bundle manifest is not valid JSON: {exc}") from exc

    _require(isinstance(data, dict), "bundle manifest root must be an object")
    _require(data.get("schema") == BUNDLE_SCHEMA,
             f"unexpected bundle schema: {data.get('schema')!r}")
    release_id = str(data.get("release_id", ""))
    _require(bool(SHA40.match(release_id)), "bundle release_id is not a 40-char sha")

    files = data.get("files")
    _require(isinstance(files, dict) and files, "bundle files map is empty")
    for rel, digest in files.items():
        _require(bool(_REL_PATH.match(rel)),
                 f"bundle path is not a safe relative path: {rel!r}")
        _require(bool(SHA256_HEX.match(str(digest))),
                 f"bundle entry has no sha256 digest: {rel!r}")
        full = os.path.join(bundle_dir, rel)
        _require(os.path.isfile(full), f"bundle entry missing on disk: {rel}")
        with open(full, "rb") as fh:
            actual = hashlib.sha256(fh.read()).hexdigest()
        _require(actual == digest, f"bundle checksum mismatch: {rel}")
        _require(os.access(full, os.R_OK), f"bundle entry unreadable: {rel}")

    missing_allow = [p for p in BUNDLE_ALLOWLIST if p not in files]
    _require(not missing_allow,
             f"bundle is missing required host scripts/config: {missing_allow}")

    images = data.get("images") or {}
    for key in ("app", "writer"):
        node = images.get(key) or {}
        _require(bool(node.get("ref")) and _is_digest_pinned(str(node["ref"])),
                 f"bundle images.{key}.ref is not digest-pinned")
        _require(bool(SHA40.match(str(node.get("revision", "")))),
                 f"bundle images.{key}.revision is not a 40-char sha")
    return {
        "release_id": release_id,
        "app_ref": str(images["app"]["ref"]),
        "writer_ref": str(images["writer"]["ref"]),
        "app_revision": str(images["app"]["revision"]),
        "writer_revision": str(images["writer"]["revision"]),
        "file_count": len(files),
    }


def validate_registry_digest(expected_digest: str, repo_digest: str,
                             config_id: str) -> dict[str, Any]:
    """A RepoDigest is a registry manifest digest; a config ID is not.

    Rollback may only rely on the registry identity, so the two must never be
    conflated (the live host currently has RepoDigests that merely resemble its
    local config IDs, which proves local recoverability at best).
    """
    expected = expected_digest.split("@")[-1]
    _require(bool(IMAGE_ID.match(expected)),
             f"expected digest is not sha256:<64 hex>: {expected_digest!r}")
    _require(bool(IMAGE_ID.match(config_id)),
             f"config id is not sha256:<64 hex>: {config_id!r}")
    _require(config_id != expected,
             "registry manifest digest equals the local config id: not a registry identity")
    _require(repo_digest.endswith(expected),
             f"resolved repo digest {repo_digest!r} does not match expected {expected!r}")
    return {"ok": True, "digest": expected, "registry_identity": True}


def _cmd_verify_bundle(args: argparse.Namespace) -> int:
    info = verify_bundle(args.dir)
    print("BUNDLE_OK=1")
    print(f"BUNDLE_RELEASE_ID={info['release_id']}")
    print(f"BUNDLE_FILE_COUNT={info['file_count']}")
    print(f"APP_IMAGE_REF={info['app_ref']}")
    print(f"WRITER_IMAGE_REF={info['writer_ref']}")
    print(f"APP_REVISION={info['app_revision']}")
    print(f"WRITER_REVISION={info['writer_revision']}")
    return 0


def _cmd_registry_digest(args: argparse.Namespace) -> int:
    verdict = validate_registry_digest(args.expected, args.repo_digest, args.config_id)
    print(f"REGISTRY_DIGEST_OK=1")
    print(f"REGISTRY_DIGEST={verdict['digest']}")
    return 0


def verify_onhost_config(bundle_dir: str, app_root: str) -> dict[str, str]:
    """Prove the on-host Compose inputs are exactly the release's, or refuse.

    The files stay where Compose expects them (project identity), but their
    content must match the bundle checksum. This converts the previous
    *invisible* dependency on the live source tree into a verified one.
    """
    manifest_path = os.path.join(bundle_dir, "release-bundle.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        files = (json.load(fh).get("files") or {})
    checked: dict[str, str] = {}
    for rel in VERIFIED_CONFIG_ALLOWLIST:
        _require(rel in files, f"config not present in bundle: {rel}")
        host = os.path.join(app_root, rel)
        _require(os.path.isfile(host), f"on-host config missing: {host}")
        with open(host, "rb") as fh:
            actual = hashlib.sha256(fh.read()).hexdigest()
        _require(actual == files[rel],
                 f"on-host config differs from the release bundle: {rel}")
        checked[rel] = actual
    return checked


def _cmd_check_config(args: argparse.Namespace) -> int:
    checked = verify_onhost_config(args.bundle, args.root)
    print("CONFIG_VERIFIED=1")
    print(f"CONFIG_FILE_COUNT={len(checked)}")
    return 0


def _cmd_write_receipt(args: argparse.Namespace) -> int:
    """Build and atomically persist a receipt from controller-supplied facts.

    The shell supplies validated values only (regex-checked below); JSON is
    assembled here so no shell interpolation can corrupt the document.
    """
    prov_kind = args.provenance_kind
    classify_provenance(prov_kind)
    receipt = Receipt(
        sequence=args.sequence,
        release_id=args.release_id,
        authorized_target_sha=args.target_sha,
        stage=args.stage,
        source_control_version=SCHEMA_ID,
        app=ServiceArtifact(args.app_service, args.app_ref, args.app_image_id,
                            args.app_revision, prov_kind, args.provenance_reference),
        writer=ServiceArtifact(args.writer_service, args.writer_ref,
                               args.writer_image_id, args.writer_revision,
                               prov_kind, args.provenance_reference),
        pair_id=args.pair_id,
        runtime={"app_container_id": args.app_container_id,
                 "writer_container_id": args.writer_container_id,
                 "observed_at_utc": args.observed_at},
        last_verified_good=None,
        health_evidence={"core_ok": args.core_ok == "1",
                         "writer_ok": args.writer_ok == "1",
                         "profile": args.profile},
    )
    previous = None
    if os.path.exists(args.path):
        previous = load_receipt_dict(args.path)
    expected = next_sequence(previous)
    _require(args.sequence == expected,
             f"sequence {args.sequence} would not be monotonic (expected {expected})")
    write_receipt_atomic(args.path, receipt)
    print(f"RECEIPT_WRITTEN={args.path}")
    print(f"RECEIPT_SEQUENCE={receipt.sequence}")
    print(f"ROLLBACK_ELIGIBLE={1 if receipt.rollback_eligibility()['eligible'] else 0}")
    print(f"ROLLBACK_REASON={receipt.rollback_eligibility()['reason']}")
    return 0


def _cmd_host_script(args: argparse.Namespace) -> int:
    """Resolve a host-executed script OUT of the verified bundle, never /opt."""
    path = resolve_host_script(args.bundle, args.name)
    print(path)
    return 0


def resolve_host_script(bundle_dir: str, name: str) -> str:
    _require(name in HOST_SCRIPT_ALLOWLIST,
             f"script is not an allowlisted host-executed input: {name!r}")
    manifest_path = os.path.join(bundle_dir, "release-bundle.json")
    with open(manifest_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    files = data.get("files") or {}
    _require(name in files, f"script not present in bundle: {name}")
    full = os.path.join(bundle_dir, name)
    _require(os.path.isfile(full), f"script missing on disk: {name}")
    with open(full, "rb") as fh:
        actual = hashlib.sha256(fh.read()).hexdigest()
    _require(actual == files[name], f"script checksum mismatch: {name}")
    need = os.R_OK | (os.X_OK if name.endswith(".sh") else 0)
    _require(os.access(full, need), f"script not readable/executable: {name}")
    return os.path.abspath(full)


def build_bundle(root: str, release_id: str, out_dir: str,
                 app_ref: str, writer_ref: str,
                 app_revision: str, writer_revision: str) -> dict[str, Any]:
    """Assemble a versioned, checksummed bundle of host-executed inputs.

    CI-side only: it never runs on a host, never contacts a registry and never
    touches production. The controller later verifies the result before use.
    """
    _require(bool(SHA40.match(release_id)), "release_id is not a 40-char sha")
    for key, ref in (("app", app_ref), ("writer", writer_ref)):
        _require(_is_digest_pinned(ref), f"{key} image ref is not digest-pinned")
    for key, rev in (("app", app_revision), ("writer", writer_revision)):
        _require(bool(SHA40.match(rev)), f"{key} revision is not a 40-char sha")

    files: dict[str, str] = {}
    for rel in BUNDLE_ALLOWLIST:
        src = os.path.join(root, rel)
        _require(os.path.isfile(src), f"allowlisted host input missing in source: {rel}")
        with open(src, "rb") as fh:
            payload = fh.read()
        dst = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as fh:
            fh.write(payload)
        os.chmod(dst, 0o750 if rel in HOST_SCRIPT_ALLOWLIST else 0o640)
        files[rel] = hashlib.sha256(payload).hexdigest()

    manifest = {
        "schema": BUNDLE_SCHEMA,
        "release_id": release_id,
        "images": {
            "app": {"ref": app_ref, "revision": app_revision},
            "writer": {"ref": writer_ref, "revision": writer_revision},
        },
        "files": files,
    }
    target = os.path.join(out_dir, "release-bundle.json")
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, target)
    return manifest


def _cmd_build_bundle(args: argparse.Namespace) -> int:
    manifest = build_bundle(args.root, args.release_id, args.out,
                           args.app_ref, args.writer_ref,
                           args.app_revision, args.writer_revision)
    print(f"BUNDLE_WRITTEN={os.path.join(args.out, 'release-bundle.json')}")
    print(f"BUNDLE_FILE_COUNT={len(manifest['files'])}")
    return 0


# --- CLI (the shell controller's only interface) ---------------------------
def _cmd_validate(args: argparse.Namespace) -> int:
    receipt = receipt_from_dict(load_receipt_dict(args.path))
    verdict = receipt.rollback_eligibility()
    print("RECEIPT_VALID=1")
    print(f"RELEASE_ID={receipt.release_id}")
    print(f"STAGE={receipt.stage}")
    print(f"PAIR_ID={receipt.pair_id}")
    print(f"APP_IMAGE_REF={receipt.app.image_ref}")
    print(f"WRITER_IMAGE_REF={receipt.writer.image_ref}")
    print(f"APP_IMAGE_ID={receipt.app.image_id}")
    print(f"WRITER_IMAGE_ID={receipt.writer.image_id}")
    print(f"ROLLBACK_ELIGIBLE={1 if verdict['eligible'] else 0}")
    print(f"ROLLBACK_REASON={verdict['reason']}")
    return 0


def _cmd_rollback_eligibility(args: argparse.Namespace) -> int:
    verdict = receipt_from_dict(load_receipt_dict(args.path)).rollback_eligibility()
    print(f"ROLLBACK_ELIGIBLE={1 if verdict['eligible'] else 0}")
    print(f"ROLLBACK_REASON={verdict['reason']}")
    print(f"ROLLBACK_DETAIL={verdict['detail']}")
    return 0 if verdict["eligible"] else 3


def _cmd_next_sequence(args: argparse.Namespace) -> int:
    previous = None
    if args.previous and os.path.exists(args.previous):
        previous = load_receipt_dict(args.previous)
    print(next_sequence(previous))
    return 0


def _cmd_stage_transition(args: argparse.Namespace) -> int:
    """A receipt may only advance along the allowed edges, never backwards."""
    data = load_receipt_dict(args.path)
    current = str(data.get("stage", ""))
    allowed = {
        "PREPARED": ("APPLIED", "ROLLBACK_UNPROVEN"),
        "APPLIED": ("VERIFIED", "ROLLED_BACK", "ROLLBACK_UNPROVEN"),
        "VERIFIED": ("ROLLED_BACK",),
        "ROLLED_BACK": (),
        "ROLLBACK_UNPROVEN": ("ROLLED_BACK",),
    }
    _require(current in allowed, f"unknown current stage: {current!r}")
    if args.to not in allowed[current]:
        raise ArtifactError(f"illegal stage transition {current} -> {args.to}")
    print(f"STAGE_TRANSITION_OK={current}->{args.to}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="release_artifact")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("validate")
    p.add_argument("--path", required=True)
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("rollback-eligibility")
    p.add_argument("--path", required=True)
    p.set_defaults(func=_cmd_rollback_eligibility)

    p = sub.add_parser("next-sequence")
    p.add_argument("--previous", default="")
    p.set_defaults(func=_cmd_next_sequence)

    p = sub.add_parser("stage-transition")
    p.add_argument("--path", required=True)
    p.add_argument("--to", required=True)
    p.set_defaults(func=_cmd_stage_transition)

    p = sub.add_parser("verify-bundle")
    p.add_argument("--dir", required=True)
    p.set_defaults(func=_cmd_verify_bundle)

    p = sub.add_parser("registry-digest")
    p.add_argument("--expected", required=True)
    p.add_argument("--repo-digest", required=True)
    p.add_argument("--config-id", required=True)
    p.set_defaults(func=_cmd_registry_digest)

    p = sub.add_parser("host-script")
    p.add_argument("--bundle", required=True)
    p.add_argument("--name", required=True)
    p.set_defaults(func=_cmd_host_script)

    p = sub.add_parser("check-config")
    p.add_argument("--bundle", required=True)
    p.add_argument("--root", required=True)
    p.set_defaults(func=_cmd_check_config)

    p = sub.add_parser("write-receipt")
    p.add_argument("--path", required=True)
    p.add_argument("--sequence", type=int, required=True)
    p.add_argument("--release-id", required=True)
    p.add_argument("--target-sha", required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--app-service", default=APP_SERVICE)
    p.add_argument("--app-ref", required=True)
    p.add_argument("--app-image-id", required=True)
    p.add_argument("--app-revision", required=True)
    p.add_argument("--writer-service", default=WRITER_SERVICE)
    p.add_argument("--writer-ref", required=True)
    p.add_argument("--writer-image-id", required=True)
    p.add_argument("--writer-revision", required=True)
    p.add_argument("--pair-id", required=True)
    p.add_argument("--app-container-id", default="")
    p.add_argument("--writer-container-id", default="")
    p.add_argument("--observed-at", default="")
    p.add_argument("--core-ok", choices=("0", "1"), default="0")
    p.add_argument("--writer-ok", choices=("0", "1"), default="0")
    p.add_argument("--profile", default="")
    p.add_argument("--provenance-kind", choices=PROVENANCE_KINDS, required=True)
    p.add_argument("--provenance-reference", required=True)
    p.set_defaults(func=_cmd_write_receipt)

    p = sub.add_parser("build-bundle")
    p.add_argument("--root", required=True)
    p.add_argument("--release-id", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--app-ref", required=True)
    p.add_argument("--writer-ref", required=True)
    p.add_argument("--app-revision", required=True)
    p.add_argument("--writer-revision", required=True)
    p.set_defaults(func=_cmd_build_bundle)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ArtifactError as exc:
        print(f"ARTIFACT_ERROR={exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
