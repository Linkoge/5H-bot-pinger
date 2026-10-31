#!/bin/sh
set -eu
# Save the first parent revision (the deployed source before this merge) plus
# whether the unit and venv existed, so rollback also works on a first install.
. "$(dirname -- "$0")/common.sh"
mkdir -p "$backup_dir"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
snap="$backup_dir/$stamp"
mkdir "$snap"
git -C "$root" archive HEAD^ | tar -x -C "$snap"
if [ -f "$unit_file" ]; then
  cp "$unit_file" "$snap/.had-unit"
fi
if [ -x "$venv_dir/bin/pinger" ]; then
  : > "$snap/.had-venv"
fi
printf '%s\n' "$snap" > "$backup_dir/latest"
printf 'Saved rollback snapshot in %s\n' "$snap"
