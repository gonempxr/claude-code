# Personal tracker bot (Telegram + Discord)

One Python process, one SQLite file. Tracks tasks, work time, and manually logged usage limits.
Only the user IDs set in `.env` can use it; everyone else is ignored.

## Setup
1. **Telegram**: talk to @BotFather, `/newbot`, copy the token. Get your numeric user id from @userinfobot.
2. **Discord**: https://discord.com/developers/applications -> New Application -> Bot -> copy token,
   enable **Message Content Intent**. Invite it with the `bot` scope (Send Messages). Your user id:
   Settings -> Advanced -> Developer Mode, then right-click yourself -> Copy User ID.
3. `cp .env.example .env`, fill it in, then:
   ```
   pip install -r requirements.txt
   set -a; . ./.env; set +a
   python main.py
   ```
The bottom keyboard in Telegram (Старт, Стоп, Таймер, Задачи, Отчёт, Лимиты) replaces typing the common commands. Under `Задачи` and `Лимиты` there are inline buttons (start a task, mark it done, log +1 usage).
Telegram commands start with `/`, Discord commands with `!` (e.g. `/tasks` vs `!tasks`). Send `help` for the list.

## Notifications (Telegram)
The bot checks once a minute and writes to you first:
- a daily summary (time worked, tasks closed, limits) at 21:00 in `TZ_NAME`; change with `summary 20:30`, disable with `summary off`;
- a reminder when a timer runs longer than 3 hours (`longtimer 2`, `longtimer off`);
- "limit is free again" when a limit you had used to 80%+ drops back under it.
They only arrive while the bot process is running. A message that fails to send (no network) is not retried.

## Run in the background on macOS (free)
Stop the bot in Terminal first (two copies cannot poll one Telegram token), then from `tracker_bot`:
```
bash deploy/install_macos.sh
```
The bot starts at login, restarts after a crash, and logs to `logs/bot.log`. `bash deploy/install_macos.sh uninstall` removes it.
It runs only while the Mac is awake; with the lid closed or asleep the bot stops and catches up when the Mac wakes.
After `git pull`, restart it with `launchctl kickstart -k gui/$(id -u)/com.personal.trackerbot`.
Not tested on a real Mac by the author of these scripts: the generated plist was only validated for syntax.

## Claude usage in chat (`claude` command / 🤖 Claude button)
Shows subscription limits (5-hour and 7-day, in %), token totals and whether Claude Code is working right now.
It reads files Claude Code writes on the machine where the bot runs, so it only sees **Claude Code sessions on that machine**
(not claude.ai chats, the mobile app, or cloud sessions).
1. `python3 deploy/install_statusline.py` points Claude Code's status line at `deploy/claude_statusline.py`
   (refuses to replace a status line you already have unless `--force`; backs up `settings.json`). Restart Claude Code.
2. Limit percentages appear only for Pro/Max subscribers and only after Claude Code's first reply in a session,
   and refresh on every reply. The bot marks data older than 10 minutes.
Token totals come from `~/.claude/projects` transcripts and work without step 1. Subscription limits are not measured in tokens.
`CLAUDE_HOME` overrides the `~/.claude` location.

## Limits
Limits are rolling windows you log by hand (`limit claude-5h 45 5 messages`, then `use claude-5h`).
There is no API to read Claude subscription usage, so the bot cannot fetch it automatically.

## Tests
`python -m pytest tests`
