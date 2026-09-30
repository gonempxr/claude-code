"""Reads Claude Code usage from files Claude Code keeps on this machine.

Two sources:
- a snapshot written by deploy/claude_statusline.py (Claude Code pipes the
  subscription rate limits, in percent, to the status line script);
- Claude Code's own transcripts in <home>/projects/**/*.jsonl, for token totals
  and "is it working right now".
Only Claude Code sessions running on this machine show up here.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SNAPSHOT_NAME = "tracker_snapshot.json"
STALE_AFTER = 600  # seconds before limit data is flagged as old
ACTIVE_WITHIN = 120  # a reply this recent counts as "working now"


def fmt_span(seconds: float) -> str:
    seconds = max(0, int(seconds))
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f"{d}д {h}ч"
    return f"{h}ч {m:02d}м" if h else f"{m}м"


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def load_snapshot(home: Path) -> dict | None:
    try:
        return json.loads((home / SNAPSHOT_NAME).read_text())
    except (OSError, ValueError):
        return None


def _parse_ts(value) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def scan_tokens(home: Path, now: int, tz: ZoneInfo) -> dict:
    """Token totals per window, deduplicated by message id (transcripts repeat messages)."""
    midnight = datetime.fromtimestamp(now, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    windows = {
        "5ч": now - 5 * 3600,
        "сегодня": int(midnight.timestamp()),
        "7 дней": now - 7 * 86400,
    }
    oldest_needed = min(windows.values())
    seen: dict[str, tuple[float, dict, str]] = {}
    projects = home / "projects"
    files = projects.rglob("*.jsonl") if projects.is_dir() else []
    for f in files:
        try:
            if f.stat().st_mtime < oldest_needed:
                continue
            project = f.relative_to(projects).parts[0]
            with f.open(errors="replace") as fh:
                for line in fh:
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    msg = d.get("message")
                    if d.get("type") != "assistant" or not isinstance(msg, dict):
                        continue
                    usage, ts = msg.get("usage"), _parse_ts(d.get("timestamp"))
                    key = msg.get("id") or d.get("uuid")
                    if not isinstance(usage, dict) or ts is None or not key:
                        continue
                    seen[key] = (ts, usage, project)  # later copies carry the final output count
        except OSError:
            continue

    totals = {w: {"in": 0, "cache_write": 0, "cache_read": 0, "out": 0, "messages": 0} for w in windows}
    last_ts, last_project = None, None
    for ts, usage, project in seen.values():
        if last_ts is None or ts > last_ts:
            last_ts, last_project = ts, project
        for name, since in windows.items():
            if ts >= since:
                t = totals[name]
                t["in"] += usage.get("input_tokens") or 0
                t["cache_write"] += usage.get("cache_creation_input_tokens") or 0
                t["cache_read"] += usage.get("cache_read_input_tokens") or 0
                t["out"] += usage.get("output_tokens") or 0
                t["messages"] += 1
    return {"totals": totals, "last_ts": last_ts, "last_project": last_project}


def _limits_block(home: Path, now: int) -> list[str]:
    snap = load_snapshot(home)
    if snap is None:
        return [
            "Лимиты подписки: данных нет.",
            "Подключи статус-строку Claude Code: python3 deploy/install_statusline.py",
        ]
    rl = snap.get("rate_limits") or {}
    lines = []
    for key, label in (("five_hour", "5 часов"), ("seven_day", "7 дней")):
        w = rl.get(key)
        if not isinstance(w, dict) or "used_percentage" not in w:
            continue
        resets = w.get("resets_at")
        if isinstance(resets, (int, float)) and resets <= now:
            lines.append(f"{label}: окно сброшено, новых данных пока нет")
        else:
            tail = f" · сброс через {fmt_span(resets - now)}" if isinstance(resets, (int, float)) else ""
            lines.append(f"{label}: {w['used_percentage']:.0f}%{tail}")
    if not lines:
        return ["Лимиты подписки: Claude Code их не передал (нужна подписка Pro/Max и хотя бы один ответ в сессии)."]
    captured = snap.get("limits_captured_at")
    if isinstance(captured, (int, float)) and now - captured > STALE_AFTER:
        lines.append(f"(данные {fmt_span(now - captured)} назад, обновятся при следующем ответе Claude Code)")
    return ["Лимиты подписки:"] + [f"  {ln}" for ln in lines]


def report(home: Path | None, now: int, tz: ZoneInfo) -> str:
    if home is None:
        return "Путь к данным Claude Code не задан."
    out = _limits_block(home, now)
    scan = scan_tokens(home, now, tz)
    out.append("")
    out.append("Токены (локальные сессии Claude Code):")
    for name, t in scan["totals"].items():
        out.append(
            f"  {name}: вход {fmt_tokens(t['in'] + t['cache_write'])} · "
            f"кэш {fmt_tokens(t['cache_read'])} · выход {fmt_tokens(t['out'])} · ответов {t['messages']}"
        )
    out.append("Лимит подписки считается не в токенах, ориентируйся на проценты выше.")
    if scan["last_ts"] is None:
        out.append("Активности Claude Code за 7 дней не найдено.")
    else:
        ago = now - scan["last_ts"]
        state = "идёт работа" if ago <= ACTIVE_WITHIN else "простаивает"
        project = scan["last_project"].strip("-")[-40:]
        out.append(f"Claude Code: {state}, последний ответ {fmt_span(ago)} назад ({project})")
    return "\n".join(out)
