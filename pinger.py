"""Schedule tiny, isolated requests through installed AI coding CLIs."""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import tomllib
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROMPT = "Reply with only: ok"
TIMEOUT_SECONDS = 60
RETRY_SECONDS = 300
MAX_ATTEMPTS = 6
CATCHUP_SECONDS = 600


@dataclass(frozen=True)
class Provider:
    name: str
    enabled: bool
    binary: str
    model: str
    effort: str = "low"


@dataclass(frozen=True)
class Config:
    start_time: dtime
    gap_minutes: int
    pings_per_day: int
    timezone: str
    providers: tuple[Provider, ...]
    log_file: Path
    state_file: Path


def load_config(path: Path) -> Config:
    with path.open("rb") as stream:
        raw = tomllib.load(stream)
    start = dtime.fromisoformat(raw.get("start_time", "07:00"))
    gap = int(raw.get("gap_minutes", 302))
    count = int(raw.get("pings_per_day", 3))
    if gap < 1 or not 1 <= count <= 48:
        raise ValueError("gap_minutes must be positive and pings_per_day must be 1..48")
    zone = raw.get("timezone", "local")
    if zone != "local":
        try:
            ZoneInfo(zone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown timezone: {zone}") from exc
    providers = []
    for name, defaults in (("claude", ("claude", "haiku", "low")), ("codex", ("codex", "luna", "low"))):
        section = raw.get(name, {})
        providers.append(Provider(name, bool(section.get("enabled", True)), str(section.get("binary", defaults[0])),
                                  str(section.get("model", defaults[1])), str(section.get("effort", defaults[2]))))
    return Config(start, gap, count, zone, tuple(providers), Path(raw.get("log_file", "pinger.log")), Path(raw.get("state_file", ".pinger-state.json")))


def timezone_for(config: Config):
    if config.timezone != "local":
        return ZoneInfo(config.timezone)
    if os.environ.get("TZ"):
        try:
            return ZoneInfo(os.environ["TZ"])
        except ZoneInfoNotFoundError:
            pass
    try:
        with Path("/etc/localtime").open("rb") as stream:
            return ZoneInfo.from_file(stream, key="local")
    except OSError:
        return datetime.now().astimezone().tzinfo


def schedule_for(day: date, config: Config) -> list[datetime]:
    zone = timezone_for(config)
    start = datetime.combine(day, config.start_time, tzinfo=zone)
    return [start + timedelta(minutes=config.gap_minutes * i) for i in range(config.pings_per_day)]


def command_for(provider: Provider, workdir: Path) -> list[str]:
    if provider.name == "claude":
        return [provider.binary, "--print", "--model", provider.model, "--effort", provider.effort,
                "--tools", "", "--no-session-persistence", PROMPT]
    if provider.name == "codex":
        return [provider.binary, "exec", "--ephemeral", "--ignore-user-config", "--model", provider.model,
                "-c", f'model_reasoning_effort="{provider.effort}"', "--sandbox", "read-only", "--cd", str(workdir), PROMPT]
    raise ValueError(f"unsupported provider: {provider.name}")


def run_one(provider: Provider, log: logging.Logger) -> bool:
    stamp = datetime.now().astimezone().isoformat(timespec="seconds")
    if not shutil.which(provider.binary):
        log.info("%s %s model=%s fail CLI not found", stamp, provider.name, provider.model)
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="pinger-") as tempdir:
            env = os.environ.copy()
            if provider.name == "claude":
                # The bare mode requires explicit API-key auth; preserve native auth when available.
                env["CLAUDE_CODE_SIMPLE"] = "1"
            subprocess.run(command_for(provider, Path(tempdir)), cwd=tempdir, env=env,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=TIMEOUT_SECONDS, check=True)
    except subprocess.TimeoutExpired:
        log.info("%s %s model=%s fail timed out after %ss", stamp, provider.name, provider.model, TIMEOUT_SECONDS)
        return False
    except subprocess.CalledProcessError:
        log.info("%s %s model=%s fail CLI exited unsuccessfully", stamp, provider.name, provider.model)
        return False
    except OSError:
        log.info("%s %s model=%s fail could not start CLI", stamp, provider.name, provider.model)
        return False
    except Exception as exc:  # one provider's surprise must never escape
        log.info("%s %s model=%s fail unexpected %s", stamp, provider.name, provider.model, type(exc).__name__)
        return False
    log.info("%s %s model=%s ok", stamp, provider.name, provider.model)
    return True


def make_logger(path: Path) -> logging.Logger:
    logger = logging.getLogger("pinger")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter("%(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    logger.addHandler(stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


def perform_provider(provider: Provider, logger: logging.Logger, sleep=time.sleep) -> None:
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if run_one(provider, logger):
                return
        except Exception as exc:
            logger.info("%s %s unexpected %s", datetime.now().astimezone().isoformat(timespec="seconds"), provider.name, type(exc).__name__)
        if attempt < MAX_ATTEMPTS:
            sleep(RETRY_SECONDS)
    logger.info("%s %s skipped after %s attempts", datetime.now().astimezone().isoformat(timespec="seconds"), provider.name, MAX_ATTEMPTS)


def perform(providers: tuple[Provider, ...], logger: logging.Logger, sleep=time.sleep) -> None:
    # Each provider retries on its own thread so a failing one never delays another.
    threads = [threading.Thread(target=perform_provider, args=(p, logger, sleep), daemon=True)
               for p in providers if p.enabled]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


def read_state(path: Path) -> set[str]:
    try:
        return set(json.loads(path.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return set()


def write_state(path: Path, state: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(state)), encoding="utf-8")


def due_slot(now: datetime, config: Config, state: set[str]) -> datetime | None:
    today = now.date()
    candidates = schedule_for(today, config) + schedule_for(today - timedelta(days=1), config)
    eligible = [slot for slot in candidates if slot <= now and (now - slot).total_seconds() <= CATCHUP_SECONDS
                and slot.isoformat() not in state]
    return max(eligible) if eligible else None


def scheduler(config: Config, logger: logging.Logger, clock=lambda: datetime.now().astimezone(), sleep=time.sleep) -> None:
    state = read_state(config.state_file)
    while True:
        try:
            now = clock()
            slot = due_slot(now, config, state)
            if slot is not None:
                # Persist before running so a restart never duplicates a ping.
                state.add(slot.isoformat())
                write_state(config.state_file, state)
                perform(config.providers, logger, sleep)
        except Exception as exc:
            logger.info("%s scheduler error %s: %s", datetime.now().astimezone().isoformat(timespec="seconds"), type(exc).__name__, exc)
        sleep(20)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pinger")
    parser.add_argument("command", choices=("schedule", "ping-now", "run"))
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "schedule":
            for slot in schedule_for(datetime.now(timezone_for(config)).date(), config):
                print(slot.strftime("%H:%M"))
            return 0
        logger = make_logger(config.log_file)
        if args.command == "ping-now":
            perform(config.providers, logger)
        else:
            scheduler(config, logger)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
