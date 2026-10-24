import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def make_checkout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "pyproject.toml").write_text("old revision\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "old"], check=True)
    (repo / "pyproject.toml").write_text("new revision\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.invalid", "commit", "-qm", "new"], check=True)
    (repo / "scripts").mkdir()
    for name in ("backup.sh", "rollback.sh", "health.sh", "deploy.sh"):
        (repo / "scripts" / name).write_bytes((ROOT / "scripts" / name).read_bytes())
    return repo


def fake_commands(tmp_path, *, active=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "commands.log"
    systemctl = bindir / "systemctl"
    systemctl.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$COMMAND_LOG\"\n" + ("exit 0\n" if active else "exit 1\n"))
    python = bindir / "python3"
    python.write_text("#!/bin/sh\nprintf 'python3 %s\\n' \"$*\" >> \"$COMMAND_LOG\"\n")
    for command in (systemctl, python):
        command.chmod(0o755)
    return os.environ | {"PATH": f"{bindir}:{os.environ['PATH']}", "COMMAND_LOG": str(log)}, log


def run_script(repo, name, env, *, check=True):
    return subprocess.run(["sh", str(repo / "scripts" / name)], cwd=repo, env=env, text=True, capture_output=True, check=check, timeout=10)


def test_deploy_configuration_uses_documented_service_and_hooks():
    config = json.loads((ROOT / ".linko-deploy.json").read_text())
    assert config["service"] == "pinger.service"
    for key in ("backup_command", "health_command", "rollback_command"):
        assert isinstance(config[key], list)
        assert config[key][0].startswith("scripts/")


def test_backup_and_rollback_use_saved_parent_revision(tmp_path):
    repo = make_checkout(tmp_path)
    backup = tmp_path / "backups"
    env, log = fake_commands(tmp_path / "commands")
    env["PINGER_DEPLOY_BACKUP_DIR"] = str(backup)
    run_script(repo, "backup.sh", env)
    revision = Path((backup / "latest").read_text().strip())
    assert (revision / "pyproject.toml").read_text() == "old revision\n"
    run_script(repo, "rollback.sh", env)
    assert "pip install --user --force-reinstall" in log.read_text()
    assert "--user restart pinger.service" in log.read_text()


def test_health_checks_user_service_state(tmp_path):
    repo = make_checkout(tmp_path)
    env, log = fake_commands(tmp_path / "commands")
    run_script(repo, "health.sh", env)
    assert "--user is-active --quiet pinger.service" in log.read_text()
    failing_env, _ = fake_commands(tmp_path / "failure", active=False)
    result = run_script(repo, "health.sh", failing_env, check=False)
    assert result.returncode == 1


def test_deploy_installs_checkout_then_restarts_unit(tmp_path):
    repo = make_checkout(tmp_path)
    env, log = fake_commands(tmp_path / "commands")
    run_script(repo, "deploy.sh", env)
    assert log.read_text().splitlines() == ["python3 -m pip install --user .", "--user restart pinger.service"]
