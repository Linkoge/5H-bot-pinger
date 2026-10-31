import json
import os
import shutil
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def make_checkout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for text in ("old", "new"):
        (repo / "pyproject.toml").write_text(f"{text} revision\n")
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", text], check=True)
    shutil.copytree(ROOT / "scripts", repo / "scripts")
    shutil.copy(ROOT / "config.example.toml", repo / "config.example.toml")
    return repo


def fake_commands(tmp_path, *, active=True, load_failure=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "commands.log"
    systemctl = bindir / "systemctl"
    systemctl.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$COMMAND_LOG\"\n" + ("exit 0\n" if active else "exit 1\n"))
    loginctl = bindir / "loginctl"
    loginctl.write_text("#!/bin/sh\nprintf 'loginctl %s\\n' \"$*\" >> \"$COMMAND_LOG\"\nexit 1\n")
    python = bindir / "python3"
    python.write_text(
        "#!/bin/sh\nprintf 'python3 %s\\n' \"$*\" >> \"$COMMAND_LOG\"\n"
        "mkdir -p \"$3/bin\"\n"
        "printf '#!/bin/sh\\nprintf \"pip %s\\\\n\" \"$*\" >> \"$COMMAND_LOG\"\\n' '$*' > \"$3/bin/pip\"\n"
        "printf '#!/bin/sh\\nprintf \"pinger %s\\\\n\" \"$*\" >> \"$COMMAND_LOG\"\\n' '$*' > \"$3/bin/pinger\"\n"
        "chmod +x \"$3/bin/pip\" \"$3/bin/pinger\"\n")
    for command in (systemctl, loginctl, python):
        command.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ | {"PATH": f"{bindir}:{os.environ['PATH']}", "COMMAND_LOG": str(log), "HOME": str(home)}
    for var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        env.pop(var, None)
    return env, log


def run_script(repo, name, env, *, check=True):
    return subprocess.run(["sh", str(repo / "scripts" / name)], cwd=repo, env=env, text=True, capture_output=True, check=check, timeout=10)


def test_deploy_configuration_uses_documented_service_and_hooks():
    config = json.loads((ROOT / ".linko-deploy.json").read_text())
    assert config["service"] == "pinger.service"
    for key in ("backup_command", "health_command", "rollback_command"):
        assert isinstance(config[key], list)
        assert config[key][0].startswith("scripts/")


def test_deploy_installs_venv_unit_and_bootstraps_config(tmp_path):
    repo = make_checkout(tmp_path)
    env, log = fake_commands(tmp_path / "commands")
    home = Path(env["HOME"])
    result = run_script(repo, "deploy.sh", env)
    commands = log.read_text()
    venv = home / ".local/share/5h-bot-pinger/venv"
    assert f"python3 -m venv {venv}" in commands
    assert "--user" not in commands.split("pip", 1)[-1].splitlines()[0]
    unit = (home / ".config/systemd/user/pinger.service").read_text()
    assert f"ExecStart={venv}/bin/pinger run --config {home}/.config/5h-bot-pinger/config.toml" in unit
    assert "Restart=always" in unit and "RestartSec=30" in unit and "WantedBy=default.target" in unit
    assert commands.index("--user daemon-reload") < commands.index("--user enable pinger.service") < commands.index("--user restart pinger.service")
    assert "loginctl enable-linger" in commands and "WARNING" in result.stderr
    config = tomllib.loads((home / ".config/5h-bot-pinger/config.toml").read_text())
    assert config["codex"]["enabled"] is False and config["claude"]["enabled"] is True
    assert config["log_file"].startswith(str(home))


def test_deploy_never_overwrites_existing_config(tmp_path):
    repo = make_checkout(tmp_path)
    env, _ = fake_commands(tmp_path / "commands")
    config = Path(env["HOME"]) / ".config/5h-bot-pinger/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("[codex]\nenabled = true\n")
    run_script(repo, "deploy.sh", env)
    assert config.read_text() == "[codex]\nenabled = true\n"


def test_backup_and_rollback_restore_previous_install(tmp_path):
    repo = make_checkout(tmp_path)
    backup = tmp_path / "backups"
    env, log = fake_commands(tmp_path / "commands")
    env["PINGER_DEPLOY_BACKUP_DIR"] = str(backup)
    run_script(repo, "deploy.sh", env)
    unit = Path(env["HOME"]) / ".config/systemd/user/pinger.service"
    unit.write_text(unit.read_text() + "# previous\n")
    run_script(repo, "backup.sh", env)
    snap = Path((backup / "latest").read_text().strip())
    assert (snap / "pyproject.toml").read_text() == "old revision\n"
    assert (snap / ".had-venv").exists() and (snap / ".had-unit").exists()
    unit.write_text("changed\n")
    log.write_text("")
    run_script(repo, "rollback.sh", env)
    assert unit.read_text().endswith("# previous\n")
    assert f"pip install --quiet {snap}" in log.read_text()
    assert "--user restart pinger.service" in log.read_text()


def test_rollback_on_first_install_removes_unit_and_venv(tmp_path):
    repo = make_checkout(tmp_path)
    backup = tmp_path / "backups"
    env, log = fake_commands(tmp_path / "commands")
    env["PINGER_DEPLOY_BACKUP_DIR"] = str(backup)
    run_script(repo, "backup.sh", env)
    snap = Path((backup / "latest").read_text().strip())
    assert not (snap / ".had-venv").exists() and not (snap / ".had-unit").exists()
    run_script(repo, "deploy.sh", env)
    # systemctl reports "not loaded" for stop/disable: rollback must tolerate it.
    failing_env, failing_log = fake_commands(tmp_path / "failing", active=False)
    failing_env["PINGER_DEPLOY_BACKUP_DIR"] = str(backup)
    failing_env["HOME"] = env["HOME"]
    run_script(repo, "rollback.sh", failing_env)
    home = Path(env["HOME"])
    assert not (home / ".config/systemd/user/pinger.service").exists()
    assert not (home / ".local/share/5h-bot-pinger/venv").exists()
    assert "--user stop pinger.service" in failing_log.read_text()


def test_health_checks_unit_state_and_schedule(tmp_path):
    repo = make_checkout(tmp_path)
    env, log = fake_commands(tmp_path / "commands")
    run_script(repo, "deploy.sh", env)
    run_script(repo, "health.sh", env)
    text = log.read_text()
    assert "--user is-active --quiet pinger.service" in text
    assert "pinger schedule --config" in text
    failing_env, _ = fake_commands(tmp_path / "failure", active=False)
    failing_env["HOME"] = env["HOME"]
    result = run_script(repo, "health.sh", failing_env, check=False)
    assert result.returncode == 1
