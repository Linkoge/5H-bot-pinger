#!/bin/sh
set -eu
systemctl --user is-active --quiet pinger.service
printf 'pinger.service is active\n'
