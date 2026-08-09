"""One-off replacer for specific Discord application emojis.

Usage:
    python -m utils.replace_emojis <folder> <name1> [name2 ...]

Deletes each named application emoji (if it exists) then re-uploads it from
<folder>/<name>.<ext> (png/jpg/gif), so the name is preserved but a new ID is
assigned. Requires BOT_TOKEN in .env.
"""
import asyncio
import sys
from pathlib import Path

import discord
from dotenv import load_dotenv
import os

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

VALID_EXTS = [".png", ".jpg", ".jpeg", ".gif"]


def _find_file(folder: Path, name: str) -> Path | None:
    for ext in VALID_EXTS:
        candidate = folder / f"{name}{ext}"
        if candidate.exists():
            return candidate
    return None


async def replace_all(folder: Path, names: list[str]):
    intents = discord.Intents.default()
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            existing = {e.name: e for e in await client.fetch_application_emojis()}

            for name in names:
                path = _find_file(folder, name)
                if path is None:
                    print(f"  SKIP  {name}: no matching file in {folder}")
                    continue

                if name in existing:
                    await existing[name].delete()
                    print(f"  deleted old {name}")

                emoji = await client.create_application_emoji(name=name, image=path.read_bytes())
                print(f"  added {emoji.name}  ->  {emoji}  (id={emoji.id})")
                await asyncio.sleep(1)
        finally:
            await client.close()

    await client.start(BOT_TOKEN)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python -m utils.replace_emojis <folder> <name1> [name2 ...]")
        sys.exit(1)
    target = Path(sys.argv[1])
    if not target.is_dir():
        print(f"Not a folder: {target}")
        sys.exit(1)
    asyncio.run(replace_all(target, sys.argv[2:]))
