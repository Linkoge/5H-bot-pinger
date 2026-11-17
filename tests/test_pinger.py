import json
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


def test_config_load(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('start_time="07:00"\ngap_minutes=302\npings_per_day=3\ntimezone="UTC"\n[claude]\nenabled=false\n')
    assert not pinger.load_config(path).providers[0].enabled


def _quiet_logger(name):
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.addHandler(logging.NullHandler())
    return logger


def test_codex_failure_does_not_affect_claude(tmp_path):
    marker = tmp_path / "claude-ran"
    claude = tmp_path / "fake-claude"
    claude.write_text(f"#!{sys.executable}\nopen({str(marker)!r}, 'a').write('x')\n")
    claude.chmod(0o755)
    providers = (pinger.Provider("codex", True, str(tmp_path / "missing-codex"), "luna"),
                 pinger.Provider("claude", True, str(claude), "haiku"))
    sleeps = []
    pinger.perform(providers, _quiet_logger("independent"), sleeps.append)
    assert marker.read_text() == "x"  # claude pinged once, unaffected by codex retries
    assert sleeps == [300] * 5  # only codex retried


def test_unexpected_exception_in_loop_does_not_kill_run(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    calls = []
    def clock():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return datetime(2026, 1, 1, 12, tzinfo=pinger.timezone_for(cfg))
    class Stop(Exception):
        pass
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise Stop
    with pytest.raises(Stop):
        pinger.scheduler(cfg, _quiet_logger("loop"), clock, sleep)
    assert len(calls) == 3


# ---- auth, failure reasons ----

def _fake_cli(tmp_path, body, name="fake-cli"):
    fake = tmp_path / name
    fake.write_text(f"#!{sys.executable}\nimport os, sys\n{body}\n")
    fake.chmod(0o755)
    return str(fake)


def _capture_logger(name):
    records = []
    class Handler(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())
    logger = logging.getLogger(name)
    logger.handlers.clear()
    logger.addHandler(Handler())
    logger.setLevel(logging.INFO)
    return logger, records


def test_claude_env_has_no_bare_mode(tmp_path, monkeypatch):
    out = tmp_path / "env-seen"
    cli = _fake_cli(tmp_path, f"open({str(out)!r}, 'w').write(repr('CLAUDE_CODE_SIMPLE' in os.environ))")
    monkeypatch.setenv("CLAUDE_CODE_SIMPLE", "1")  # even an inherited value must not reach the CLI
    logger, _ = _capture_logger("env-test")
    assert pinger.run_one(pinger.Provider("claude", True, cli, "haiku"), logger)
    assert out.read_text() == "False"


def test_failure_reason_is_logged_and_redacted(tmp_path):
    secret = "sk-" + "ant-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3"
    cli = _fake_cli(tmp_path, f"sys.stderr.write('Not logged in\\n  token {secret} please run /login ' + 'x' * 400)\nsys.exit(3)")
    logger, records = _capture_logger("reason-test")
    assert not pinger.run_one(pinger.Provider("claude", True, cli, "haiku"), logger)
    (line,) = records
    assert "fail exit 3: Not logged in" in line
    assert secret not in line and "[redacted]" in line
    assert "\n" not in line and len(line.split("fail ", 1)[1]) <= 160


def test_sanitize_reason_redacts_key_shapes():
    jwt = "ey" + "J" + "abcdefghij.ey" + "J" + "abcdefghij.signature123"
    gh = "gh" + "p_" + "a" * 36
    long_hex = "0123456789abcdef" * 3
    text = f"usage limit reached\nBearer {jwt} {gh} {long_hex} API_KEY=hunter2"
    out = pinger.sanitize_reason(text)
    for leaked in (jwt, gh, long_hex, "hunter2"):
        assert leaked not in out
    assert out.startswith("usage limit reached ") and "\n" not in out
    assert len(pinger.sanitize_reason("word " * 200)) <= 150


# ---- boot / missed-ping catch-up ----

UTC = pinger.ZoneInfo("UTC")


def at(hour, minute=0, day=1):
    return datetime(2026, 1, day, hour, minute, tzinfo=UTC)


def hm(value):
    return value.strftime("%d %H:%M")


@pytest.mark.smoke
def test_boot_inside_active_period_pings_immediately(tmp_path):
    cfg = config(tmp_path)
    assert pinger.next_ping(at(9, 30), cfg, at(0, 1, day=1) - timedelta(days=1)) <= at(9, 30)
    assert pinger.next_ping(at(9, 30), cfg, None) <= at(9, 30)
    assert pinger.next_ping(at(7, 0, 1), cfg, None) <= at(7, 0)  # right at the first slot


def test_reaim_after_boot_ping(tmp_path):
    cfg = config(tmp_path)
    boot = at(9, 30)
    assert hm(pinger.next_ping(boot, cfg, boot)) == "01 14:32"
    second = at(14, 32)
    assert hm(pinger.next_ping(second, cfg, second)) == "01 19:34"
    third = at(19, 34)
    # 19:34 + gap is past the end of the active period (22:06): stop for the night, resume at the start time
    assert hm(pinger.next_ping(third, cfg, third)) == "02 07:00"


@pytest.mark.smoke
def test_no_ping_at_night_or_within_gap(tmp_path):
    cfg = config(tmp_path)
    for night in (at(3), at(6, 59), at(22, 6), at(23, 30)):
        target = pinger.next_ping(night, cfg, None)
        assert target > night, night
    assert hm(pinger.next_ping(at(23, 30), cfg, at(19, 34))) == "02 07:00"
    just_pinged = at(9, 30)
    for later in (at(9, 31), at(12, 2), at(14, 31)):
        assert pinger.next_ping(later, cfg, just_pinged) > later
    assert pinger.next_ping(at(14, 32), cfg, just_pinged) <= at(14, 32)


def test_regular_day_follows_slots(tmp_path):
    cfg = config(tmp_path)
    assert hm(pinger.next_ping(at(7, 1), cfg, at(7, 0))) == "01 12:02"
    assert hm(pinger.next_ping(at(12, 5), cfg, at(12, 2))) == "01 17:04"
    assert pinger.next_ping(at(7, 1), cfg, at(7, 0)) > at(7, 1)


def test_overnight_active_period_crosses_midnight(tmp_path):
    cfg = config(tmp_path, start_time=time(23), gap_minutes=120, pings_per_day=3)  # active 23:00 -> 05:00
    assert pinger.next_ping(at(1, 30, day=2), cfg, None) <= at(1, 30, day=2)
    assert pinger.next_ping(at(12, day=2), cfg, None) > at(12, day=2)


def test_failed_round_skips_to_next_slot(tmp_path):
    cfg = config(tmp_path)
    boot = at(9, 30)
    assert hm(pinger.next_ping(at(9, 55), cfg, None, boot)) == "01 12:02"
    assert hm(pinger.next_ping(at(12, 30), cfg, at(7, 0), at(12, 2))) == "01 17:04"


def test_schedule_lines_account_for_last_ping(tmp_path):
    cfg = config(tmp_path)
    state = {}
    pinger.mark(state, "claude", "last_success", at(9, 30))
    cfg = config(tmp_path, providers=(pinger.Provider("claude", True, "claude", "haiku"),))
    assert pinger.schedule_lines(cfg, at(10), state) == ["09:30 (done)", "14:32", "19:34"]
    assert pinger.schedule_lines(cfg, at(10), {}) == ["10:00 (due now)", "15:02", "20:04"]
    assert pinger.schedule_lines(cfg, at(23), state) == ["09:30 (done)"]


def _run_scheduler(cfg, clock_values, monkeypatch, pings, ok=True):
    """Run the loop through the given times; pings collects (name) per real ping."""
    def fake_run_one(provider, log):
        pings.append(provider.name)
        return ok
    monkeypatch.setattr(pinger, "run_one", fake_run_one)
    times = iter(clock_values)
    current = [None]
    def clock():
        return current[0]
    class Stop(Exception):
        pass
    def sleep(seconds):
        if seconds != pinger.TICK_SECONDS:
            return  # retry waits inside a round
        try:
            current[0] = next(times)
        except StopIteration:
            raise Stop
    current[0] = next(times)
    with pytest.raises(Stop):
        pinger.scheduler(cfg, _quiet_logger("sched"), clock, sleep)


def _claude_cfg(tmp_path):
    return config(tmp_path, providers=(pinger.Provider("claude", True, "claude", "haiku"),))


def test_scheduler_pings_on_boot_then_reaims(tmp_path, monkeypatch):
    cfg = _claude_cfg(tmp_path)
    pings = []
    ticks = [at(9, 30), at(9, 30), at(12, 5), at(14, 31), at(14, 32), at(14, 33)]
    _run_scheduler(cfg, ticks, monkeypatch, pings)
    assert pings == ["claude", "claude"]  # 09:30 boot ping, then 14:32
    state = json.loads(cfg.state_file.read_text())
    assert state["providers"]["claude"]["last_success"].startswith("2026-01-01T14:32")
    assert state["last_success"] == state["providers"]["claude"]["last_success"]


def test_scheduler_quiet_at_night_and_within_gap(tmp_path, monkeypatch):
    cfg = _claude_cfg(tmp_path)
    pings = []
    _run_scheduler(cfg, [at(2), at(5), at(22, 30), at(23, 59)], monkeypatch, pings)
    assert pings == []
    pinger.write_state(cfg.state_file, {"providers": {"claude": {"last_success": at(9, 30).isoformat()}}})
    _run_scheduler(cfg, [at(9, 40), at(12, 3), at(14, 31)], monkeypatch, pings)
    assert pings == []


def test_no_duplicate_pings_across_restart(tmp_path, monkeypatch):
    cfg = _claude_cfg(tmp_path)
    pings = []
    _run_scheduler(cfg, [at(9, 30), at(9, 31)], monkeypatch, pings)
    assert pings == ["claude"]
    _run_scheduler(cfg, [at(9, 32), at(9, 33), at(12, 10)], monkeypatch, pings)  # "restart"
    assert pings == ["claude"]


def test_failed_round_is_not_repeated_after_restart(tmp_path, monkeypatch):
    cfg = _claude_cfg(tmp_path)
    pings = []
    _run_scheduler(cfg, [at(9, 30), at(9, 31)], monkeypatch, pings, ok=False)
    assert pings == ["claude"] * pinger.MAX_ATTEMPTS
    state = json.loads(cfg.state_file.read_text())
    assert "last_success" not in state["providers"]["claude"]
    pings.clear()
    _run_scheduler(cfg, [at(9, 40), at(11, 0)], monkeypatch, pings, ok=False)  # skipped until the next slot
    assert pings == []
    _run_scheduler(cfg, [at(12, 3), at(12, 4)], monkeypatch, pings)
    assert pings == ["claude"]


def test_interrupted_round_marker_prevents_duplicate(tmp_path, monkeypatch):
    # The service dies mid-round: only the pre-written attempt marker exists.
    cfg = _claude_cfg(tmp_path)
    pinger.write_state(cfg.state_file, {"providers": {"claude": {"last_attempt": at(9, 30).isoformat()}}})
    pings = []
    _run_scheduler(cfg, [at(9, 31), at(9, 32)], monkeypatch, pings)
    assert pings == []


def test_legacy_state_file_is_ignored(tmp_path):
    path = tmp_path / "state"
    path.write_text(json.dumps(["2026-01-01T07:00:00+00:00"]))
    assert pinger.read_state(path) == {}


def test_ping_now_records_success(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'timezone="UTC"\nstate_file="{tmp_path / "state.json"}"\nlog_file="{tmp_path / "x.log"}"\n'
                        '[claude]\nenabled=true\n[codex]\nenabled=false\n')
    monkeypatch.setattr(pinger, "run_one", lambda provider, log: True)
    assert pinger.main(["ping-now", "--config", str(cfg_file)]) == 0
    assert "last_success" in json.loads((tmp_path / "state.json").read_text())["providers"]["claude"]
