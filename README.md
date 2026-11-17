# 5H-bot-pinger

## What it is

`pinger` is a small Python tool that makes scheduled, tiny requests through your installed Claude Code and Codex command line tools. It can help start a subscription usage window near the times you choose.

Claude and Codex subscriptions limit usage over rolling five-hour windows. The window begins with the first request after the prior window has ended; while you are idle, its timer does not count down. For example, if a window ends at 14:00 and you next use the AI at 20:00, that next window starts at 20:00. If you use it up, the following wait can be another five hours.

Without scheduled pings, first use at 11:00 could start a window that runs out before the next useful session, leaving a wait until about 16:00. With pings at 07:00, 12:02, and 17:04, a session beginning at 11:00 can get a fresh window around 12:02 instead. Provider behavior and limits can vary, so this is an approximation, not a guaranteed reset time.

## How it works

Set one daily start time, a gap (302 minutes by default: five hours plus a two-minute safety margin), and a number of pings. The example configuration creates 07:00, 12:02, and 17:04 each day. The timezone can be `local` or an IANA timezone name.

Each enabled provider gets one non-interactive request with the exact prompt `Reply with only: ok`. Claude defaults to model `haiku` and lowest effort (`low`), with an empty temporary working directory, no tools, and no saved session. Codex defaults to model `luna` and lowest reasoning effort (`low`), with a temporary working directory, read-only sandbox, and ephemeral session. No project instructions such as `CLAUDE.md` or `AGENTS.md` are present in the temporary directory. The ping uses the CLI's own existing login (a subscription login works; `CLAUDE_CODE_SIMPLE`/bare mode, which requires an API key, is never set). Successful output is discarded. On failure only a short single-line reason is logged: the exit code plus the first 150 characters of the CLI's stderr/stdout (for example `fail exit 1: Not logged in` or a usage-limit message), with anything token- or key-shaped redacted.

Each attempt has a 60-second timeout. Failures are retried after five minutes, for six attempts total, then skipped until the next due ping. A single provider failure does not crash the scheduler. One short result line per attempt is written to stdout and `pinger.log`.

**Boot and missed-ping catch-up.** The *active period* of a day runs from the first scheduled slot until the last slot plus the gap (07:00 until 22:06 for the example). Outside it (night) there are no pings. Every loop tick, and on startup or boot, the scheduler checks: if now is inside the active period and there has been no successful ping within the last `gap_minutes`, it pings immediately. If the machine boots at 09:30 with a 07:00 slot, the ping happens at 09:30. Afterwards the next ping is at `max(next scheduled slot, last successful ping + gap)`: with the example schedule and a 09:30 boot ping, the next ping is 14:32, then 19:34 if that is still inside the active period (it ends 22:06), and then nothing until the next day, which starts again at the start time. A ping is never sent while a window opened by the pinger is still active, because the next ping is never earlier than the last success plus the gap. A round that fails all six attempts is skipped until the next scheduled slot, and a restart in the middle of a round does not repeat it.

State is kept per provider in the state file (default `.pinger-state.json`): the time of the last successful ping and the start of the last attempt round, written before the round runs so restarts never cause duplicate pings. `pinger ping-now` records its successes too. Older state files in the previous format are ignored.

