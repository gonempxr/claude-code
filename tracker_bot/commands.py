"""Platform-independent command handling: text in, text out.

Both the Telegram and Discord adapters only strip their own prefix and call
`execute`, so behaviour is identical on both.
"""
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

HELP = """Tasks
  task <title>         add a task
  tasks                list open tasks
  done <id>            complete a task
Time
  track [id] [note]    start the timer (optionally on a task)
  stop                 stop the timer
  timer                show the running timer
  report [today|week]  time worked (default: today)
Usage limits (rolling window)
  limit <name> <cap> <window_hours> [unit]   e.g. limit claude-5h 45 5 messages
  unlimit <name>       delete a limit and its history
  use <name> [amount]  log usage (default 1)
  limits               show usage against every limit"""


class CommandError(Exception):
    pass


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    h, rem = divmod(seconds, 3600)
    m = rem // 60
    return f"{h}h {m:02d}m" if h else f"{m}m"


def fmt_num(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:g}"


def _parse_number(s: str, what: str) -> float:
    try:
        value = float(s)
    except ValueError:
        raise CommandError(f"{what} must be a number, got '{s}'")
    if value <= 0:
        raise CommandError(f"{what} must be positive")
    return value


def _parse_id(s: str) -> int:
    if not s.isdigit():
        raise CommandError(f"'{s}' is not a task id")
    return int(s)


