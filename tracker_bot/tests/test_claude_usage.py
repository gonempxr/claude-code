import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import claude_usage
import db
from commands import Tracker

UTC = ZoneInfo("UTC")
NOW = int(datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc).timestamp())


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _line(msg_id, ts, out=10, inp=1, cw=100, cr=1000, typ="assistant"):
    return json.dumps({
        "type": typ, "timestamp": _iso(ts), "uuid": f"u-{msg_id}-{ts}",
        "message": {"id": msg_id, "usage": {
            "input_tokens": inp, "cache_creation_input_tokens": cw,
            "cache_read_input_tokens": cr, "output_tokens": out}},
    })


def _write(home, project, name, lines):
    d = home / "projects" / project
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text("\n".join(lines) + "\n")


def test_dedupes_repeated_messages_and_keeps_final_output(tmp_path):
    # the same message is logged 3 times while streaming; the last copy has the final count
    _write(tmp_path, "-p1", "a.jsonl", [
        _line("m1", NOW - 60, out=5), _line("m1", NOW - 60, out=20), _line("m1", NOW - 60, out=42),
    ])
    # a resumed session copies the same message into another file
    _write(tmp_path, "-p2", "b.jsonl", [_line("m1", NOW - 60, out=42)])
    t = claude_usage.scan_tokens(tmp_path, NOW, UTC)["totals"]["5ч"]
    assert t["messages"] == 1 and t["out"] == 42 and t["cache_read"] == 1000


def test_windows_split_by_age(tmp_path):
    _write(tmp_path, "-p", "a.jsonl", [
        _line("recent", NOW - 3600),             # in every window
        _line("earlier_today", NOW - 7 * 3600),  # 05:00: today + 7d, not 5h
        _line("two_days", NOW - 2 * 86400),      # only 7d
        _line("ancient", NOW - 10 * 86400),      # none
    ])
    t = claude_usage.scan_tokens(tmp_path, NOW, UTC)["totals"]
    assert (t["5ч"]["messages"], t["сегодня"]["messages"], t["7 дней"]["messages"]) == (1, 2, 3)


def test_ignores_non_assistant_and_corrupt_lines(tmp_path):
    _write(tmp_path, "-p", "a.jsonl", [
        "not json", _line("u1", NOW - 10, typ="user"), json.dumps({"type": "assistant", "message": "str"}),
        _line("ok", NOW - 10),
    ])
    assert claude_usage.scan_tokens(tmp_path, NOW, UTC)["totals"]["5ч"]["messages"] == 1


def test_missing_home_does_not_crash(tmp_path):
    out = claude_usage.report(tmp_path / "nope", NOW, UTC)
    assert "данных нет" in out and "Активности Claude Code за 7 дней не найдено" in out
    assert "не задан" in claude_usage.report(None, NOW, UTC)


def test_activity_state(tmp_path):
    _write(tmp_path, "-home-me-proj", "a.jsonl", [_line("m", NOW - 30)])
    assert "идёт работа" in claude_usage.report(tmp_path, NOW, UTC)
    assert "простаивает" in claude_usage.report(tmp_path, NOW + 3600, UTC)


def _snapshot(home, limits, captured):
    (home / "tracker_snapshot.json").write_text(json.dumps({"rate_limits": limits, "limits_captured_at": captured}))


def test_limits_shown_with_reset(tmp_path):
    _snapshot(tmp_path, {"five_hour": {"used_percentage": 23.5, "resets_at": NOW + 2 * 3600},
                         "seven_day": {"used_percentage": 41.2, "resets_at": NOW + 3 * 86400}}, NOW - 30)
    out = claude_usage.report(tmp_path, NOW, UTC)
    assert "5 часов: 24% · сброс через 2ч 00м" in out
    assert "7 дней: 41% · сброс через 3д 0ч" in out
    assert "назад, обновятся" not in out


def test_stale_and_expired_limits_are_flagged(tmp_path):
    _snapshot(tmp_path, {"five_hour": {"used_percentage": 90, "resets_at": NOW - 5}}, NOW - 7200)
    out = claude_usage.report(tmp_path, NOW, UTC)
    assert "окно сброшено" in out and "90%" not in out
    assert "2ч 00м назад" in out


def test_snapshot_without_limits(tmp_path):
    _snapshot(tmp_path, {}, NOW)
    assert "Pro/Max" in claude_usage.report(tmp_path, NOW, UTC)


def test_claude_command_and_help(tmp_path):
    _write(tmp_path, "-p", "a.jsonl", [_line("m", NOW - 30)])
    t = Tracker(db.connect(":memory:"), "UTC", tmp_path)
    assert "Токены" in t.execute("claude", NOW)
    assert "claude" in t.execute("help", NOW)
    assert "не задан" in Tracker(db.connect(":memory:"), "UTC").execute("claude", NOW)


# ---- statusline scripts (run as real subprocesses) ----
def _run(script, home, stdin=None, args=()):
    return subprocess.run([sys.executable, str(ROOT / "deploy" / script), *args], input=stdin, text=True,
                          capture_output=True, env={"CLAUDE_HOME": str(home), "PATH": "/usr/bin:/bin"})


def test_statusline_writes_snapshot_and_keeps_limits_when_absent(tmp_path):
    first = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": 4102444800}}})
    r = _run("claude_statusline.py", tmp_path, first)
    assert r.returncode == 0 and r.stdout.strip() == "5ч 24%"
    snap = json.loads((tmp_path / "tracker_snapshot.json").read_text())
    captured = snap["limits_captured_at"]
    # a new session's first updates have no rate_limits: the old value must survive
    r = _run("claude_statusline.py", tmp_path, json.dumps({"model": {}}))
    snap = json.loads((tmp_path / "tracker_snapshot.json").read_text())
    assert r.stdout.strip() == "5ч 24%"
    assert snap["rate_limits"]["five_hour"]["used_percentage"] == 23.5
    assert snap["limits_captured_at"] == captured  # freshness of the limits is not faked


def test_statusline_survives_garbage_input(tmp_path):
    r = _run("claude_statusline.py", tmp_path, "garbage")
    assert r.returncode == 0 and r.stdout.strip() == "—"


def test_install_statusline_preserves_settings_and_refuses_overwrite(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"theme": "dark"}))
    assert _run("install_statusline.py", tmp_path).returncode == 0
    settings = json.loads((tmp_path / "settings.json").read_text())
    assert settings["theme"] == "dark" and "claude_statusline.py" in settings["statusLine"]["command"]
    assert (tmp_path / "settings.json.bak").exists()
    assert _run("install_statusline.py", tmp_path).returncode == 0  # idempotent

    (tmp_path / "settings.json").write_text(json.dumps({"statusLine": {"type": "command", "command": "mine.sh"}}))
    r = _run("install_statusline.py", tmp_path)
    assert r.returncode != 0 and "already have" in r.stderr
    assert json.loads((tmp_path / "settings.json").read_text())["statusLine"]["command"] == "mine.sh"
    assert _run("install_statusline.py", tmp_path, args=["--force"]).returncode == 0
