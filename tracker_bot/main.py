import asyncio
import logging
from pathlib import Path

import config
import db
import discord_adapter
import telegram_adapter
from commands import Tracker


async def main() -> None:
    cfg = config.load()
    tracker = Tracker(db.connect(cfg.db_path), cfg.tz, Path(cfg.claude_home))
    jobs = []
    if cfg.telegram_token:
        jobs.append(telegram_adapter.run(cfg.telegram_token, cfg.telegram_user_id, tracker))
    if cfg.discord_token:
        jobs.append(discord_adapter.run(cfg.discord_token, cfg.discord_user_id, tracker))
    await asyncio.gather(*jobs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
