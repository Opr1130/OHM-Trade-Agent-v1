#!/usr/bin/env bash
# Linux ATDD for the release-artifact path.
#
# Runs on a disposable Ubuntu runner (GitHub-hosted) with no production access.
# It exercises the artifact CONTRACT end to end against a temporary fixture tree
# and asserts every negative case fails closed. It does not start production
# containers and never contacts a registry.
#
#   bash deploy/release/atdd/run-artifact-atdd.sh
set -uo pipefail
LC_ALL=C
export LC_ALL

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_ROOT="$(cd "$HERE/../.." && pwd)"
TOOL="$APP_ROOT/deploy/release/release_artifact.py"
PY="${PYTHON:-python3}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0
declare -a FAILED=()

ok()  { PASS=$((PASS+1)); printf '  PASS  %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); FAILED+=("$1"); printf '  FAIL  %s (%s)\n' "$1" "$2"; }
sec() { printf '\n== %s ==\n' "$1"; }

SHA="fde262e82516deaa282c05fde30ab898f3510446"
OTHER="c196dd674678b3b1ee91c351ee97c1c9e2b3938d"
APP_REF="ghcr.io/opip/app@sha256:$(printf 'a%.0s' {1..64})"
WRITER_REF="ghcr.io/opip/writer@sha256:$(printf 'b%.0s' {1..64})"
APP_ID="sha256:$(printf 'c%.0s' {1..64})"
WRITER_ID="sha256:$(printf 'd%.0s' {1..64})"

expect_ok() { local name="$1"; shift
  if "$@" >"$WORK/out" 2>&1; then ok "$name"; else bad "$name" "$(tail -2 "$WORK/out" | tr '\n' ' ')"; fi; }
expect_fail() { local name="$1"; shift
  if "$@" >"$WORK/out" 2>&1; then bad "$name" "unexpected success"; else ok "$name"; fi; }

sec "tool availability"
expect_ok "release tool parses" "$PY" -c "import ast;ast.parse(open('$TOOL',encoding='utf-8').read())"

sec "release bundle (D9: host-executed inputs)"
BUNDLE="$WORK/bundle"
mkdir -p "$BUNDLE"
expect_ok "bundle builds from the release tree" \
  "$PY" "$TOOL" build-bundle --root "$APP_ROOT" --release-id "$SHA" --out "$BUNDLE" \
    --app-ref "$APP_REF" --writer-ref "$WRITER_REF" \
    --app-revision "$SHA" --writer-revision "$OTHER"
expect_ok "bundle verifies" "$PY" "$TOOL" verify-bundle --dir "$BUNDLE"
expect_ok "on-host compose inputs match the bundle" \
  "$PY" "$TOOL" check-config --bundle "$BUNDLE" --root "$APP_ROOT"
expect_ok "allowlisted host script resolves inside the bundle" \
  "$PY" "$TOOL" host-script --bundle "$BUNDLE" --name deploy/remote/reconcile-scheduler.sh
expect_fail "non-allowlisted script is refused" \
  "$PY" "$TOOL" host-script --bundle "$BUNDLE" --name deploy/remote/not-real.sh

printf '\n# tampered\n' >> "$BUNDLE/deploy/remote/reconcile-scheduler.sh"
expect_fail "tampered host script is refused" "$PY" "$TOOL" verify-bundle --dir "$BUNDLE"
cp "$APP_ROOT/deploy/remote/reconcile-scheduler.sh" "$BUNDLE/deploy/remote/reconcile-scheduler.sh"
chmod 0750 "$BUNDLE/deploy/remote/reconcile-scheduler.sh"
expect_ok "restored bundle verifies again" "$PY" "$TOOL" verify-bundle --dir "$BUNDLE"

sec "receipts and rollback eligibility"
RECEIPT="$WORK/receipts/current.json"
write_receipt() { # path provenance ref [seq]
  "$PY" "$TOOL" write-receipt --path "$1" --sequence "${4:-1}" --release-id "$SHA" \
    --target-sha "$SHA" --stage VERIFIED \
    --app-ref "$APP_REF" --app-image-id "$APP_ID" --app-revision "$SHA" \
    --writer-ref "$WRITER_REF" --writer-image-id "$WRITER_ID" --writer-revision "$OTHER" \
    --pair-id "pair-$SHA-$OTHER" --core-ok 1 --writer-ok 1 --profile EVIDENCE_SHADOW \
    --provenance-kind "$2" --provenance-reference "$3"
}
expect_ok "registry-pinned VERIFIED receipt is written" write_receipt "$RECEIPT" registry "$APP_REF"
expect_ok "registry-pinned receipt is rollback-eligible" \
  "$PY" "$TOOL" rollback-eligibility --path "$RECEIPT"
expect_fail "non-monotonic sequence is refused" write_receipt "$RECEIPT" registry "$APP_REF" 1

LOCAL="$WORK/receipts/local.json"
expect_ok "local-store receipt is recorded (not promoted)" \
  write_receipt "$LOCAL" local_store "$APP_ID"
expect_fail "local-store provenance is not rollback-eligible" \
  "$PY" "$TOOL" rollback-eligibility --path "$LOCAL"
grep -q 'BLOCKED_STORAGE' "$WORK/out" && ok "blocked reason is BLOCKED_STORAGE" \
  || bad "blocked reason is BLOCKED_STORAGE" "reason missing"

sec "registry identity vs local config id"
expect_ok "matching registry digest accepted" \
  "$PY" "$TOOL" registry-digest --expected "$APP_ID" \
    --repo-digest "ghcr.io/opip/app@$APP_ID" --config-id "$WRITER_ID"
expect_fail "config id used as registry digest is refused" \
  "$PY" "$TOOL" registry-digest --expected "$WRITER_ID" \
    --repo-digest "ghcr.io/opip/app@$WRITER_ID" --config-id "$WRITER_ID"
expect_fail "mismatched repo digest is refused" \
  "$PY" "$TOOL" registry-digest --expected "$APP_ID" \
    --repo-digest "ghcr.io/opip/app@$WRITER_ID" --config-id "$APP_ID"

sec "corrupt / absent state fails closed"
printf '{"schema":"opip-release-receipt-v1","sequence":1,"app":{' > "$WORK/receipts/trunc.json"
expect_fail "truncated receipt refused" "$PY" "$TOOL" validate --path "$WORK/receipts/trunc.json"
expect_fail "absent receipt refused" "$PY" "$TOOL" validate --path "$WORK/receipts/absent.json"

sec "controller is Git-free in artifact mode"
CTRL="$APP_ROOT/deploy/remote/ohm-deploy"
expect_ok "controller parses under Linux bash" bash -n "$CTRL"
grep -q 'GIT=(false)' "$CTRL" && ok "no-.git tripwire present" || bad "no-.git tripwire" "missing"
grep -q -- '--no-build' "$CTRL" && ok "implicit build forbidden" || bad "no-build flag" "missing"
grep -q 'OPIP_DEPLOY_ARTIFACT_MODE' "$CTRL" && ok "artifact mode explicit, default off" || bad "mode switch" "missing"
if awk '/artifact_apply_pair "\$ROLLBACK_APP_REF"/,/elif docker compose/' "$CTRL" | grep -q 'compose build'; then
  bad "rollback artifact branch rebuilds" "build found"
else
  ok "rollback artifact branch never rebuilds"
fi

sec "result"
printf 'TOTAL: pass=%d fail=%d skip=0\n' "$PASS" "$FAIL"
if [ "$FAIL" -ne 0 ]; then printf 'failed: %s\n' "${FAILED[*]}"; exit 1; fi
exit 0