class Tracker:
    def __init__(self, conn: sqlite3.Connection, tz: str = "UTC"):
        self.db = conn
        self.tz = ZoneInfo(tz)

    # ---- tasks ----
    def add_task(self, title: str, now: int) -> str:
        if not title:
            raise CommandError("Usage: task <title>")
        cur = self.db.execute("INSERT INTO tasks(title, created_at) VALUES (?, ?)", (title, now))
        self.db.commit()
        return f"Added task #{cur.lastrowid}: {title}"

    def list_tasks(self) -> str:
        rows = self.db.execute("SELECT id, title FROM tasks WHERE done_at IS NULL ORDER BY id").fetchall()
        if not rows:
            return "No open tasks."
        return "\n".join(f"#{r['id']} {r['title']}" for r in rows)

    def done_task(self, task_id: int, now: int) -> str:
        row = self.db.execute("SELECT title, done_at FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise CommandError(f"No task #{task_id}")
        if row["done_at"] is not None:
            raise CommandError(f"Task #{task_id} is already done")
        self.db.execute("UPDATE tasks SET done_at = ? WHERE id = ?", (now, task_id))
        self.db.commit()
        return f"Done #{task_id}: {row['title']}"

    # ---- timer ----
    def _active(self):
        return self.db.execute(
            "SELECT s.id, s.started_at, s.note, t.title FROM sessions s "
            "LEFT JOIN tasks t ON t.id = s.task_id WHERE s.ended_at IS NULL"
        ).fetchone()

    @staticmethod
    def _label(row) -> str:
        return row["title"] or row["note"] or "untitled"

    def start(self, args: list[str], now: int) -> str:
        active = self._active()
        if active:
            raise CommandError(
                f"Timer already running on '{self._label(active)}' "
                f"({fmt_duration(now - active['started_at'])}). Use stop first."
            )
        task_id, note = None, " ".join(args) or None
        if args and args[0].isdigit():
            task_id, note = int(args[0]), " ".join(args[1:]) or None
            task = self.db.execute("SELECT title, done_at FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not task:
                raise CommandError(f"No task #{task_id}")
            if task["done_at"] is not None:
                raise CommandError(f"Task #{task_id} is already done")
        self.db.execute(
            "INSERT INTO sessions(task_id, note, started_at) VALUES (?, ?, ?)", (task_id, note, now)
        )
        self.db.commit()
        return "Timer started" + (f" on task #{task_id}" if task_id else "") + (f": {note}" if note else "")

    def stop(self, now: int) -> str:
        active = self._active()
        if not active:
            raise CommandError("No timer is running.")
        self.db.execute("UPDATE sessions SET ended_at = ? WHERE id = ?", (now, active["id"]))
        self.db.commit()
        return f"Stopped '{self._label(active)}': {fmt_duration(now - active['started_at'])}"

    def timer(self, now: int) -> str:
        active = self._active()
        if not active:
            return "No timer is running."
        return f"Running on '{self._label(active)}': {fmt_duration(now - active['started_at'])}"

    def report(self, period: str, now: int) -> str:
        local_now = datetime.fromtimestamp(now, self.tz)
        midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        if period == "today":
            since = midnight
        elif period == "week":
            since = midnight - timedelta(days=midnight.weekday())  # Monday
        else:
            raise CommandError("Usage: report [today|week]")
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
        if not totals:
            return f"Nothing tracked ({period})."
        lines = [f"{fmt_duration(v)}  {k}" for k, v in sorted(totals.items(), key=lambda kv: -kv[1])]
        return f"Worked {period}: {fmt_duration(sum(totals.values()))}\n" + "\n".join(lines)

    # ---- limits ----
    def set_limit(self, args: list[str]) -> str:
        if len(args) < 3:
            raise CommandError("Usage: limit <name> <cap> <window_hours> [unit]")
        name = args[0].lower()
        cap = _parse_number(args[1], "cap")
        window = _parse_number(args[2], "window_hours")
        unit = args[3] if len(args) > 3 else "units"
        self.db.execute(
            "INSERT INTO limits(name, cap, window_hours, unit) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET cap=excluded.cap, window_hours=excluded.window_hours, unit=excluded.unit",
            (name, cap, window, unit),
        )
        self.db.commit()
        return f"Limit '{name}': {fmt_num(cap)} {unit} per {fmt_num(window)}h rolling"

    def delete_limit(self, name: str) -> str:
        cur = self.db.execute("DELETE FROM limits WHERE name = ?", (name.lower(),))
        self.db.commit()
        if not cur.rowcount:
            raise CommandError(f"No limit '{name}'")
        return f"Deleted limit '{name}'"

    def use(self, args: list[str], now: int) -> str:
        if not args:
            raise CommandError("Usage: use <name> [amount]")
        name = args[0].lower()
        amount = _parse_number(args[1], "amount") if len(args) > 1 else 1.0
        if not self.db.execute("SELECT 1 FROM limits WHERE name = ?", (name,)).fetchone():
            raise CommandError(f"No limit '{name}'. Create it with: limit <name> <cap> <window_hours> [unit]")
        self.db.execute("INSERT INTO usage(limit_name, amount, at) VALUES (?, ?, ?)", (name, amount, now))
        self.db.commit()
        return self._limit_line(self.db.execute("SELECT * FROM limits WHERE name = ?", (name,)).fetchone(), now)

    def _limit_line(self, lim, now: int) -> str:
        since = now - int(lim["window_hours"] * 3600)
        row = self.db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS used, MIN(at) AS oldest FROM usage WHERE limit_name = ? AND at > ?",
            (lim["name"], since),
        ).fetchone()
        used, cap = row["used"], lim["cap"]
        pct = used / cap * 100
        flag = " OVER LIMIT" if used > cap else (" WARNING" if pct >= 80 else "")
        line = f"{lim['name']}: {fmt_num(used)}/{fmt_num(cap)} {lim['unit']} ({pct:.0f}%){flag}"
        if row["oldest"] is not None:
            line += f", oldest entry frees up in {fmt_duration(row['oldest'] + int(lim['window_hours'] * 3600) - now)}"
        return line

    def limits(self, now: int) -> str:
        rows = self.db.execute("SELECT * FROM limits ORDER BY name").fetchall()
        if not rows:
            return "No limits set. Create one: limit <name> <cap> <window_hours> [unit]"
        return "\n".join(self._limit_line(r, now) for r in rows)

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
                    raise CommandError("Usage: done <id>")
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
                    raise CommandError("Usage: unlimit <name>")
                return self.delete_limit(args[0])
            if cmd == "use":
                return self.use(args, now)
            if cmd == "limits":
                return self.limits(now)
            return f"Unknown command '{cmd}'.\n\n{HELP}"
        except CommandError as e:
            return str(e)