The CLI arguments are verified against the installed `claude --help` and `codex exec --help` output. Claude uses documented `--print`, `--model`, `--effort low`, `--tools`, and `--no-session-persistence` flags. Codex uses `exec --ephemeral --ignore-user-config --model`, `-c model_reasoning_effort=low`, `--sandbox read-only`, and `--cd`. If a future CLI version changes these options, check its help before updating the command builder. Official references: [Claude Code CLI usage](https://docs.anthropic.com/en/docs/claude-code/cli-usage) and [Codex CLI documentation](https://developers.openai.com/codex/cli/).

## Choosing your schedule

Choose a start time near when you usually wake up or begin work. Three pings cover a typical day. Four pings add one more late-day reset, if you need a window later at night; each additional ping also makes a provider request.

The active period lasts from the start time until the last ping plus the gap (08:00 until 23:06 for three pings from 08:00), so choose the start time and ping count to cover your day; pings are never sent at night.

For example, start at 08:00 with a 302-minute gap to get approximately 08:00, 13:02, and 18:04. Set `pings_per_day = 4` for another ping at 23:06. Keep the start time local to the intended daily routine.

## Install, configure, and run

Python 3.11 or newer is required. The scheduler uses only the Python standard library. Install the package in a virtualenv (`python3 -m venv .venv && .venv/bin/pip install .`) or use the automated deployment below, then copy `config.example.toml` to `config.toml` and edit it. The config file is ignored by Git. Authentication stays in each CLI's own login; this tool does not store API keys.

Check today's plan with `pinger schedule`; it takes the last ping into account (`09:30 (done)`, then the projected `14:32`, `19:34`; `(due now)` marks a ping that would run on the next tick). Send one immediate request per enabled provider with `pinger ping-now`. Start the persistent scheduler with `pinger run`. These commands use `config.toml` in the current directory; pass `--config /path/to/config.toml` to choose another file.

### systemd user service (automatic)

Deployment (`.linko-deploy.json` and `scripts/`) sets everything up; no manual unit is needed:

- Installs the tool into a dedicated venv, `~/.local/share/5h-bot-pinger/venv` (no `pip --user`, which externally-managed Pythons block).
- Writes `~/.config/systemd/user/pinger.service` (`ExecStart=<venv>/bin/pinger run --config ~/.config/5h-bot-pinger/config.toml`, `Restart=always`, `RestartSec=30`, `WantedBy=default.target`, plus `Environment=HOME=...` and `Environment=PATH=...`), then runs `systemctl --user daemon-reload`, `enable` and `restart`.
- The unit sets `HOME` and a `PATH` that starts with the directory of the `claude` binary resolved at deploy time (`command -v claude`, then `~/.local/bin` and `~/.claude/local`), because systemd user services get a minimal environment. If `claude` is not found, deploy warns and the health check fails; install and log in to Claude Code, then redeploy.
- Creates `~/.config/5h-bot-pinger/config.toml` from `config.example.toml` only if it does not exist, with `[codex] enabled = false` and Claude enabled; log and state files go under `~/.local/share/5h-bot-pinger/`. An existing config is never overwritten. Set `enabled = true` under `[codex]` once you have a Codex subscription.
- Tries `loginctl enable-linger "$USER"` so the service runs without a login session. If that is not allowed without sudo, deploy logs a warning and continues; run the command yourself once.

Inspect output with `journalctl --user -u pinger.service`. XDG_DATA_HOME and XDG_CONFIG_HOME are honored.

### Deployment hooks

- `scripts/backup.sh` saves the first parent revision to `.deploy-backups/` (override with `PINGER_DEPLOY_BACKUP_DIR`; ignored by Git) and records whether the unit and venv existed.
- `scripts/health.sh` requires `systemctl --user is-active pinger.service`, a `claude` executable in the directory list of the unit's `PATH`, and a successful `pinger schedule` with the deployed config.
- `scripts/rollback.sh` rebuilds the previous venv and restores the previous unit; on a first install it stops, disables and removes the new unit and venv, tolerating "not loaded".

A failing provider (missing binary, not logged in, no subscription, unreachable) only affects itself: each provider pings and retries on its own (every five minutes, six attempts), and unexpected errors in the scheduler loop are logged while it keeps running.

### Cron alternative

Cron is less suitable because it cannot keep the scheduler running to catch up missed pings after a boot. To launch it at boot, add `@reboot cd /path/to/checkout && /path/to/pinger run` to your crontab. The process must remain running; configure cron's environment and paths appropriately.

The application log defaults to `pinger.log` in the current directory. It contains timestamps, provider names, model names, outcome, and a short failure reason. On failures it records only the short redacted reason described above, never credentials.

## Cost

The requests use the configured small model and a very short prompt, and isolation avoids loading project files and tools, which helps keep input small. The exact amount charged or usage consumed depends on provider pricing and the CLI's hidden system context; “near-zero” is not “free.” Each request may count slightly toward weekly subscription limits.

## Limitations and disclaimer

This relies on the providers' current five-hour window behavior, which can change and may vary by plan. It cannot guarantee available capacity or a reset at a precise time. Use the CLIs and subscriptions within their providers' terms. This project is not affiliated with Anthropic or OpenAI.

## License

MIT. See [LICENSE](LICENSE).
