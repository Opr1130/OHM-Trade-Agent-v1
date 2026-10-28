#!/usr/bin/env bash
# O'Pip release source provider.
#
# Sourced by deploy/remote/ohm-deploy. It is the ONLY place that knows how the
# production source tree is obtained, verified, activated and restored, so the
# rest of the controller (compose build, health, writer identity, scheduler
# reconciliation, protection preflight, runtime verifier, receipts, rollback
# sequencing) stays byte-identical for both source models.
#
# Two source models are supported:
#
#   git-worktree  REPO_ROOT is a Git checkout (the historical droplet).
#   release-tree  REPO_ROOT is the certified release-tree layout: no .git, the
#                 running source installed from a verified artifact of an exact
#                 commit (the current production droplet).
#
# Provenance is never assumed. The release-tree model keeps these SEPARATE
# facts and records each one:
#   commit identity   the exact 40-hex commit the artifact was produced from
#   tree identity     `rev-parse <commit>^{tree}` in the trusted object cache
#   artifact digest   SHA-256 of the archive bytes produced from that commit
#   manifest digest   SHA-256 over the sorted `sha256  path` stream
#   install identity  the manifest re-verified against the live application root
#
# Caller contract: REPO_ROOT, APP_ROOT, STATE_DIR, TARGET_SHA and LAST_GOOD_FILE
# are already set, the deploy lock is held, and `set -Eeuo pipefail` is active.

# Bounded, overridable only under the controller's test seam.
OPIP_RELEASE_ROOT="${OPIP_RELEASE_ROOT:-/srv/opip-v2}"
OPIP_SOURCE_CACHE="${OPIP_SOURCE_CACHE:-$OPIP_RELEASE_ROOT/cache/repo.git}"
OPIP_RELEASES_DIR="${OPIP_RELEASES_DIR:-$STATE_DIR/releases}"
OPIP_STAGING_DIR="${OPIP_STAGING_DIR:-$STATE_DIR/staging}"
OPIP_CURRENT_RELEASE="$STATE_DIR/current-release.env"
OPIP_SOURCE_MODE=""
OPIP_STAGED_SHA=""
REPO_OWNER=""
GIT=()

#: Funded-credential NAMES that must never reach the core container. This is a
#: literal mirror of ``app.services.release_profiles.FUNDED_CREDENTIAL_KEYS``;
#: the ATDD contract test asserts both lists are identical, so changing the
#: Python authority fails CI instead of silently widening the core environment.
OPIP_FUNDED_CREDENTIAL_KEYS="KRAKEN_API_KEY KRAKEN_API_SECRET KRAKEN_PRIVATE_KEY KRKN_API_KEY LIVE_TRADING_KEY"

#: F11 protection credential NAMES that the core IS allowed to carry. A literal
#: mirror of ``app.exchanges.kraken_protection_credentials``; the ATDD contract
#: test asserts both name pairs are identical, so the Python authority cannot
#: drift from the provider that materialises the file.
#:
#: These are NOT funded names. F11 protection health is read-only and is not
#: optional (it observes the live account through Balance/OpenPositions), so the
#: dedicated owner-created read-only key crosses the isolation boundary under
#: these two names - and under no others. The runtime then independently proves
#: the key's identity, its exact two scopes and its droplet IP allowlist before
#: F11 may use it, so a value that is not that key is refused rather than
#: trusted. Funded authority still never crosses: the five names above are
#: removed from the core environment unconditionally.
OPIP_F11_READONLY_CREDENTIAL_KEYS="OPIP_F11_KRAKEN_READONLY_API_KEY OPIP_F11_KRAKEN_READONLY_API_SECRET"

opip_core_env_path() {
  # Runtime-only, controller-generated file. Like `.env` and `data/` it is NOT
  # part of the release artifact and MUST stay out of the release manifest:
  # `rt_list_tree` excludes it, so a tree that carries it still re-verifies and
  # the next activation cannot refuse it as "files absent from its manifest".
  printf '%s\n' "$APP_ROOT/.env.core"
}

