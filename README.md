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

Python 3.11 or newer is required. The scheduler uses only the Python standard library. Install the package in a virtualenv (`python3 -m venv .venv && .venv/bin/pip install .`) or use the automated deployment below, then copy `config.example.toml` to `config.toml` and edit it. The config file is ignored by Git. Authentication stays in each CLI's own login; this tool does not store API keys.

Check today's schedule with `pinger schedule`. Send one immediate request per enabled provider with `pinger ping-now`. Start the persistent scheduler with `pinger run`. These commands use `config.toml` in the current directory; pass `--config /path/to/config.toml` to choose another file.

### systemd user service (automatic)

Deployment (`.linko-deploy.json` and `scripts/`) sets everything up; no manual unit is needed:

- Installs the tool into a dedicated venv, `~/.local/share/5h-bot-pinger/venv` (no `pip --user`, which externally-managed Pythons block).
- Writes `~/.config/systemd/user/pinger.service` (`ExecStart=<venv>/bin/pinger run --config ~/.config/5h-bot-pinger/config.toml`, `Restart=always`, `RestartSec=30`, `WantedBy=default.target`), then runs `systemctl --user daemon-reload`, `enable` and `restart`.
- Creates `~/.config/5h-bot-pinger/config.toml` from `config.example.toml` only if it does not exist, with `[codex] enabled = false` and Claude enabled; log and state files go under `~/.local/share/5h-bot-pinger/`. An existing config is never overwritten. Set `enabled = true` under `[codex]` once you have a Codex subscription.
- Tries `loginctl enable-linger "$USER"` so the service runs without a login session. If that is not allowed without sudo, deploy logs a warning and continues; run the command yourself once.

Inspect output with `journalctl --user -u pinger.service`. XDG_DATA_HOME and XDG_CONFIG_HOME are honored.

### Deployment hooks

- `scripts/backup.sh` saves the first parent revision to `.deploy-backups/` (override with `PINGER_DEPLOY_BACKUP_DIR`; ignored by Git) and records whether the unit and venv existed.
- `scripts/health.sh` requires `systemctl --user is-active pinger.service` and a successful `pinger schedule` with the deployed config.
- `scripts/rollback.sh` rebuilds the previous venv and restores the previous unit; on a first install it stops, disables and removes the new unit and venv, tolerating "not loaded".

A failing provider (missing binary, not logged in, no subscription, unreachable) only affects itself: each provider pings and retries on its own (every five minutes, six attempts), and unexpected errors in the scheduler loop are logged while it keeps running.

### Cron alternative

Cron is less suitable because it cannot keep the scheduler running to catch a start-up that occurs within ten minutes of a missed slot. To launch it at boot, add `@reboot cd /path/to/checkout && /path/to/pinger run` to your crontab. The process must remain running; configure cron's environment and paths appropriately.

The application log defaults to `pinger.log` in the current directory. It contains timestamps, provider names, model names, outcome, and a short failure reason. It never records command output or credentials.

## Cost

The requests use the configured small model and a very short prompt, and isolation avoids loading project files and tools, which helps keep input small. The exact amount charged or usage consumed depends on provider pricing and the CLI's hidden system context; “near-zero” is not “free.” Each request may count slightly toward weekly subscription limits.

## Limitations and disclaimer

This relies on the providers' current five-hour window behavior, which can change and may vary by plan. It cannot guarantee available capacity or a reset at a precise time. Use the CLIs and subscriptions within their providers' terms. This project is not affiliated with Anthropic or OpenAI.

## License

MIT. See [LICENSE](LICENSE).
