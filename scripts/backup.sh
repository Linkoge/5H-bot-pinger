#!/bin/sh
set -eu

# Save the first parent revision, which is the deployed source before this merge.
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${PINGER_DEPLOY_BACKUP_DIR:-"$root/.deploy-backups"}
mkdir -p "$backup_dir"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
wheel_dir="$backup_dir/$stamp"
mkdir "$wheel_dir"
git -C "$root" archive HEAD^ | tar -x -C "$wheel_dir"
printf '%s\n' "$wheel_dir" > "$backup_dir/latest"
printf 'Saved rollback wheel in %s\n' "$wheel_dir"
