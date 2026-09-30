import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import db
from commands import Tracker

T0 = 1_750_000_000  # arbitrary fixed instant


@pytest.fixture
def t():
    return Tracker(db.connect(":memory:"), "UTC")


def test_tasks_lifecycle(t):
    assert "No open tasks" in t.execute("tasks", T0)
    assert "#1" in t.execute("task write report", T0)
    assert "#1 write report" in t.execute("tasks", T0)
    assert "Done #1" in t.execute("done 1", T0)
    assert "already done" in t.execute("done 1", T0)
    assert "No task #9" in t.execute("done 9", T0)
    assert "not a task id" in t.execute("done abc", T0)


def test_timer_and_report(t):
    t.execute("task deep work", T0)
    assert "Timer started on task #1" in t.execute("track 1", T0)
    assert "already running" in t.execute("track", T0 + 60)
    assert "1h 30m" in t.execute("timer", T0 + 5400)
    assert "1h 30m" in t.execute("stop", T0 + 5400)
    assert "No timer" in t.execute("stop", T0 + 5401)
    out = t.execute("report week", T0 + 5401)
    assert "deep work" in out and "1h 30m" in out


def test_report_counts_running_timer_and_clips_to_period(t):
    # session started yesterday-ish, still running: "today" must only count since local midnight
    midnight = T0 - (T0 % 86400)
    t.execute("track", midnight - 7200)
    out = t.execute("report today", midnight + 3600)
    assert "Worked today: 1h 00m" in out


def test_limit_rolling_window(t):
    assert "10 messages per 5h" in t.execute("limit claude 10 5 messages", T0)
    t.execute("use claude 4", T0)
    out = t.execute("use claude 4", T0 + 3600)
    assert "8/10" in out and "WARNING" in out
    # the first 4 fall out of the 5h window
    out = t.execute("limits", T0 + 5 * 3600 + 1)
    assert "4/10" in out and "WARNING" not in out
    out = t.execute("use claude 7", T0 + 5 * 3600 + 2)
    assert "OVER LIMIT" in out


def test_limit_reset_time(t):
    t.execute("limit a 10 5", T0)
    out = t.execute("use a", T0 + 3600)
    assert "frees up in 5h 00m" in out  # oldest entry = this one, window 5h


def test_limit_errors(t):
    assert "No limit 'x'" in t.execute("use x", T0)
    assert "must be a number" in t.execute("limit a ten 5", T0)
    assert "must be positive" in t.execute("limit a 10 0", T0)
    assert "Usage" in t.execute("limit a", T0)
    t.execute("limit a 10 5", T0)
    assert "Deleted" in t.execute("unlimit a", T0)
    assert "No limit" in t.execute("unlimit a", T0)


def test_unknown_and_help(t):
    assert "Unknown command" in t.execute("wat", T0)
    assert t.execute("", T0).startswith("Tasks")
