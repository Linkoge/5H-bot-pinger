#!/bin/sh
set -eu
. "$(dirname -- "$0")/common.sh"
systemctl --user is-active --quiet pinger.service
"$venv_dir/bin/pinger" schedule --config "$config_file" >/dev/null
printf 'pinger.service is active and the schedule loads\n'
