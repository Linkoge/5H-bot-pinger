"""Schedule tiny, isolated requests through installed AI coding CLIs."""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
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
TICK_SECONDS = 20
REASON_LIMIT = 150


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


_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(?:authorization|bearer|token|api[_-]?key|secret|password|passwd|credential)s?\b\s*[:=]?\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b(?:gh[pousr]_|github_pat_|xox[baprs]-)[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bey[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.?[A-Za-z0-9_-]*"),
    re.compile(r"\b(?=[A-Za-z0-9_+/=-]*\d)[A-Za-z0-9_+/=-]{24,}"),
)


def sanitize_reason(text: str, limit: int = REASON_LIMIT) -> str:
    """One short line of CLI output with anything token/key-shaped redacted."""
    text = " ".join(text[:4000].split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    return text[:limit]


def _now() -> datetime:
    return datetime.now().astimezone()


def run_one(provider: Provider, log: logging.Logger) -> bool:
    stamp = _now().isoformat(timespec="seconds")
    head = f"{stamp} {provider.name} model={provider.model}"
    if not shutil.which(provider.binary):
        log.info("%s fail CLI not found", head)
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="pinger-") as tempdir:
            env = os.environ.copy()
            # Claude's bare mode demands API-key auth; the ping must use the CLI's normal login.
            env.pop("CLAUDE_CODE_SIMPLE", None)
            result = subprocess.run(command_for(provider, Path(tempdir)), cwd=tempdir, env=env,
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                                    timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        log.info("%s fail timed out after %ss", head, TIMEOUT_SECONDS)
        return False
    except OSError:
        log.info("%s fail could not start CLI", head)
        return False
    except Exception as exc:  # one provider's surprise must never escape
        log.info("%s fail unexpected %s", head, type(exc).__name__)
        return False
    if result.returncode != 0:
        reason = sanitize_reason(f"{result.stderr or ''} {result.stdout or ''}")
        log.info("%s fail exit %s%s", head, result.returncode, f": {reason}" if reason else "")
        return False
    log.info("%s ok", head)
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


def perform_provider(provider: Provider, logger: logging.Logger, sleep=time.sleep, clock=_now) -> datetime | None:
    """Run one retry round; return the success time, or None when every attempt failed."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if run_one(provider, logger):
                return clock()
        except Exception as exc:
            logger.info("%s %s unexpected %s", _now().isoformat(timespec="seconds"), provider.name, type(exc).__name__)
        if attempt < MAX_ATTEMPTS:
            sleep(RETRY_SECONDS)
    logger.info("%s %s skipped after %s attempts", _now().isoformat(timespec="seconds"), provider.name, MAX_ATTEMPTS)
    return None


def perform(providers: tuple[Provider, ...], logger: logging.Logger, sleep=time.sleep, clock=_now) -> dict[str, datetime]:
    """Ping every enabled provider; returns {provider name: success time} for the ones that worked."""
    # Each provider retries on its own thread so a failing one never delays another.
    results: dict[str, datetime] = {}

    def work(provider: Provider) -> None:
        done = perform_provider(provider, logger, sleep, clock)
        if done is not None:
            results[provider.name] = done

    threads = [threading.Thread(target=work, args=(p,), daemon=True) for p in providers if p.enabled]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return results


# State file: {"last_success": iso, "providers": {name: {"last_success": iso, "last_attempt": iso}}}.
# "last_attempt" is written before a round starts, so a restart mid-round skips to the next due ping.
def _parse_time(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else None


def read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}  # the old list-of-slots format is ignored


def provider_times(state: dict, name: str) -> tuple[datetime | None, datetime | None]:
    entry = state.get("providers", {}).get(name, {}) if isinstance(state.get("providers"), dict) else {}
    if not isinstance(entry, dict):
        return None, None
    return _parse_time(entry.get("last_success")), _parse_time(entry.get("last_attempt"))


def mark(state: dict, name: str, key: str, when: datetime) -> None:
    providers = state.setdefault("providers", {})
    providers.setdefault(name, {})[key] = when.isoformat(timespec="seconds")
    successes = [e["last_success"] for e in providers.values() if isinstance(e, dict) and e.get("last_success")]
    if successes:
        state["last_success"] = max(successes, key=lambda v: _parse_time(v) or datetime.min.replace(tzinfo=ZoneInfo("UTC")))


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def active_period(day: date, config: Config) -> tuple[list[datetime], datetime]:
    """Slots of a day and the end of its active period (last slot + gap)."""
    slots = schedule_for(day, config)
    return slots, slots[-1] + timedelta(minutes=config.gap_minutes)


def next_ping(now: datetime, config: Config, last_success: datetime | None = None,
              last_attempt: datetime | None = None) -> datetime | None:
    """When the next ping is planned (a time <= now means due immediately).

    Inside an active period (first slot until last slot + gap) the next ping is
    max(next slot, last successful ping + gap); the next slot is the first one
    after the last success or failed round. Returns None when nothing is left.
    """
    gap = timedelta(minutes=config.gap_minutes)
    marks = [t for t in (last_success, last_attempt) if t is not None]
    ref = max(marks) if marks else None
    today = now.astimezone(timezone_for(config)).date()
    best = None
    for offset in (-1, 0, 1):
        slots, end = active_period(today + timedelta(days=offset), config)
        later = [s for s in slots if ref is None or s > ref]
        if end <= now or not later:
            continue
        target = later[0] if last_success is None else max(later[0], last_success + gap)
        if target < end and (best is None or target < best):
            best = target
    return best


def projected_pings(now: datetime, config: Config, last_success: datetime | None,
                    last_attempt: datetime | None = None) -> list[tuple[datetime, bool]]:
    """Today's remaining pings as (time, due_now), assuming each succeeds on time."""
    today = now.astimezone(timezone_for(config)).date()
    planned, cursor, success, attempt = [], now, last_success, last_attempt
    for _ in range(100):
        target = next_ping(cursor, config, success, attempt)
        if target is None:
            break
        fire = max(target, now)
        if fire.astimezone(timezone_for(config)).date() != today:
            break
        planned.append((fire, target <= now))
        cursor, success, attempt = fire, fire, None
    return planned


def schedule_lines(config: Config, now: datetime, state: dict) -> list[str]:
    zone = timezone_for(config)
    today = now.astimezone(zone).date()
    plans = {}
    for provider in config.providers:
        if not provider.enabled:
            continue
        success, attempt = provider_times(state, provider.name)
        lines = []
        if success and success.astimezone(zone).date() == today:
            lines.append(f"{success.astimezone(zone):%H:%M} (done)")
        lines += [f"{t.astimezone(zone):%H:%M}" + (" (due now)" if due else "") for t, due in projected_pings(now, config, success, attempt)]
        plans[provider.name] = lines
    if not plans:
        plans[""] = [f"{t.astimezone(zone):%H:%M}" for t, _ in projected_pings(now, config, None)]
    if len({tuple(v) for v in plans.values()}) == 1:
        return next(iter(plans.values())) or ["(no more pings today)"]
    return [f"{name}: {line}" for name, lines in plans.items() for line in (lines or ["(no more pings today)"])]


def record_results(path: Path, results: dict[str, datetime]) -> None:
    if results:
        state = read_state(path)
        for name, when in results.items():
            mark(state, name, "last_success", when)
        write_state(path, state)


def scheduler(config: Config, logger: logging.Logger, clock=_now, sleep=time.sleep) -> None:
    announced: dict[str, datetime | None] = {}
    while True:
        try:
            now = clock()
            state = read_state(config.state_file)
            due = []
            for provider in (p for p in config.providers if p.enabled):
                success, attempt = provider_times(state, provider.name)
                target = next_ping(now, config, success, attempt)
                if announced.get(provider.name, 0) != target:
                    announced[provider.name] = target
                    logger.info("%s scheduler: next ping %s at %s (last success %s)", now.isoformat(timespec="seconds"),
                                provider.name, target.isoformat(timespec="seconds") if target else "none",
                                success.isoformat(timespec="seconds") if success else "never")
                if target is not None and target <= now:
                    due.append(provider)
            if due:
                # Persist before running so a restart never duplicates a ping.
                for provider in due:
                    mark(state, provider.name, "last_attempt", now)
                write_state(config.state_file, state)
                record_results(config.state_file, perform(tuple(due), logger, sleep, clock))
        except Exception as exc:
            logger.info("%s scheduler error %s: %s", _now().isoformat(timespec="seconds"), type(exc).__name__, exc)
        sleep(TICK_SECONDS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pinger")
    parser.add_argument("command", choices=("schedule", "ping-now", "run"))
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        if args.command == "schedule":
            print("\n".join(schedule_lines(config, _now(), read_state(config.state_file))))
            return 0
        logger = make_logger(config.log_file)
        if args.command == "ping-now":
            record_results(config.state_file, perform(config.providers, logger))
        else:
            scheduler(config, logger)
    except (OSError, ValueError, tomllib.TOMLDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
