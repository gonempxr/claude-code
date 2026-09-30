import time

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)

from commands import Tracker

# Label on the persistent bottom keyboard -> command line handed to Tracker.execute
BUTTONS = {
    "▶ Старт": "track",
    "⏹ Стоп": "stop",
    "⏱ Таймер": "timer",
    "📋 Задачи": "tasks",
    "📊 Отчёт": "report today",
    "📉 Лимиты": "limits",
}
_LAYOUT = [["▶ Старт", "⏹ Стоп", "⏱ Таймер"], ["📋 Задачи", "📊 Отчёт", "📉 Лимиты"]]
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text=label) for label in row] for row in _LAYOUT],
    resize_keyboard=True,
    is_persistent=True,
)

CALLBACK_ACTIONS = {"track", "done", "use"}
MAX_CALLBACK_BYTES = 64  # Telegram limit for callback_data


def _button(text: str, data: str) -> InlineKeyboardButton | None:
    if len(data.encode()) > MAX_CALLBACK_BYTES:
        return None
    return InlineKeyboardButton(text=text, callback_data=data)


def inline_markup(tracker: Tracker, cmd: str) -> InlineKeyboardMarkup | None:
    """Buttons attached under the tasks / limits lists; None for every other reply."""
    rows = []
    if cmd == "tasks":
        for task_id, title in tracker.open_tasks():
            row = [
                _button(f"▶ #{task_id} {title[:22]}", f"track:{task_id}"),
                _button(f"✓ #{task_id}", f"done:{task_id}"),
            ]
            rows.append([b for b in row if b])
    elif cmd == "limits":
        for name in tracker.limit_names():
            btn = _button(f"+1 {name}", f"use:{name}")
            if btn:
                rows.append([btn])
    rows = [r for r in rows if r]
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def parse_callback(data: str) -> str | None:
    action, _, arg = data.partition(":")
    if action in CALLBACK_ACTIONS and arg:
        return f"{action} {arg}"
    return None


def build_dispatcher(owner_id: int, tracker: Tracker) -> Dispatcher:
    dp = Dispatcher()

    @dp.message()
    async def on_message(msg: Message) -> None:
        # Silently ignore everyone except the owner.
        if not msg.from_user or msg.from_user.id != owner_id or not msg.text:
            return
        text = msg.text.strip()
        if text in BUTTONS:
            line = BUTTONS[text]
        elif text.startswith("/"):
            head, _, rest = text[1:].partition(" ")
            cmd = head.split("@", 1)[0].lower()  # "/tasks@my_bot" in groups
            if cmd == "start":  # Telegram sends /start on first open; our timer command is "track"
                cmd = "help"
            line = f"{cmd} {rest}"
        else:
            return
        reply = tracker.execute(line, int(time.time()))
        markup = inline_markup(tracker, line.split()[0].lower())
        await msg.answer(reply, reply_markup=markup or MAIN_KEYBOARD)

    @dp.callback_query()
    async def on_callback(cb: CallbackQuery) -> None:
        line = parse_callback(cb.data or "")
        if cb.from_user.id != owner_id or not line:
            await cb.answer()
            return
        reply = tracker.execute(line, int(time.time()))
        await cb.answer(reply[:200])  # shown as a toast
        # Redraw the list the button was under so it reflects the change.
        refresh = "limits" if line.startswith("use ") else "tasks"
        if isinstance(cb.message, Message):
            try:
                await cb.message.edit_text(
                    tracker.execute(refresh, int(time.time())),
                    reply_markup=inline_markup(tracker, refresh),
                )
            except TelegramBadRequest:
                pass  # "message is not modified" when nothing changed

    return dp


async def run(token: str, owner_id: int, tracker: Tracker) -> None:
    await build_dispatcher(owner_id, tracker).start_polling(Bot(token))
