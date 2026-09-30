import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

import db
from commands import Tracker
from telegram_adapter import build_dispatcher, inline_markup, parse_callback, send_due

OWNER, STRANGER = 111, 222


class StubSession(BaseSession):
    """Records outgoing Bot API calls instead of hitting the network."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        pass

    async def stream_content(self, *a, **k):
        yield b""

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message(message_id=99, date=datetime.now(), chat=Chat(id=OWNER, type="private"))
        return True


def _msg(user_id, text):
    return Message(
        message_id=1, date=datetime.now(), chat=Chat(id=user_id, type="private"),
        from_user=User(id=user_id, is_bot=False, first_name="x"), text=text,
    )


def _send(tracker, update_builder):
    session = StubSession()
    bot = Bot("123456:TESTTOKEN", session=session)
    dp = build_dispatcher(OWNER, tracker)
    asyncio.run(dp.feed_update(bot, update_builder()))
    return session.calls


def _tracker():
    return Tracker(db.connect(":memory:"), "UTC")


def test_button_label_runs_command_and_keeps_main_keyboard():
    t = _tracker()
    calls = _send(t, lambda: Update(update_id=1, message=_msg(OWNER, "📋 Задачи")))
    assert calls[0].text == "Открытых задач нет."
    assert calls[0].reply_markup.keyboard[0][0].text == "▶ Старт"


def test_tasks_reply_gets_inline_buttons():
    t = _tracker()
    t.execute("task write report", 0)
    calls = _send(t, lambda: Update(update_id=1, message=_msg(OWNER, "/tasks")))
    row = calls[0].reply_markup.inline_keyboard[0]
    assert [b.callback_data for b in row] == ["track:1", "done:1"]


def test_stranger_is_ignored():
    t = _tracker()
    assert _send(t, lambda: Update(update_id=1, message=_msg(STRANGER, "/task hack"))) == []
    assert t.open_tasks() == []


def test_start_shows_help_not_timer():
    t = _tracker()
    calls = _send(t, lambda: Update(update_id=1, message=_msg(OWNER, "/start")))
    assert calls[0].text.startswith("Задачи")
    assert t.timer(0) == "Таймер не запущен."


def test_callback_done_completes_task_and_redraws_list():
    t = _tracker()
    t.execute("task a", 0)
    cb = lambda: Update(update_id=1, callback_query=CallbackQuery(
        id="1", from_user=User(id=OWNER, is_bot=False, first_name="x"),
        chat_instance="c", data="done:1", message=_msg(OWNER, "list"),
    ))
    calls = _send(t, cb)
    assert t.open_tasks() == []
    assert isinstance(calls[0], AnswerCallbackQuery) and "Готово #1" in calls[0].text
    assert isinstance(calls[1], EditMessageText) and calls[1].text == "Открытых задач нет."


def test_callback_from_stranger_changes_nothing():
    t = _tracker()
    t.execute("task a", 0)
    cb = lambda: Update(update_id=1, callback_query=CallbackQuery(
        id="1", from_user=User(id=STRANGER, is_bot=False, first_name="x"),
        chat_instance="c", data="done:1", message=_msg(STRANGER, "list"),
    ))
    _send(t, cb)
    assert t.open_tasks() == [(1, "a")]


def test_parse_callback_rejects_garbage():
    assert parse_callback("done:3") == "done 3"
    assert parse_callback("use:claude-5h") == "use claude-5h"
    assert parse_callback("drop:everything") is None
    assert parse_callback("done:") is None


def test_overlong_limit_name_is_skipped_not_crashing():
    t = _tracker()
    t.execute("limit " + "я" * 40 + " 10 5", 0)  # 40 cyrillic chars = 80 bytes of callback data
    assert inline_markup(t, "limits") is None


def test_send_due_pushes_notifications_to_owner():
    t = _tracker()
    t.execute("summary 00:00", 0)
    session = StubSession()
    bot = Bot("123456:TESTTOKEN", session=session)
    asyncio.run(send_due(bot, OWNER, t, 1_750_000_000))
    assert len(session.calls) == 1
    assert session.calls[0].chat_id == OWNER and session.calls[0].text.startswith("Итог дня")
    asyncio.run(send_due(bot, OWNER, t, 1_750_000_060))
    assert len(session.calls) == 1  # nothing new, nothing resent
