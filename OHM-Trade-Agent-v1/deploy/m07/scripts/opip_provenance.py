"""HF-06 provenance: bind a bundle to trusted V1 host evidence.

A bundle is only acceptable as FINAL V1 state when all of the following hold
together (never individually): trusted identity, fresh stated cutoff, a real
(non-synthetic) canonical generation, a matching archive digest, and every
declared dataset present.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import opip_archive as archive_mod
    import opip_bundle as bundle_mod
except ModuleNotFoundError:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import opip_archive as archive_mod
    import opip_bundle as bundle_mod

CERTIFIED_SHA = "c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
TRUSTED_PROOF = "host-evidence"
FIXTURE_PROOF = "fixture"


def normalise_identity(evidence: dict) -> dict:
    required = ("droplet_id", "ip")
    missing = [key for key in required if not str(evidence.get(key, "")).strip()]
    return {
        "droplet_id": str(evidence.get("droplet_id", "")).strip(),
        "ip": str(evidence.get("ip", "")).strip(),
        "hostname": str(evidence.get("hostname", "")).strip(),
        "sampled_at_utc": str(evidence.get("sampled_at_utc", "")).strip(),
        "proven": not missing,
        "missing": missing,
    }


def _parse(ts: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def verify_provenance(
    *,
    bundle_dir: Path,
    evidence: dict,
    max_age_hours: int = 24,
    allow_fixture: bool = False,
    expected_droplet_id: str = "589794133",
) -> dict:
    checks: list[dict] = []

    def add(ident: str, ok: bool, detail: str) -> None:
        checks.append({"id": ident, "status": "PASS" if ok else "FAIL", "detail": detail})

    bundle_dir = Path(bundle_dir)
    manifest_path = bundle_dir / bundle_mod.MANIFEST_NAME
    if not manifest_path.is_file():
        add("MANIFEST_PRESENT", False, str(manifest_path))
        return {"verdict": "FAIL", "checks": checks}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    identity = normalise_identity(evidence)
    add("IDENTITY_EVIDENCE_COMPLETE", identity["proven"], f"missing={identity['missing']}")
    add("IDENTITY_DROPLET", identity["droplet_id"] == expected_droplet_id,
        f"evidence={identity['droplet_id']} expected={expected_droplet_id}")

    source = manifest.get("source") or {}
    proof = str(source.get("identity_proof", "")).lower()
    trusted = proof == TRUSTED_PROOF
    fixture = proof == FIXTURE_PROOF and allow_fixture
    add("PROOF_SOURCE_TRUSTED", trusted or fixture,
        f"identity_proof={proof} trusted={trusted} fixture_allowed={fixture}")
    add("MANIFEST_IDENTITY_MATCHES_EVIDENCE",
        str(source.get("droplet_id")) == identity["droplet_id"] and str(source.get("ip")) == identity["ip"],
        f"manifest={source.get('droplet_id')}/{source.get('ip')} evidence={identity['droplet_id']}/{identity['ip']}")

    cutoff = _parse(manifest.get("consistency_cutoff_utc", ""))
    exported = _parse(manifest.get("exported_at_utc", ""))
    add("CUTOFF_PRESENT", cutoff is not None, str(manifest.get("consistency_cutoff_utc")))
    if cutoff and exported:
        add("CUTOFF_NOT_IN_FUTURE", cutoff <= exported + timedelta(minutes=5),
            f"cutoff={cutoff.isoformat()} exported={exported.isoformat()}")
        add("CUTOFF_FRESH", (datetime.now(timezone.utc) - cutoff) <= timedelta(hours=max_age_hours),
            f"age_hours={(datetime.now(timezone.utc) - cutoff).total_seconds() / 3600:.1f} max={max_age_hours}")
    sampled = _parse(identity["sampled_at_utc"])
    if sampled and cutoff:
        add("EVIDENCE_SAMPLED_NEAR_CUTOFF", abs((sampled - cutoff).total_seconds()) <= max_age_hours * 3600,
            f"sampled={sampled.isoformat()}")

    canonical = manifest.get("canonical") or {}
    add("CANONICAL_DECLARED", bool(canonical), "generation declared" if canonical else "no canonical generation")
    if canonical:
        add("CANONICAL_NOT_SYNTHETIC", canonical.get("synthetic") is not True, f"synthetic={canonical.get('synthetic')}")
        add("CANONICAL_SOURCE_SHA", canonical.get("source_release_sha") == CERTIFIED_SHA,
            str(canonical.get("source_release_sha")))
        add("CANONICAL_EVENTS", int(canonical.get("event_count", 0)) > 0, f"events={canonical.get('event_count')}")

    archive = next(iter(sorted(bundle_dir.glob("*.tar"))), None)
    if archive is None:
        add("ARCHIVE_PRESENT", False, "no archive in the bundle directory")
    else:
        try:
            digest = archive_mod.verify_external_digest(archive)
            add("ARCHIVE_DIGEST", True, digest["sha256"][:12])
        except archive_mod.ArchiveError as exc:
            add("ARCHIVE_DIGEST", False, str(exc))

    listed = sum(len(d.get("files", [])) for d in manifest.get("datasets", []))
    declared = int((manifest.get("totals") or {}).get("files", -1))
    add("DATASET_ENTRIES_COMPLETE", listed == declared, f"listed={listed} declared={declared}")

    failed = [c for c in checks if c["status"] == "FAIL"]
    return {"verdict": "PASS" if not failed else "FAIL", "checks": checks, "identity": identity,
            "reason": (failed[0]["id"] + ": " + failed[0]["detail"]) if failed else "provenance verified"}
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MISSION-07-R1 HF-06 provenance verifier")
    parser.add_argument("--bundle", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--max-age-hours", type=int, default=24)
    parser.add_argument("--allow-fixture", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args()

    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    result = verify_provenance(bundle_dir=Path(args.bundle), evidence=evidence,
                               max_age_hours=args.max_age_hours, allow_fixture=args.allow_fixture)
    text = json.dumps(result, indent=2, sort_keys=True)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

