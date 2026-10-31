# Shared paths for the deploy hooks. Sourced, not executed.
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
data_home=${XDG_DATA_HOME:-"$HOME/.local/share"}
config_home=${XDG_CONFIG_HOME:-"$HOME/.config"}
app_dir="$data_home/5h-bot-pinger"
venv_dir="$app_dir/venv"
config_file="$config_home/5h-bot-pinger/config.toml"
unit_dir="$config_home/systemd/user"
unit_file="$unit_dir/pinger.service"
backup_dir=${PINGER_DEPLOY_BACKUP_DIR:-"$root/.deploy-backups"}

write_unit() {
  mkdir -p "$unit_dir"
  cat > "$unit_file" <<UNIT
[Unit]
Description=Scheduled AI CLI warm-up pings

[Service]
Type=simple
WorkingDirectory=$app_dir
ExecStart=$venv_dir/bin/pinger run --config $config_file
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
UNIT
}

# Fresh venv from the given source directory (replaces any existing venv).
install_venv() {
  rm -rf "$venv_dir"
  mkdir -p "$app_dir"
  python3 -m venv "$venv_dir"
  "$venv_dir/bin/pip" install --quiet "$1"
}
