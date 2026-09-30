"""Platform-independent command handling: text in, text out.

Both the Telegram and Discord adapters only strip their own prefix and call
`execute`, so behaviour is identical on both. `due_notifications` produces the
proactive messages (daily summary, long timer, limit recovered) for the
Telegram adapter's background loop.
"""
import re
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import claude_usage

HELP = """Задачи
  task <текст>         добавить задачу
  tasks                открытые задачи
  done <id>            закрыть задачу
Время
  track [id] [заметка] включить таймер (можно на задачу)
  stop                 остановить таймер
  timer                что сейчас идёт
  report [today|week]  сколько отработано (по умолчанию today)
Лимиты (скользящее окно)
  limit <имя> <макс> <часов> [единица]   напр. limit claude-5h 45 5 messages
  unlimit <имя>        удалить лимит и его историю
  use <имя> [сколько]  записать расход (по умолчанию 1)
  limits               расход по всем лимитам
Уведомления
  summary HH:MM|off    время ежедневной сводки (по умолчанию 21:00)
  longtimer <часов>|off  напомнить, если таймер идёт дольше (по умолчанию 3)
  settings             текущие настройки
Claude
  claude               лимиты подписки, токены, идёт ли работа"""

DEFAULT_SUMMARY = "21:00"
DEFAULT_LONG_TIMER = "3"
WARN_PCT = 80

PERIODS = {"today": "today", "сегодня": "today", "week": "week", "неделя": "week"}
PERIOD_TITLE = {"today": "сегодня", "week": "на этой неделе"}


class CommandError(Exception):
    pass


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}ч {m:02d}м" if h else f"{m}м"


def fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def _parse_number(s: str, what: str) -> float:
    try:
        value = float(s.replace(",", "."))
    except ValueError:
        raise CommandError(f"{what}: нужно число, а не '{s}'")
    if value <= 0:
        raise CommandError(f"{what}: число должно быть больше нуля")
    return value


def _parse_id(s: str) -> int:
    if not s.isdigit():
        raise CommandError(f"'{s}' не похоже на номер задачи")
    return int(s)


