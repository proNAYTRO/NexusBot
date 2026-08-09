"""One-off bulk uploader for Discord application emojis.

Usage:
    python -m utils.upload_emojis <folder>

Uploads every .png/.jpg/.gif in <folder> as an application emoji (named after
the filename stem), so it's usable anywhere the bot is, not just one server.
Skips names that already exist on the application. Requires BOT_TOKEN in .env.
"""
import asyncio
import sys
from pathlib import Path

import discord
from dotenv import load_dotenv
import os

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

VALID_EXTS = {".png", ".jpg", ".jpeg", ".gif"}


async def upload_all(folder: Path):
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            existing = {e.name for e in await client.fetch_application_emojis()}
            files = sorted(p for p in folder.iterdir() if p.suffix.lower() in VALID_EXTS)
            print(f"Found {len(files)} image(s) in {folder}, {len(existing)} emoji(s) already uploaded.")

            for path in files:
                name = path.stem
                if name in existing:
                    print(f"  skip  {name} (already exists)")
                    continue
                try:
                    emoji = await client.create_application_emoji(name=name, image=path.read_bytes())
                    print(f"  added {emoji.name}  ->  {emoji}")
                except discord.HTTPException as e:
                    print(f"  FAILED {name}: {e}")
                await asyncio.sleep(1)  # be gentle on rate limits
        finally:
            await client.close()

    await client.start(BOT_TOKEN)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python -m utils.upload_emojis <folder>")
        sys.exit(1)
    target = Path(sys.argv[1])
    if not target.is_dir():
        print(f"Not a folder: {target}")
        sys.exit(1)
    asyncio.run(upload_all(target))
