# 5H-bot-pinger

## What it is

`pinger` is a small Python tool that makes scheduled, tiny requests through your installed Claude Code and Codex command line tools. It can help start a subscription usage window near the times you choose.

Claude and Codex subscriptions limit usage over rolling five-hour windows. The window begins with the first request after the prior window has ended; while you are idle, its timer does not count down. For example, if a window ends at 14:00 and you next use the AI at 20:00, that next window starts at 20:00. If you use it up, the following wait can be another five hours.

Without scheduled pings, first use at 11:00 could start a window that runs out before the next useful session, leaving a wait until about 16:00. With pings at 07:00, 12:02, and 17:04, a session beginning at 11:00 can get a fresh window around 12:02 instead. Provider behavior and limits can vary, so this is an approximation, not a guaranteed reset time.

## How it works

Set one daily start time, a gap (302 minutes by default: five hours plus a two-minute safety margin), and a number of pings. The example configuration creates 07:00, 12:02, and 17:04 each day. Each day starts over at the configured time; there are no extra pings outside the schedule. The timezone can be `local` or an IANA timezone name.

Each enabled provider gets one non-interactive request with the exact prompt `Reply with only: ok`. Claude defaults to model `haiku` and lowest effort (`low`), with an empty temporary working directory, no tools, and no saved session. Codex defaults to model `luna` and lowest reasoning effort (`low`), with a temporary working directory, read-only sandbox, and ephemeral session. No project instructions such as `CLAUDE.md` or `AGENTS.md` are present in the temporary directory. CLI output is discarded so hidden context, response contents, tokens, and credentials do not enter logs.

Each attempt has a 60-second timeout. Failures are retried after five minutes, for six attempts total, then skipped until the next scheduled ping. A single provider failure does not crash the scheduler. One short result line per attempt is written to stdout and `pinger.log`. If the machine starts within ten minutes after a scheduled time, the scheduler runs that ping once; older missed pings are skipped. State is kept in `.pinger-state.json` to avoid replaying an already handled slot after a restart.

The CLI arguments are verified against the installed `claude --help` and `codex exec --help` output. Claude uses documented `--print`, `--model`, `--effort low`, `--tools`, and `--no-session-persistence` flags. Codex uses `exec --ephemeral --ignore-user-config --model`, `-c model_reasoning_effort=low`, `--sandbox read-only`, and `--cd`. If a future CLI version changes these options, check its help before updating the command builder. Official references: [Claude Code CLI usage](https://docs.anthropic.com/en/docs/claude-code/cli-usage) and [Codex CLI documentation](https://developers.openai.com/codex/cli/).

## Choosing your schedule

Choose a start time near when you usually wake up or begin work. Three pings cover a typical day. Four pings add one more late-day reset, if you need a window later at night; each additional ping also makes a provider request.

For example, start at 08:00 with a 302-minute gap to get approximately 08:00, 13:02, and 18:04. Set `pings_per_day = 4` for another ping at 23:06. Keep the start time local to the intended daily routine.

## Install, configure, and run

Python 3.11 or newer is required. The scheduler uses only the Python standard library. Install the package with `python3 -m pip install .`, then copy `config.example.toml` to `config.toml` and edit it. The config file is ignored by Git. Authentication stays in each CLI's own login; this tool does not store API keys.

Check today's schedule with `pinger schedule`. Send one immediate request per enabled provider with `pinger ping-now`. Start the persistent scheduler with `pinger run`. These commands use `config.toml` in the current directory; pass `--config /path/to/config.toml` to choose another file.

### systemd user service

Save the following as `~/.config/systemd/user/pinger.service`, replacing `/path/to/checkout` with the directory containing your config:

```ini
[Unit]
Description=Scheduled AI CLI warm-up pings

[Service]
Type=simple
WorkingDirectory=/path/to/checkout
ExecStart=%h/.local/bin/pinger run
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
```

Enable it with `systemctl --user daemon-reload` and `systemctl --user enable --now pinger.service`. On systems where the user service should run before login, enable lingering with `loginctl enable-linger "$USER"`. Inspect service output with `journalctl --user -u pinger.service`.

### Cron alternative

Cron is less suitable because it cannot keep the scheduler running to catch a start-up that occurs within ten minutes of a missed slot. To launch it at boot, add `@reboot cd /path/to/checkout && /path/to/pinger run` to your crontab. The process must remain running; configure cron's environment and paths appropriately.

The application log defaults to `pinger.log` in the current directory. It contains timestamps, provider names, model names, outcome, and a short failure reason. It never records command output or credentials.

## Cost

The requests use the configured small model and a very short prompt, and isolation avoids loading project files and tools, which helps keep input small. The exact amount charged or usage consumed depends on provider pricing and the CLI's hidden system context; “near-zero” is not “free.” Each request may count slightly toward weekly subscription limits.

## Limitations and disclaimer

This relies on the providers' current five-hour window behavior, which can change and may vary by plan. It cannot guarantee available capacity or a reset at a precise time. Use the CLIs and subscriptions within their providers' terms. This project is not affiliated with Anthropic or OpenAI.

## License

MIT. See [LICENSE](LICENSE).
