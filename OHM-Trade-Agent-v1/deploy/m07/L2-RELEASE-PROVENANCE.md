# L-02 — Release provenance for a deliberately Git-free extraction

## Why this exists

The V2 release is installed as a plain tree at

```
/srv/opip-v2/releases/c196dd674678b3b1ee91c351ee97c1c9e2b3938d/OHM-Trade-Agent-v1
```

It has **no `.git`** and **no `release-manifest.sha256`**. L-02 as originally
written (`git rev-parse HEAD` + manifest) therefore cannot be satisfied by that
valid layout. This document defines the replacement evidence contract. It is a
read-only equivalence proof, not a new artifact on the host.

## What must not happen

* No `.git` directory, manifest or other file is written onto V2 to make
  readiness pass.
* A manifest generated from the candidate V2 files is never evidence of their
  authenticity.
* The expected inventory must come from a trusted checkout or an approved archive
  of the pinned commit, never from the host under test.

## Tool

`scripts/opip_release_provenance.py` mirrors `bootstrap-v2.sh manifest_stream`
exactly (provenance excludes `.git/`, `data/`, `.env`, `__pycache__/`), records
every regular file with SHA-256 and byte count, records symlinks separately, and
classifies excluded paths as permitted runtime-only files.

| Command | Purpose | Exit codes |
|---|---|---|
| `build --root <trusted> --out expected.json` | expected inventory from a clean Git checkout whose `HEAD` is the pinned SHA | 0 PASS, 2 INSUFFICIENT, 3 REFUSED |
| `build --out expected.json --provenance git-archive --archive <file> --archive-sha256 <digest>` | expected inventory derived by safe isolated extraction of the **verified archive bytes**; `--root` is refused | 0 PASS, 2 INSUFFICIENT, 3 REFUSED |
| `inventory --root <host-release>` | actual inventory; never trusted by itself; `--out` optional so a probe can print to stdout | 0 |
| `compare --expected --actual` | full path-by-path equivalence | 0 PASS, 1 FAIL, 3 REFUSED |

`build` refuses a dirty checkout, a non-Git tree, an archive digest that does not
match the approved value, an archive plus a candidate `--root`, an unpinned SHA in
production mode, unsafe archive members, and any `candidate` provenance.
`compare` refuses an expected inventory that is not trusted, whose certified SHA
is not the pin, whose recorded manifest digest does not match its contents, whose
archive provenance is not bound to verified archive bytes, or an actual inventory
that claims authenticity.

## Archive trust boundary

For `git-archive`, the approved digest authenticates **only the archive**. The
expected inventory is therefore computed from an extraction of those bytes into a
private temporary directory, never from an operator-supplied `--root`. Supplying
both is refused, so a candidate release cannot authenticate itself against an
unrelated approved archive.

Extraction is manual and fail-closed:

* digest verified before any member is read;
* tar and zip are supported; anything else is refused;
* absolute paths, drive letters, backslashes, `.`/`..`, empty segments and
  control characters are refused before writing;
* tar symlinks, hardlinks, devices and other special members are refused; zip
  symlink/special mode bits are refused;
* duplicate members (case-insensitive) are refused;
* member count and extracted-byte bounds are enforced;
* every write is re-checked to remain inside the private extraction root.

## Recorded evidence

`expected.json` records:

* `certified_sha` (must be `c196dd67…`),
* `manifest_digest` — SHA-256 over the sorted `sha256  path` stream,
* `counts.source_files` / `source_bytes` / `source_symlinks`,
* `provenance` — `git-checkout` with `head` and `clean`, or `git-archive` with
  `derived_from: archive-bytes`, `extraction: isolated-tempdir`, the approved
  digest, format and member counts,
* the exact policy id and exclusions.

`compare` output records missing, unexpected-added, changed, missing/added/
changed symlinks, permitted runtime-only files, both provenance records and the
verdict. A PASS means the host source tree is byte-identical to the trusted
inventory at the pinned commit; it does **not** by itself attest the host
identity, the image or the release installation path.

## Future authorised probe (not run by this increment)

The probe is read-only when the inventory is printed to stdout and captured by
the operator; nothing is written on V2 and the toolkit need not be installed:

```bash
# operator workstation, after a checked-out trusted tree at c196dd67 is available
python deploy/m07/scripts/opip_release_provenance.py build \
  --root <trusted-checkout>/OHM-Trade-Agent-v1 --out expected.json

# one read-only privileged observation (stdout only; no host write)
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes opipadmin@<v2> \
  'sudo -n python3 - --root /srv/opip-v2/releases/c196dd67…/OHM-Trade-Agent-v1 \
     --host <host> --droplet-id 610307364 --privilege "sudo -n"' \
  < deploy/m07/scripts/opip_release_provenance.py > actual.json

python deploy/m07/scripts/opip_release_provenance.py compare \
  --expected expected.json --actual actual.json
```

This execution requires fresh owner authorisation. It is **not** part of the
corrective PR's CI and was **not** run against V2.

## Limits

* `git-archive` provenance trusts the approved archive digest; it does not
  re-derive the commit graph from the archive.
* `inventory` must read the root-only release through `sudo -n`; the observation
  records `privilege` but cannot prove the caller.
* A hash-equivalent tree proves content identity, not installation history.
