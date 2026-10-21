#!/usr/bin/env bash
# Normalize evidence-export readability for the least-privilege reader account.
#
# Contract: the reader group may traverse export directories and read export
# files, but never write them. Hidden staging directories are deliberately left
# alone: the exporter owns their lifecycle and they are not published evidence.
#
# This exists because `install -d -m 0750` only sets the mode of directories it
# creates. When the reader account is introduced after the export tree already
# exists, pre-existing nested directories stay 0700 and an otherwise correctly
# authorized reader cannot traverse into them.
set -Eeuo pipefail

EXPORT_ROOT="${1:-}"
READER_GROUP="${2:-}"

if [[ -z "$EXPORT_ROOT" || -z "$READER_GROUP" ]]; then
  echo "usage: $0 <export-root> <reader-group>" >&2
  exit 64
fi
if [[ ! -d "$EXPORT_ROOT" ]]; then
  echo "export root does not exist: $EXPORT_ROOT" >&2
  exit 66
fi
if [[ -L "$EXPORT_ROOT" ]]; then
  echo "refusing symlinked export root: $EXPORT_ROOT" >&2
  exit 67
fi

chgrp "$READER_GROUP" "$EXPORT_ROOT"
chmod 0750 "$EXPORT_ROOT"

# find is intentionally symlink-safe (no -L / -follow): a symlink inside the
# export tree is never chgrp/chmod'ed and can never grant access to a target
# outside the export root. Symlinked entries simply remain unreadable.
while IFS= read -r -d '' dir; do
  chgrp "$READER_GROUP" "$dir"
  chmod 0750 "$dir"
done < <(find "$EXPORT_ROOT" -mindepth 1 -type d -not -path '*/.*' -print0)

while IFS= read -r -d '' file; do
  chgrp "$READER_GROUP" "$file"
  chmod 0640 "$file"
done < <(find "$EXPORT_ROOT" -mindepth 1 -type f -not -path '*/.*' -print0)

# The reader takes a shared read lock on this hidden file, so it must be
# group-readable even though other hidden staging paths are not published.
if [[ -f "$EXPORT_ROOT/.publish.lock" ]]; then
  chgrp "$READER_GROUP" "$EXPORT_ROOT/.publish.lock"
  chmod 0640 "$EXPORT_ROOT/.publish.lock"
fi

echo "O'Pip learning reader permissions normalized."
echo "export_root=$EXPORT_ROOT"
echo "reader_group=$READER_GROUP"
