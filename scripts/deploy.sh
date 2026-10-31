#!/bin/sh
set -eu
. "$(dirname -- "$0")/common.sh"
cd "$root"

install_venv "$root"

# Create the real config only if missing; owner has no Codex subscription.
if [ ! -e "$config_file" ]; then
  mkdir -p "$(dirname -- "$config_file")"
  awk -v logpath="$app_dir/pinger.log" -v statepath="$app_dir/.pinger-state.json" '
    /^\[/ { section = $0 }
    /^log_file[ \t]*=/ { print "log_file = \"" logpath "\""; next }
    /^state_file[ \t]*=/ { print "state_file = \"" statepath "\""; next }
    section == "[codex]" && /^enabled[ \t]*=/ { print "enabled = false"; next }
    { print }
  ' config.example.toml > "$config_file"
  echo "Created $config_file with codex disabled"
fi

write_unit
systemctl --user daemon-reload
systemctl --user enable pinger.service
systemctl --user restart pinger.service

# Lingering lets the service run without a login session; never fatal.
if command -v loginctl >/dev/null 2>&1; then
  if loginctl enable-linger "$(id -un)" </dev/null >/dev/null 2>&1; then
    echo 'Lingering enabled'
  else
    echo "WARNING: could not enable lingering; run: loginctl enable-linger $(id -un)" >&2
  fi
fi