class Tracker:
    def __init__(self, conn: sqlite3.Connection, tz: str = "UTC", claude_home: Path | None = None):
        self.db = conn
        self.tz = ZoneInfo(tz)
        self.claude_home = claude_home

    # ---- settings storage ----
    def _get(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def _set(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.db.commit()

    # ---- tasks ----
    def add_task(self, title: str, now: int) -> str:
        if not title:
            raise CommandError("Использование: task <текст>")
        cur = self.db.execute("INSERT INTO tasks(title, created_at) VALUES (?, ?)", (title, now))
        self.db.commit()
        return f"Добавлена задача #{cur.lastrowid}: {title}"

    def open_tasks(self) -> list[tuple[int, str]]:
        rows = self.db.execute("SELECT id, title FROM tasks WHERE done_at IS NULL ORDER BY id").fetchall()
        return [(r["id"], r["title"]) for r in rows]

    def limit_names(self) -> list[str]:
        return [r["name"] for r in self.db.execute("SELECT name FROM limits ORDER BY name")]

    def list_tasks(self) -> str:
        tasks = self.open_tasks()
        if not tasks:
            return "Открытых задач нет."
        return "\n".join(f"#{i} {title}" for i, title in tasks)

    def done_task(self, task_id: int, now: int) -> str:
        row = self.db.execute("SELECT title, done_at FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise CommandError(f"Нет задачи #{task_id}")
        if row["done_at"] is not None:
            raise CommandError(f"Задача #{task_id} уже закрыта")
        self.db.execute("UPDATE tasks SET done_at = ? WHERE id = ?", (now, task_id))
        self.db.commit()
        return f"Готово #{task_id}: {row['title']}"

    # ---- timer ----
    def _active(self):
        return self.db.execute(
            "SELECT s.id, s.started_at, s.note, t.title FROM sessions s "
            "LEFT JOIN tasks t ON t.id = s.task_id WHERE s.ended_at IS NULL"
        ).fetchone()

    @staticmethod
    def _label(row) -> str:
        return row["title"] or row["note"] or "без названия"

    def start(self, args: list[str], now: int) -> str:
        active = self._active()
        if active:
            raise CommandError(
                f"Таймер уже идёт: '{self._label(active)}' "
                f"({fmt_duration(now - active['started_at'])}). Сначала stop."
            )
        task_id, note = None, " ".join(args) or None
        if args and args[0].isdigit():
            task_id, note = int(args[0]), " ".join(args[1:]) or None
            task = self.db.execute("SELECT title, done_at FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not task:
                raise CommandError(f"Нет задачи #{task_id}")
            if task["done_at"] is not None:
                raise CommandError(f"Задача #{task_id} уже закрыта")
        self.db.execute(
            "INSERT INTO sessions(task_id, note, started_at) VALUES (?, ?, ?)", (task_id, note, now)
        )
        self.db.commit()
        return "Таймер запущен" + (f" на задаче #{task_id}" if task_id else "") + (f": {note}" if note else "")

    def stop(self, now: int) -> str:
        active = self._active()
        if not active:
            raise CommandError("Таймер не запущен.")
        self.db.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (now, active["id"]))
        self.db.commit()
        return f"Остановлено '{self._label(active)}': {fmt_duration(now - active['started_at'])}"

    def timer(self, now: int) -> str:
        active = self._active()
        if not active:
            return "Таймер не запущен."
        return f"Идёт '{self._label(active)}': {fmt_duration(now - active['started_at'])}"

    def _midnight(self, now: int) -> datetime:
        return datetime.fromtimestamp(now, self.tz).replace(hour=0, minute=0, second=0, microsecond=0)

    def report(self, period: str, now: int) -> str:
        period = PERIODS.get(period)
        if period is None:
            raise CommandError("Использование: report [today|week]")
        midnight = self._midnight(now)
        since = midnight if period == "today" else midnight - timedelta(days=midnight.weekday())  # Monday
        since_ts = int(since.timestamp())
        rows = self.db.execute(
            "SELECT s.started_at, s.ended_at, s.note, t.title FROM sessions s "
            "LEFT JOIN tasks t ON t.id = s.task_id "
            "WHERE COALESCE(s.ended_at, ?) > ?",
            (now, since_ts),
        ).fetchall()
        totals: dict[str, float] = {}
        for r in rows:
            start = max(r["started_at"], since_ts)
            end = r["ended_at"] if r["ended_at"] is not None else now
            totals[self._label(r)] = totals.get(self._label(r), 0) + (end - start)
        title = PERIOD_TITLE[period]
        if not totals:
            return f"Время {title} не записано."
        lines = [f"{fmt_duration(v)}  {k}" for k, v in sorted(totals.items(), key=lambda kv: -kv[1])]
        return f"Отработано {title}: {fmt_duration(sum(totals.values()))}\n" + "\n".join(lines)

    # ---- limits ----
    def set_limit(self, args: list[str]) -> str:
        if len(args) < 3:
            raise CommandError("Использование: limit <имя> <макс> <часов> [единица]")
        name = args[0].lower()
        cap = _parse_number(args[1], "макс")
        window = _parse_number(args[2], "часов")
        unit = args[3] if len(args) > 3 else "шт."
        self.db.execute(
            "INSERT INTO limits(name, cap, window_hours, unit) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET cap=excluded.cap, window_hours=excluded.window_hours, unit=excluded.unit",
            (name, cap, window, unit),
        )
        self.db.commit()
        return f"Лимит '{name}': {fmt_num(cap)} {unit} за {fmt_num(window)}ч (скользящее окно)"

    def delete_limit(self, name: str) -> str:
        name = name.lower()
        cur = self.db.execute("DELETE FROM limits WHERE name = ?", (name,))
        self.db.execute("DELETE FROM settings WHERE key = ?", (f"limit_level:{name}",))
        self.db.commit()
        if not cur.rowcount:
            raise CommandError(f"Нет лимита '{name}'")
        return f"Лимит '{name}' удалён"

    def use(self, args: list[str], now: int) -> str:
        if not args:
            raise CommandError("Использование: use <имя> [сколько]")
        name = args[0].lower()
        amount = _parse_number(args[1], "сколько") if len(args) > 1 else 1.0
        if not self.db.execute("SELECT 1 FROM limits WHERE name = ?", (name,)).fetchone():
            raise CommandError(f"Нет лимита '{name}'. Создай: limit <имя> <макс> <часов> [единица]")
        self.db.execute("INSERT INTO usage(limit_name, amount, at) VALUES (?, ?, ?)", (name, amount, now))
        self.db.commit()
        return self._limit_line(self.db.execute("SELECT * FROM limits WHERE name = ?", (name,)).fetchone(), now)

    def _usage(self, lim, now: int):
        since = now - int(lim["window_hours"] * 3600)
        row = self.db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS used, MIN(at) AS oldest FROM usage WHERE limit_name = ? AND at > ?",
            (lim["name"], since),
        ).fetchone()
        return row["used"], row["oldest"]

    @staticmethod
    def _level(used: float, cap: float) -> int:
        """0 = fine, 1 = at/over the warning threshold, 2 = over the limit."""
        if used > cap:
            return 2
        return 1 if used / cap * 100 >= WARN_PCT else 0

    def _limit_line(self, lim, now: int) -> str:
        used, oldest = self._usage(lim, now)
        cap = lim["cap"]
        flag = {0: "", 1: " ⚠ почти лимит", 2: " ⛔ лимит превышен"}[self._level(used, cap)]
        line = f"{lim['name']}: {fmt_num(used)}/{fmt_num(cap)} {lim['unit']} ({used / cap * 100:.0f}%){flag}"
        if oldest is not None:
            line += f", старая запись освободится через {fmt_duration(oldest + int(lim['window_hours'] * 3600) - now)}"
        return line

    def limits(self, now: int) -> str:
        rows = self.db.execute("SELECT * FROM limits ORDER BY name").fetchall()
        if not rows:
            return "Лимитов нет. Создай: limit <имя> <макс> <часов> [единица]"
        return "\n".join(self._limit_line(r, now) for r in rows)

    # ---- notification settings ----
    def set_summary(self, args: list[str], now: int) -> str:
        if not args:
            raise CommandError("Использование: summary HH:MM или summary off")
        if args[0].lower() == "off":
            self._set("summary_time", "off")
            return "Ежедневная сводка выключена."
        m = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", args[0])
        if not m:
            raise CommandError("Время в формате HH:MM, например 21:00")
        value = f"{int(m.group(1)):02d}:{m.group(2)}"
        self._set("summary_time", value)
        # A time that has already passed today waits until tomorrow instead of firing at once.
        local = datetime.fromtimestamp(now, self.tz)
        if (local.hour, local.minute) >= (int(m.group(1)), int(m.group(2))):
            self._set("summary_last", local.date().isoformat())
        return f"Сводка каждый день в {value}."

    def set_long_timer(self, args: list[str]) -> str:
        if not args:
            raise CommandError("Использование: longtimer <часов> или longtimer off")
        if args[0].lower() == "off":
            self._set("long_timer_hours", "off")
            return "Напоминание о долгом таймере выключено."
        hours = _parse_number(args[0], "часов")
        self._set("long_timer_hours", fmt_num(hours))
        return f"Напомню, если таймер идёт дольше {fmt_num(hours)}ч."

    def settings(self) -> str:
        summary = self._get("summary_time", DEFAULT_SUMMARY)
        long_timer = self._get("long_timer_hours", DEFAULT_LONG_TIMER)
        return (
            f"Сводка дня: {'выкл' if summary == 'off' else summary}\n"
            f"Долгий таймер: {'выкл' if long_timer == 'off' else long_timer + 'ч'}\n"
            f"Часовой пояс: {self.tz.key}"
        )

    # ---- proactive messages ----
    def daily_summary(self, now: int) -> str:
        midnight_ts = int(self._midnight(now).timestamp())
        closed = self.db.execute("SELECT COUNT(*) FROM tasks WHERE done_at >= ?", (midnight_ts,)).fetchone()[0]
        parts = [
            "Итог дня",
            self.report("today", now),
            f"Закрыто задач: {closed}, открыто: {len(self.open_tasks())}",
        ]
        if self.limit_names():
            parts.append(self.limits(now))
        return "\n".join(parts)

    def due_notifications(self, now: int) -> list[str]:
        """Messages that should be pushed now. State is saved so each fires once."""
        out: list[str] = []
        local = datetime.fromtimestamp(now, self.tz)

        summary = self._get("summary_time", DEFAULT_SUMMARY)
        if summary != "off":
            h, m = map(int, summary.split(":"))
            today = local.date().isoformat()
            if (local.hour, local.minute) >= (h, m) and self._get("summary_last") != today:
                self._set("summary_last", today)
                out.append(self.daily_summary(now))

        long_timer = self._get("long_timer_hours", DEFAULT_LONG_TIMER)
        active = self._active()
        if (
            active
            and long_timer != "off"
            and now - active["started_at"] >= float(long_timer) * 3600
            and self._get("timer_notified") != str(active["id"])
        ):
            self._set("timer_notified", str(active["id"]))
            out.append(
                f"Таймер идёт уже {fmt_duration(now - active['started_at'])}: '{self._label(active)}'. "
                "Не забыл остановить?"
            )

        for lim in self.db.execute("SELECT * FROM limits ORDER BY name").fetchall():
            used, _ = self._usage(lim, now)
            level = self._level(used, lim["cap"])
            key = f"limit_level:{lim['name']}"
            prev = int(self._get(key, "0"))
            if prev >= 1 and level == 0:
                out.append(f"Лимит снова свободен: {self._limit_line(lim, now)}")
            if prev != level:
                self._set(key, str(level))
        return out

    # ---- dispatch ----
    def execute(self, line: str, now: int) -> str:
        parts = line.strip().split()
        if not parts:
            return HELP
        cmd, args = parts[0].lower(), parts[1:]
        rest = line.strip()[len(parts[0]):].strip()
        try:
            if cmd == "help":
                return HELP
            if cmd == "task":
                return self.add_task(rest, now)
            if cmd == "tasks":
                return self.list_tasks()
            if cmd == "done":
                if not args:
                    raise CommandError("Использование: done <id>")
                return self.done_task(_parse_id(args[0]), now)
            if cmd == "track":
                return self.start(args, now)
            if cmd == "stop":
                return self.stop(now)
            if cmd == "timer":
                return self.timer(now)
            if cmd == "report":
                return self.report(args[0].lower() if args else "today", now)
            if cmd == "limit":
                return self.set_limit(args)
            if cmd == "unlimit":
                if not args:
                    raise CommandError("Использование: unlimit <имя>")
                return self.delete_limit(args[0])
            if cmd == "use":
                return self.use(args, now)
            if cmd == "limits":
                return self.limits(now)
            if cmd == "summary":
                return self.set_summary(args, now)
            if cmd == "longtimer":
                return self.set_long_timer(args)
            if cmd == "settings":
                return self.settings()
            if cmd == "claude":
                return claude_usage.report(self.claude_home, now, self.tz)
            return f"Неизвестная команда '{cmd}'.\n\n{HELP}"
        except CommandError as e:
            return str(e)
