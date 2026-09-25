#!/usr/bin/env bash
#
# O'Pip Intelligence Committee — provision the isolated host runtime (Module 2H).
#
# RUNS ON THE LEARNING/ANALYTICS PLANE. Installs the exact release tree at
# /opt/opip/app and a virtualenv at /opt/opip/venv whose dependencies come only
# from the committed requirements.txt. Mode is forced OFF. The timer is left
# disabled. Provider credential values are preserved and never printed.
#
# Usage:
#   provision-committee-host-runtime.sh <40-char-release-sha>
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=committee-host-runtime-lib.sh
source "$SCRIPT_DIR/committee-host-runtime-lib.sh"

TARGET_SHA="${1:-}"
if [[ "$#" -ne 1 ]]; then
  echo "usage: $0 <40-char-release-sha>" >&2
  exit 64
fi
require_exact_sha "$TARGET_SHA" || exit $?
runtime_paths_init
provision_main "$TARGET_SHA" "$SCRIPT_DIR"
