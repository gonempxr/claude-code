import time

from aiogram import Bot, Dispatcher
from aiogram.types import Message

from commands import Tracker


async def run(token: str, owner_id: int, tracker: Tracker) -> None:
    bot = Bot(token)
    dp = Dispatcher()

    @dp.message()
    async def on_message(msg: Message) -> None:
        # Silently ignore everyone except the owner.
        if not msg.from_user or msg.from_user.id != owner_id or not msg.text:
            return
        text = msg.text.strip()
        if not text.startswith("/"):
            return
        head, _, rest = text[1:].partition(" ")
        cmd = head.split("@", 1)[0].lower()  # "/tasks@my_bot" in groups
        if cmd == "start":  # Telegram sends /start on first open; our timer command is "track"
            cmd = "help"
        await msg.answer(tracker.execute(f"{cmd} {rest}", int(time.time())))

    await dp.start_polling(bot)