opip_write_core_env() {
  # Generate the core-only environment from the owner's `.env`, excluding every
  # funded credential name. The owner's `.env` is never modified and no value is
  # ever printed - only the destination path and the number of excluded names.
  #
  # The file is written atomically (temp file on the deploy state filesystem,
  # then rename), is root-owned mode 0600, and is excluded from the Docker build
  # context by `.dockerignore` (`.env.*`). Regenerating with unchanged input is
  # idempotent; a rotated `.env` produces a new file on the next activation.
  local src="$APP_ROOT/.env" dest tmp key
  local -a keys
  dest="$(opip_core_env_path)"
  if [[ ! -f "$src" || -L "$src" || ! -r "$src" ]]; then
    echo "refusing core environment: $src is missing, unreadable or a symlink" >&2
    return 1
  fi
  read -r -a keys <<<"$OPIP_FUNDED_CREDENTIAL_KEYS"
  if ((${#keys[@]} == 0)); then
    echo "refusing core environment: no funded credential names are configured" >&2
    return 1
  fi
  install -d -m 0700 "$STATE_DIR"
  tmp="$(mktemp "$STATE_DIR/.core-env.XXXXXX")" || return 1
  if ! awk -F= -v keys="$OPIP_FUNDED_CREDENTIAL_KEYS" '
      BEGIN { n = split(keys, a, " "); for (i = 1; i <= n; i++) skip[a[i]] = 1 }
      { name = $1; gsub(/^[ \t]+|[ \t]+$/, "", name); if (name in skip) next; print }
    ' "$src" > "$tmp"; then
    rm -f -- "$tmp"
    echo "refusing core environment: filtering $src failed" >&2
    return 1
  fi
  # Defence in depth: a funded name must not survive filtering under any
  # spelling (leading whitespace, padding around the separator).
  for key in "${keys[@]}"; do
    if grep -qE "^[[:space:]]*${key}[[:space:]]*=" "$tmp" 2>/dev/null; then
      rm -f -- "$tmp"
      echo "refusing core environment: ${key} survived filtering" >&2
      return 1
    fi
  done
  # The F11 protection pair is the only exchange credential the core may carry,
  # and it must cross whole. A half pair is a broken rotation: refusing here
  # means the core is never started with a silently disabled read-only client,
  # and the pair is never completed from a funded name. Values are counted, never
  # printed.
  local -a f11_keys
  local f11_present=0 f11_state
  read -r -a f11_keys <<<"$OPIP_F11_READONLY_CREDENTIAL_KEYS"
  for key in "${f11_keys[@]}"; do
    if grep -qE "^[[:space:]]*${key}[[:space:]]*=[[:space:]]*[^[:space:]]" \
      "$tmp" 2>/dev/null; then
      f11_present=$((f11_present + 1))
    fi
  done
  if ((${#f11_keys[@]} > 0)) &&
    ((f11_present != 0)) && ((f11_present != ${#f11_keys[@]})); then
    rm -f -- "$tmp"
    echo "refusing core environment: the F11 read-only credential pair is incomplete" \
      "(${f11_present}/${#f11_keys[@]} present)" >&2
    return 1
  fi
  f11_state=ABSENT
  if ((${#f11_keys[@]} > 0)) && ((f11_present == ${#f11_keys[@]})); then
    f11_state=PRESENT
  fi
  if [[ ! -s "$tmp" ]]; then
    rm -f -- "$tmp"
    echo "refusing core environment: the filtered file is empty" >&2
    return 1
  fi
  chmod 0600 "$tmp"
  chown root:root "$tmp" 2>/dev/null || true
  mv -f -- "$tmp" "$dest" || { rm -f -- "$tmp"; return 1; }
  chmod 0600 "$dest"
  chown root:root "$dest" 2>/dev/null || true
  if [[ "$(stat -c '%a' "$dest")" != "600" ]]; then
    echo "refusing core environment: $dest is not mode 0600" >&2
    return 1
  fi
  echo "OPIP_CORE_ENV=WRITTEN path=$dest excluded_count=${#keys[@]}" \
    "f11_readonly_pair=$f11_state"
}

opip_core_env_mode() {
  # opip_core_env_mode <candidate-tree> -> FILTERED | LEGACY_OWNER_ENV
  #
  # Which environment file the CANDIDATE core service actually consumes. The
  # protection preflight must observe exactly that file, so this is read from the
  # candidate's own `docker-compose.yml`, never assumed from the tree the
  # controller happens to be running in.
  #
  # FILTERED            the core declares `env_file: - .env.core`, so only the
  #                     controller-generated filtered file is authoritative. The
  #                     caller must refuse when that file does not exist; there is
  #                     no fallback to the owner's unfiltered `.env`, because a
  #                     preflight that reads a different environment than the
  #                     runtime proves nothing about the runtime (the reported
  #                     production F11 failure was exactly that).
  # LEGACY_OWNER_ENV    a genuinely verified legacy shape: the core service
  #                     itself declares the owner's `.env`, so the owner's `.env`
  #                     IS the runtime environment and is compatible.
  # nonzero             no provable mode (unreadable compose file, no core
  #                     service block, no `env_file` list, a symlinked file, or
  #                     any other shape). The caller refuses before mutation.
  local tree="${1:-}" compose block
  [[ -n "$tree" ]] || return 1
  compose="${tree%/}/docker-compose.yml"
  [[ -f "$compose" && ! -L "$compose" && -r "$compose" ]] || return 1
  # Extract just the core service block (top-level keys are two-space indented;
  # the service's own keys are deeper), so an `env_file` declared by another
  # service can never be mistaken for the core's.
  block="$(awk '
    /^  [A-Za-z0-9._-]+:[[:space:]]*$/ {
      name = $1; sub(/:$/, "", name); in_core = (name == "ohm-trade-agent"); next
    }
    in_core { print }
  ' "$compose" 2>/dev/null)" || return 1
  [[ -n "$block" ]] || return 1
  if grep -Eq '^[[:space:]]*-[[:space:]]*\.env\.core[[:space:]]*$' <<<"$block"; then
    printf 'FILTERED\n'
    return 0
  fi
  if grep -Eq '^[[:space:]]*-[[:space:]]*\.env[[:space:]]*$' <<<"$block"; then
    printf 'LEGACY_OWNER_ENV\n'
    return 0
  fi
  return 1
}

opip_f11_credential_state() {
  # opip_f11_credential_state <exit-code> <identity> <verdict> <source> <fingerprint>
  #   -> VERIFIED | ABSENT | REFUSED
  #
  # The single decision used by the preflight, the running-core proof and the
  # Tradevisor gate, so no caller can invent a weaker reading of the same job.
  #
  # VERIFIED requires ALL of: a successful process exit, an explicit PASS
  # verdict, a PASS identity, the dedicated protection-pair provenance and a
  # well-formed one-way fingerprint. A `|| true` that lost the exit code, a
  # PASS verdict from a job that then failed Balance/OpenPositions, or a
  # fingerprint that is missing/`NONE` can therefore never be read as success.
  #
  # ABSENT is the explicitly unconfigured posture: the job reports no credential
  # at all (`NONE`) and did not SUCCEED. A broken or half pair reports `INVALID`
  # and is therefore REFUSED, not absent. ABSENT is never a success on its own:
  # opip_f11_release_credential_gate decides which profile may accept it.
  local exit_code="${1:-}" identity="${2:-}" verdict="${3:-}"
  local source="${4:-}" fingerprint="${5:-}"
  [[ "$exit_code" =~ ^[0-9]+$ ]] || { printf 'REFUSED\n'; return 0; }
  if [[ "$exit_code" == "0" && "$verdict" == "PASS" && "$identity" == "PASS" &&
    "$source" == "OPIP_F11_READONLY" && "$fingerprint" =~ ^[0-9a-f]{16}$ ]]; then
    printf 'VERIFIED\n'
    return 0
  fi
  if [[ "$exit_code" != "0" && "$identity" == "UNVERIFIED" &&
    "$verdict" == "FAIL" && "$source" == "NONE" ]]; then
    printf 'ABSENT\n'
    return 0
  fi
  printf 'REFUSED\n'
  return 0
}

opip_f11_fingerprint_parity() {
  # opip_f11_fingerprint_parity <preflight-fingerprint> <runtime-fingerprint>
  #
  # Proves the preflight and the running core resolved the SAME credential and
  # scope set. Missing (`NONE`, empty) or mismatched proof fails: parity is an
  # equality of two verified one-way fingerprints, never an assumption.
  local preflight="${1:-}" runtime="${2:-}"
  [[ "$preflight" =~ ^[0-9a-f]{16}$ ]] || return 1
  [[ "$runtime" =~ ^[0-9a-f]{16}$ ]] || return 1
  [[ "$preflight" == "$runtime" ]] || return 1
  return 0
}

opip_f11_release_credential_gate() {
  # opip_f11_release_credential_gate <release-profile> <state> -> 0 acceptable
  #
  # EVIDENCE_SHADOW is the evidence-plane release profile and REQUIRES a
  # verified read-only protection credential: an absent credential refuses it
  # before mutation. The credential-less exception belongs only to the
  # SAFE_BASELINE rollback posture, which never becomes a deployable release
  # (`validate_candidate_container_identity` refuses it), so it can never be
  # borrowed by an EVIDENCE_SHADOW activation.
  local profile="${1:-}" state="${2:-}"
  case "$profile:$state" in
    EVIDENCE_SHADOW:VERIFIED) return 0 ;;
    SAFE_BASELINE:ABSENT) return 0 ;;
    *) return 1 ;;
  esac
}

opip_source_die() {
  echo "$*" >&2
  exit 69
}

# Detect the source model. A Git checkout always wins, so an existing droplet
# can never be silently reinterpreted as a release tree.
opip_source_init() {
  if [[ -d "$REPO_ROOT/.git" ]]; then
    OPIP_SOURCE_MODE="git-worktree"
    REPO_OWNER="$(stat -c '%U' "$REPO_ROOT/.git")"
    GIT=(sudo -u "$REPO_OWNER" git -C "$REPO_ROOT")
  elif [[ -d "$OPIP_SOURCE_CACHE" ]]; then
    OPIP_SOURCE_MODE="release-tree"
    REPO_OWNER="root"
    GIT=(git --git-dir="$OPIP_SOURCE_CACHE")
  else
    opip_source_die "refusing deploy: neither a Git worktree at $REPO_ROOT nor a release object cache at $OPIP_SOURCE_CACHE exists; run the release-tree controller bootstrap"
  fi
  echo "OPIP_SOURCE_MODE=$OPIP_SOURCE_MODE"
}

# --- release-tree helpers -------------------------------------------------

rt_state_get() {
  # rt_state_get <file> <key>
  [[ -s "$1" ]] || return 1
  sed -n "s/^$2=//p" "$1" | head -n 1
}

rt_write_state() {
  # rt_write_state <file> <sha> <tree> <manifest_digest> <archive_digest>
  local tmp
  tmp="$(mktemp "$STATE_DIR/.current-release.XXXXXX")"
  {
    printf 'schema=1\n'
    printf 'sha=%s\n' "$2"
    printf 'tree=%s\n' "$3"
    printf 'manifest_digest=%s\n' "$4"
    printf 'archive_digest=%s\n' "$5"
    printf 'installed_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$tmp"
  chmod 0644 "$tmp"
  mv -f "$tmp" "$1"
}

rt_manifest_digest() {
  # Digest of the sorted `sha256  path` stream: a stable identity for the tree.
  sha256sum "$1" | awk '{print $1}'
}

rt_list_tree() {
  # Deterministic `sha256  relative-path` stream for a release tree directory.
  # Runtime-only paths are excluded exactly like the M07 install contract.
  #
  # `./.env.core` is the controller-generated filtered core environment
  # (`opip_write_core_env`). It lives in the application root but is never part
  # of the release artifact, so it is excluded here for the same reason `.env`
  # and `data/` are: otherwise the file the controller must create to satisfy
  # `env_file: .env.core` would make the very next activation refuse the tree as
  # carrying "files absent from its manifest".
  local root="$1"
  (
    cd "$root" || exit 1
    find . -type f \
      -not -path './data/*' -not -path './.env' -not -path './.env.core' \
      -not -path './__pycache__/*' \
      -not -name '*.py[cod]' -print0 \
      | LC_ALL=C sort -z \
      | xargs -0 -r sha256sum
  ) | sed 's|\./||'
}

rt_extract_archive() {
  # rt_extract_archive <archive> <dest>
  # Fail closed on any member that is not a plain relative file/directory.
  local archive="$1" dest="$2" listing
  listing="$(tar -tvf "$archive")" || return 1
  if printf '%s\n' "$listing" | awk '{print $1}' | grep -Eq '^[lhcbps]'; then
    echo "refusing release artifact: non-regular archive member" >&2
    return 1
  fi
  if printf '%s\n' "$listing" | grep -Eq '(^| )/|(^| )\.\./'; then
    echo "refusing release artifact: absolute or traversing member path" >&2
    return 1
  fi
  install -d -m 0755 "$dest"
  tar -xf "$archive" -C "$dest" --no-same-owner --no-same-permissions || return 1
  return 0
}

rt_verify_tree() {
  # rt_verify_tree <tree-root> <manifest>
  # Re-verifies every recorded file and refuses any unrecorded source file.
  local root="$1" manifest="$2" listed extra
  ( cd "$root" && sha256sum --quiet -c "$manifest" ) >/dev/null 2>&1 || {
    echo "release tree does not match its manifest" >&2
    return 1
  }
  listed="$(awk '{print $NF}' "$manifest" | LC_ALL=C sort)"
  extra="$(comm -13 <(printf '%s\n' "$listed") \
    <(rt_list_tree "$root" | awk '{print $NF}' | LC_ALL=C sort) || true)"
  if [[ -n "$extra" ]]; then
    echo "release tree carries files absent from its manifest:" >&2
    printf '%s\n' "$extra" >&2
    return 1
  fi
  return 0
}

# --- authoritative remote main --------------------------------------------

rt_remote_main() {
  # Network identity of origin/main: the same second source the Git model uses.
  "${GIT[@]}" ls-remote origin refs/heads/main 2>/dev/null | awk 'NR==1{print $1}'
}

rt_require_exact_commit() {
  "${GIT[@]}" cat-file -e "${TARGET_SHA}^{commit}" 2>/dev/null || {
    echo "refusing deploy: target commit is not present in the trusted object cache" >&2
    return 1
  }
  "${GIT[@]}" rev-parse "${TARGET_SHA}^{tree}" >/dev/null || return 1
}

# --- source-plane integrity (replaces worktree dirty/untracked gates) -----

rt_installed_manifest() {
  local sha
  sha="$(rt_state_get "$OPIP_CURRENT_RELEASE" sha || true)"
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || return 1
  [[ -s "$OPIP_RELEASES_DIR/$sha/manifest.sha256" ]] || return 1
  printf '%s\n' "$OPIP_RELEASES_DIR/$sha/manifest.sha256"
}

rt_require_unmodified_source() {
  # The live source must be exactly the last installed release: the release-tree
  # equivalent of the dirty-worktree and untracked-build-context gates. A hand
  # edited or partially synchronized tree refuses deployment instead of being
  # silently overwritten.
  local manifest
  if ! manifest="$(rt_installed_manifest)"; then
    if [[ -s "$OPIP_CURRENT_RELEASE" ]]; then
      echo "refusing deploy: installed release manifest is missing from $OPIP_RELEASES_DIR" >&2
      return 66
    fi
    echo "OPIP_SOURCE_BASELINE=UNRECORDED"
    return 0
  fi
  if ! rt_verify_tree "$APP_ROOT" "$manifest"; then
    echo "refusing deploy: production source tree does not match the installed release" >&2
    return 66
  fi
  echo "OPIP_SOURCE_BASELINE=VERIFIED"
  return 0
}

# --- previous release retention (rollback vehicle) -----------------------

rt_snapshot_current() {
  # rt_snapshot_current <sha> -> retains the RUNNING source bytes for rollback.
  local sha="$1" dir manifest
  [[ "$sha" =~ ^[0-9a-f]{40}$ ]] || return 1
  dir="$OPIP_RELEASES_DIR/$sha"
  if [[ -s "$dir/source.tar" && -s "$dir/manifest.sha256" ]]; then
    echo "OPIP_ROLLBACK_VEHICLE=RETAINED sha=$sha"
    return 0
  fi
  install -d -m 0700 "$OPIP_RELEASES_DIR" "$dir"
  manifest="$dir/manifest.sha256"
  rt_list_tree "$APP_ROOT" > "$manifest"
  # Same runtime-only exclusions as `rt_list_tree`: the retained vehicle is
  # source bytes, so the owner's `.env` and the controller-generated filtered
  # core environment never enter it (and never break its own re-verification).
  tar -cf "$dir/source.tar" -C "$APP_ROOT" \
    --exclude=./data --exclude=./.env --exclude=./.env.core --exclude=./__pycache__ .
  rt_verify_tree "$APP_ROOT" "$manifest" || {
    echo "refusing deploy: cannot retain the running source as a rollback vehicle" >&2
    return 1
  }
  printf 'sha=%s\nmanifest_digest=%s\nretained_at=%s\n' \
    "$sha" "$(rt_manifest_digest "$manifest")" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    > "$dir/release.env"
  echo "OPIP_ROLLBACK_VEHICLE=RETAINED sha=$sha"
  return 0
}

rt_install_tree() {
  # rt_install_tree <source-app-root> <manifest>
  # Synchronizes source content only: production data, .env and byte-code caches
  # are never read, replaced or deleted.
  local src="$1" manifest="$2"
  command -v rsync >/dev/null 2>&1 || {
    echo "refusing deploy: rsync is required for release-tree activation" >&2
    return 1
  }
  # --checksum is mandatory: a release archive carries the COMMIT timestamp on
  # every file, so two releases that share a size and a commit second would be
  # considered up to date by rsync's size+mtime quick check and silently keep
  # stale source. Content comparison is the only correct test here.
  rsync -a --checksum --delete \
    --exclude=/data --exclude=/.env --exclude=/.env.core --exclude=/__pycache__ \
    --exclude='*.py[cod]' \
    "$src/" "$APP_ROOT/" || return 1
  rt_verify_tree "$APP_ROOT" "$manifest" || return 1
  return 0
}

# --- public API used by the controller -----------------------------------

source_prepare() {
  # Release-tree model only. The Git model keeps its original inline gates in
  # the controller, byte-identical to the historical droplet behaviour.
  install -d -m 0700 "$OPIP_RELEASES_DIR" "$OPIP_STAGING_DIR"
  "${GIT[@]}" fetch --prune --no-tags origin main >/dev/null 2>&1 || {
    echo "refusing deploy: cannot reach the source origin from the object cache" >&2
    exit 65
  }
  local remote_main staged tree archive manifest recorded
  remote_main="$(rt_remote_main)"
  if [[ "$TARGET_SHA" != "$remote_main" ]]; then
    echo "refusing deploy: target is not current origin/main" >&2
    echo "target=$TARGET_SHA origin/main=$remote_main" >&2
    exit 65
  fi
  rt_require_exact_commit || exit 65
  rt_require_unmodified_source || exit "$?"

  staged="$OPIP_STAGING_DIR/$TARGET_SHA"
  rm -rf "$staged"
  install -d -m 0700 "$staged"
  archive="$staged/release.tar"
  "${GIT[@]}" archive --format=tar "$TARGET_SHA" > "$archive" || {
    echo "refusing deploy: cannot produce the exact release artifact" >&2
    exit 69
  }
  rt_extract_archive "$archive" "$staged/tree" || exit 69
  tree="$staged/tree/OHM-Trade-Agent-v1"
  [[ -s "$tree/docker-compose.yml" ]] || {
    echo "refusing deploy: artifact does not carry the application tree" >&2
    exit 69
  }
  manifest="$staged/manifest.sha256"
  rt_list_tree "$tree" > "$manifest"
  rt_verify_tree "$tree" "$manifest" || exit 69
  printf 'schema=1\nsha=%s\ntree=%s\narchive_digest=%s\nmanifest_digest=%s\nfile_count=%s\n' \
    "$TARGET_SHA" "$("${GIT[@]}" rev-parse "${TARGET_SHA}^{tree}")" \
    "$(sha256sum "$archive" | awk '{print $1}')" "$(rt_manifest_digest "$manifest")" \
    "$(grep -c . "$manifest")" > "$staged/provenance.env"

  PREVIOUS_SHA="$(rt_state_get "$OPIP_CURRENT_RELEASE" sha || true)"
  if [[ -s "$LAST_GOOD_FILE" ]]; then
    recorded="$(cat "$LAST_GOOD_FILE")"
    [[ "$recorded" =~ ^[0-9a-f]{40}$ ]] && PREVIOUS_SHA="$recorded"
  fi
  [[ "$PREVIOUS_SHA" =~ ^[0-9a-f]{40}$ ]] || {
    echo "refusing deploy: no verifiable previous release is recorded for rollback" >&2
    exit 69
  }
  rt_snapshot_current "$PREVIOUS_SHA" || exit 69
  OPIP_STAGED_SHA="$TARGET_SHA"
  echo "OPIP_SOURCE_COMMIT=$TARGET_SHA"
  echo "OPIP_SOURCE_ARTIFACT_SHA256=$(sed -n 's/^archive_digest=//p' "$staged/provenance.env")"
  echo "OPIP_SOURCE_MANIFEST_SHA256=$(sed -n 's/^manifest_digest=//p' "$staged/provenance.env")"
}

source_materialize_candidate() {
  # source_materialize_candidate <dest> -> <dest>/OHM-Trade-Agent-v1/app
  # Release-tree model only: the candidate tree is an extraction of the verified
  # artifact, so the live application tree is never touched before preflight PASS.
  local dest="$1"
  rm -rf "$dest"
  rt_extract_archive "$OPIP_STAGING_DIR/$TARGET_SHA/release.tar" "$dest" || {
    rm -rf "$dest"
    return 1
  }
  return 0
}

source_activate_target() {
  # Release-tree activation: verified install of the staged artifact.
  local staged tree manifest tree_sha archive_digest
  staged="$OPIP_STAGING_DIR/$TARGET_SHA"
  tree="$staged/tree/OHM-Trade-Agent-v1"
  manifest="$staged/manifest.sha256"
  if [[ ! -d "$tree" || ! -s "$manifest" ]]; then
    echo "refusing deploy: staged release for $TARGET_SHA is incomplete" >&2
    return 1
  fi
  rt_verify_tree "$tree" "$manifest" || return 1
  tree_sha="$(sed -n 's/^tree=//p' "$staged/provenance.env")"
  archive_digest="$(sed -n 's/^archive_digest=//p' "$staged/provenance.env")"
  install -d -m 0700 "$OPIP_RELEASES_DIR/$TARGET_SHA"
  cp -f "$manifest" "$OPIP_RELEASES_DIR/$TARGET_SHA/manifest.sha256"
  printf 'sha=%s\ntree=%s\nmanifest_digest=%s\ninstalled_at=%s\n' \
    "$TARGET_SHA" "$tree_sha" "$(rt_manifest_digest "$manifest")" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OPIP_RELEASES_DIR/$TARGET_SHA/release.env"
  rt_install_tree "$tree" "$manifest" || return 1
  rt_write_state "$OPIP_CURRENT_RELEASE" "$TARGET_SHA" "$tree_sha" \
    "$(rt_manifest_digest "$manifest")" "$archive_digest"
  echo "OPIP_RELEASE_INSTALLED=$TARGET_SHA"
  return 0
}

source_restore_previous() {
  # Release-tree rollback: the retained previous release, never `git reset`.
  local dir work
  dir="$OPIP_RELEASES_DIR/$PREVIOUS_SHA"
  if [[ ! -s "$dir/source.tar" || ! -s "$dir/manifest.sha256" ]]; then
    echo "refusing rollback: retained previous release $PREVIOUS_SHA is incomplete" >&2
    return 1
  fi
  work="$(mktemp -d "$OPIP_STAGING_DIR/rollback.XXXXXX")" || return 1
  if ! tar -xf "$dir/source.tar" -C "$work"; then
    rm -rf "$work"
    return 1
  fi
  if ! rt_verify_tree "$work" "$dir/manifest.sha256"; then
    rm -rf "$work"
    return 1
  fi
  if ! rt_install_tree "$work" "$dir/manifest.sha256"; then
    rm -rf "$work"
    return 1
  fi
  rm -rf "$work"
  rt_write_state "$OPIP_CURRENT_RELEASE" "$PREVIOUS_SHA" "-" \
    "$(rt_manifest_digest "$dir/manifest.sha256")" "-"
  echo "OPIP_RELEASE_RESTORED=$PREVIOUS_SHA"
  return 0
}

source_release_head() {
  # The controller's "which release is installed" identity check.
  rt_state_get "$OPIP_CURRENT_RELEASE" sha
}


