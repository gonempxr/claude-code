import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import db
from commands import Tracker

MIDNIGHT = 1_750_000_000 - (1_750_000_000 % 86400)  # a UTC midnight
T0 = MIDNIGHT + 10 * 3600  # 10:00 UTC, so "today" logic has room on both sides


@pytest.fixture
def t():
    return Tracker(db.connect(":memory:"), "UTC")


def test_tasks_lifecycle(t):
    assert "нет" in t.execute("tasks", T0)
    assert "#1" in t.execute("task написать отчёт", T0)
    assert "#1 написать отчёт" in t.execute("tasks", T0)
    assert "Готово #1" in t.execute("done 1", T0)
    assert "уже закрыта" in t.execute("done 1", T0)
    assert "Нет задачи #9" in t.execute("done 9", T0)
    assert "не похоже на номер" in t.execute("done abc", T0)


def test_timer_and_report(t):
    t.execute("task глубокая работа", T0)
    assert "Таймер запущен на задаче #1" in t.execute("track 1", T0)
    assert "уже идёт" in t.execute("track", T0 + 60)
    assert "1ч 30м" in t.execute("timer", T0 + 5400)
    assert "1ч 30м" in t.execute("stop", T0 + 5400)
    assert "не запущен" in t.execute("stop", T0 + 5401)
    out = t.execute("report week", T0 + 5401)
    assert "глубокая работа" in out and "1ч 30м" in out
    assert "1ч 30м" in t.execute("report сегодня", T0 + 5401)


def test_report_counts_running_timer_and_clips_to_period(t):
    # started 2h before midnight and still running: "today" must only count since midnight
    t.execute("track", MIDNIGHT - 7200)
    assert "Отработано сегодня: 1ч 00м" in t.execute("report today", MIDNIGHT + 3600)


def test_limit_rolling_window(t):
    assert "10 messages за 5ч" in t.execute("limit claude 10 5 messages", T0)
    t.execute("use claude 4", T0)
    out = t.execute("use claude 4", T0 + 3600)
    assert "8/10" in out and "почти лимит" in out
    # the first 4 fall out of the 5h window
    out = t.execute("limits", T0 + 5 * 3600 + 1)
    assert "4/10" in out and "почти лимит" not in out
    assert "лимит превышен" in t.execute("use claude 7", T0 + 5 * 3600 + 2)


def test_limit_reset_time(t):
    t.execute("limit a 10 5", T0)
    assert "через 5ч 00м" in t.execute("use a", T0 + 3600)


def test_limit_errors(t):
    assert "Нет лимита 'x'" in t.execute("use x", T0)
    assert "нужно число" in t.execute("limit a ten 5", T0)
    assert "больше нуля" in t.execute("limit a 10 0", T0)
    assert "Использование" in t.execute("limit a", T0)
    t.execute("limit a 10 5", T0)
    assert "удалён" in t.execute("unlimit a", T0)
    assert "Нет лимита" in t.execute("unlimit a", T0)


def test_unknown_and_help(t):
    assert "Неизвестная команда" in t.execute("wat", T0)
    assert t.execute("", T0).startswith("Задачи")


# ---- notifications ----
def test_daily_summary_fires_once_after_the_set_time(t):
    t.execute("summary 21:00", T0)
    assert t.due_notifications(MIDNIGHT + 20 * 3600) == []
    first = t.due_notifications(MIDNIGHT + 21 * 3600 + 5)
    assert len(first) == 1 and first[0].startswith("Итог дня")
    assert t.due_notifications(MIDNIGHT + 22 * 3600) == []  # not repeated the same day
    assert len(t.due_notifications(MIDNIGHT + 86400 + 21 * 3600)) == 1  # next day again


def test_daily_summary_content(t):
    t.execute("task a", T0)
    t.execute("task b", T0)
    t.execute("track 1", T0)
    t.execute("stop", T0 + 1800)
    t.execute("done 1", T0 + 1800)
    t.execute("limit c 10 5 msg", T0)
    t.execute("use c 2", MIDNIGHT + 20 * 3600)  # inside the 5h window before the summary
    out = t.daily_summary(MIDNIGHT + 21 * 3600)
    assert "Отработано сегодня: 30м" in out
    assert "Закрыто задач: 1, открыто: 1" in out
    assert "c: 2/10" in out


def test_summary_off_and_bad_time(t):
    assert "выключена" in t.execute("summary off", T0)
    assert t.due_notifications(MIDNIGHT + 23 * 3600) == []
    assert "HH:MM" in t.execute("summary 25:99", T0)


def test_setting_a_time_already_passed_waits_until_tomorrow(t):
    t.execute("summary 08:00", T0)  # T0 is 10:00
    assert t.due_notifications(T0 + 60) == []
    assert len(t.due_notifications(MIDNIGHT + 86400 + 8 * 3600)) == 1


def test_long_timer_reminder_once_per_session(t):
    t.execute("summary off", T0)
    t.execute("track", T0)
    assert t.due_notifications(T0 + 2 * 3600) == []
    msgs = t.due_notifications(T0 + 3 * 3600)
    assert len(msgs) == 1 and "3ч 00м" in msgs[0]
    assert t.due_notifications(T0 + 4 * 3600) == []
    t.execute("stop", T0 + 5 * 3600)
    t.execute("track", T0 + 6 * 3600)  # a new session reminds again
    assert len(t.due_notifications(T0 + 9 * 3600)) == 1


def test_long_timer_setting(t):
    t.execute("summary off", T0)
    t.execute("longtimer 1", T0)
    t.execute("track", T0)
    assert len(t.due_notifications(T0 + 3600)) == 1
    t.execute("stop", T0 + 3700)
    t.execute("longtimer off", T0)
    t.execute("track", T0 + 4000)
    assert t.due_notifications(T0 + 4000 + 10 * 3600) == []


def test_limit_recovered_notification(t):
    t.execute("summary off", T0)
    t.execute("limit c 10 5 msg", T0)
    t.execute("use c 9", T0)
    assert t.due_notifications(T0 + 60) == []  # high usage is only recorded, it already showed in the reply
    msgs = t.due_notifications(T0 + 5 * 3600 + 1)
    assert len(msgs) == 1 and msgs[0].startswith("Лимит снова свободен")
    assert t.due_notifications(T0 + 5 * 3600 + 120) == []


def test_settings_output(t):
    t.execute("summary 20:30", T0)
    t.execute("longtimer off", T0)
    out = t.execute("settings", T0)
    assert "20:30" in out and "Долгий таймер: выкл" in out and "UTC" in out
