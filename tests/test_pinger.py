import logging
import os
import subprocess
import sys
from datetime import date, datetime, time, timedelta

import pytest

import pinger


def config(tmp_path, **overrides):
    values = {"start_time": time(7), "gap_minutes": 302, "pings_per_day": 3,
              "timezone": "UTC", "providers": (), "log_file": tmp_path / "log",
              "state_file": tmp_path / "state"}
    values.update(overrides)
    return pinger.Config(**values)


@pytest.mark.smoke
def test_example_schedule_and_midnight(tmp_path):
    cfg = config(tmp_path)
    assert [x.strftime("%H:%M") for x in pinger.schedule_for(date(2026, 1, 1), cfg)] == ["07:00", "12:02", "17:04"]
    overnight = config(tmp_path, start_time=time(23), gap_minutes=120, pings_per_day=3)
    slots = pinger.schedule_for(date(2026, 1, 1), overnight)
    assert [x.date() for x in slots] == [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 2)]


def test_iana_timezone(tmp_path):
    cfg = config(tmp_path, timezone="Asia/Tbilisi")
    assert pinger.schedule_for(date(2026, 1, 1), cfg)[0].utcoffset() == timedelta(hours=4)


@pytest.mark.smoke
def test_commands_use_isolation_and_correct_modes(tmp_path):
    claude = pinger.command_for(pinger.Provider("claude", True, "claude", "haiku"), tmp_path)
    assert "--print" in claude and "--no-session-persistence" in claude and "--tools" in claude
    assert claude[claude.index("--model") + 1] == "haiku"
    assert claude[claude.index("--effort") + 1] == "low"
    assert claude[-1] == "Reply with only: ok"
    codex = pinger.command_for(pinger.Provider("codex", True, "codex", "luna"), tmp_path)
    assert "--ephemeral" in codex and "--sandbox" in codex and "read-only" in codex
    assert "--cd" in codex and str(tmp_path) in codex
    assert codex[codex.index("--model") + 1] == "luna"
    assert codex[codex.index("-c") + 1] == 'model_reasoning_effort="low"'
    assert codex[-1] == "Reply with only: ok"


def test_fake_cli_success_and_failure(tmp_path, monkeypatch):
    fake = tmp_path / "fake-cli"
    fake.write_text(f"#!{sys.executable}\nimport os, sys\nsys.exit(int(os.environ.get('FAKE_EXIT', '0')))\n")
    fake.chmod(0o755)
    provider = pinger.Provider("claude", True, str(fake), "haiku")
    logger = logging.getLogger("test-pinger")
    logger.addHandler(logging.NullHandler())
    assert pinger.run_one(provider, logger)
    monkeypatch.setenv("FAKE_EXIT", "1")
    assert not pinger.run_one(provider, logger)


def test_retries_timeout_and_logs_no_stderr(tmp_path, monkeypatch):
    provider = pinger.Provider("claude", True, "claude", "haiku")
    attempts, sleeps = [], []
    monkeypatch.setattr(pinger.shutil, "which", lambda _: "/bin/true")
    def run(*args, **kwargs):
        attempts.append(1)
        raise subprocess.TimeoutExpired("claude", 60, stderr="SECRET")
    monkeypatch.setattr(pinger.subprocess, "run", run)
    logger = logging.getLogger("retry-test")
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    pinger.perform((provider,), logger, sleeps.append)
    assert len(attempts) == 6
    assert sleeps == [300] * 5


def test_catchup_window_and_state(tmp_path):
    cfg = config(tmp_path)
    slot = pinger.schedule_for(date(2026, 1, 1), cfg)[0]
    assert pinger.due_slot(slot + timedelta(minutes=10), cfg, set()) == slot
    assert pinger.due_slot(slot + timedelta(minutes=10, seconds=1), cfg, set()) is None
    assert pinger.due_slot(slot + timedelta(minutes=1), cfg, {slot.isoformat()}) is None


def test_config_load(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('start_time="07:00"\ngap_minutes=302\npings_per_day=3\ntimezone="UTC"\n[claude]\nenabled=false\n')
    assert not pinger.load_config(path).providers[0].enabled
