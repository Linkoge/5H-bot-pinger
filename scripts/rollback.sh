#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
backup_dir=${PINGER_DEPLOY_BACKUP_DIR:-"$root/.deploy-backups"}
latest="$backup_dir/latest"
if [ ! -f "$latest" ]; then
  echo 'No deployment backup is available' >&2
  exit 1
fi
wheel_dir=$(cat "$latest")
if [ ! -f "$wheel_dir/pyproject.toml" ]; then
  echo "No rollback source found in $wheel_dir" >&2
  exit 1
fi
python3 -m pip install --user --force-reinstall "$wheel_dir"
systemctl --user restart pinger.service
