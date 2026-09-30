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

## Limits
Limits are rolling windows you log by hand (`limit claude-5h 45 5 messages`, then `use claude-5h`).
There is no API to read Claude subscription usage, so the bot cannot fetch it automatically.

## Tests
`python -m pytest tests`
