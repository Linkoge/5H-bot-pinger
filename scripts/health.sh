#!/bin/sh
set -eu
. "$(dirname -- "$0")/common.sh"
systemctl --user is-active --quiet pinger.service

# The service must be able to find claude using the PATH written into its unit.
unit_path=$(sed -n 's/^Environment="PATH=\(.*\)"$/\1/p' "$unit_file" | head -n 1)
found=
old_ifs=$IFS
IFS=:
for dir in $unit_path; do
  if [ -n "$dir" ] && [ -x "$dir/claude" ]; then found="$dir/claude"; break; fi
done
IFS=$old_ifs
if [ -z "$found" ]; then
  echo "claude CLI not found on the unit's PATH ($unit_path)" >&2
  exit 1
fi

"$venv_dir/bin/pinger" schedule --config "$config_file" >/dev/null
printf 'pinger.service is active, claude found at %s, and the schedule loads\n' "$found"
