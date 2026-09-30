import time

import discord

from commands import Tracker

PREFIX = "!"


async def run(token: str, owner_id: int, tracker: Tracker) -> None:
    intents = discord.Intents.default()
    intents.message_content = True  # privileged: enable in the Developer Portal
    client = discord.Client(intents=intents)

    @client.event
    async def on_message(msg: discord.Message) -> None:
        if msg.author.id != owner_id or not msg.content.startswith(PREFIX):
            return
        reply = tracker.execute(msg.content[len(PREFIX):], int(time.time()))
        await msg.channel.send(f"```\n{reply[:1900]}\n```")

    await client.start(token)
