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

# Directory holding the claude CLI: PATH first, then the usual install spots.
find_claude_dir() {
  found=$(command -v claude 2>/dev/null || true)
  if [ -z "$found" ]; then
    for candidate in "$HOME/.local/bin/claude" "$HOME/.claude/local/claude"; do
      if [ -x "$candidate" ]; then found=$candidate; break; fi
    done
  fi
  if [ -n "$found" ]; then dirname -- "$found"; fi
}

write_unit() {
  mkdir -p "$unit_dir"
  # systemd user services get a minimal environment: set HOME and a PATH that finds claude.
  claude_dir=$(find_claude_dir)
  if [ -z "$claude_dir" ]; then
    echo "WARNING: claude CLI not found; install it and log in, then redeploy" >&2
  fi
  unit_path="${claude_dir:+$claude_dir:}$HOME/.local/bin:$HOME/.claude/local:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  # systemd treats % specially in unit files.
  unit_path=$(printf '%s' "$unit_path" | sed 's/%/%%/g')
  unit_home=$(printf '%s' "$HOME" | sed 's/%/%%/g')
  cat > "$unit_file" <<UNIT
[Unit]
Description=Scheduled AI CLI warm-up pings

[Service]
Type=simple
WorkingDirectory=$app_dir
Environment="HOME=$unit_home"
Environment="PATH=$unit_path"
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
