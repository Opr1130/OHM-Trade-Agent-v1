#!/usr/bin/env bash
set -Eeuo pipefail

JOB="${1:-}"
case "$JOB" in
  capture|outcomes) ;;
  *)
    echo "unsupported O'Pip cleanup job: $JOB" >&2
    exit 64
    ;;
esac

command -v docker >/dev/null 2>&1 || exit 0
LABEL="com.opip.learning.job=$JOB"
mapfile -t ids < <(docker ps -aq --filter "label=$LABEL")
if (( ${#ids[@]} > 0 )); then
  docker rm -f "${ids[@]}" >/dev/null 2>&1 || true
fi

remaining="$(docker ps -aq --filter "label=$LABEL" | wc -l)"
if [[ "$remaining" != "0" ]]; then
  echo "O'Pip cleanup could not reap all $JOB containers" >&2
  exit 1
fi

echo "O'Pip learning cleanup: job=$JOB clean"
