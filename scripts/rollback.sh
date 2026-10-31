#!/bin/sh
set -eu
. "$(dirname -- "$0")/common.sh"
latest="$backup_dir/latest"
if [ ! -f "$latest" ]; then
  echo 'No deployment backup is available' >&2
  exit 1
fi
snap=$(cat "$latest")
if [ ! -f "$snap/pyproject.toml" ]; then
  echo "No rollback source found in $snap" >&2
  exit 1
fi
if [ -f "$snap/.had-venv" ]; then
  install_venv "$snap"
  if [ -f "$snap/.had-unit" ]; then
    mkdir -p "$unit_dir"
    cp "$snap/.had-unit" "$unit_file"
  else
    write_unit
  fi
  systemctl --user daemon-reload
  systemctl --user restart pinger.service
else
  # First install: remove what the deploy added; "not loaded" is not an error.
  systemctl --user stop pinger.service || true
  systemctl --user disable pinger.service || true
  rm -f "$unit_file"
  rm -rf "$venv_dir"
  systemctl --user daemon-reload || true
  echo 'Removed first-install unit and venv'
fi
