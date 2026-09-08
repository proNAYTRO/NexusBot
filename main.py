import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import asyncio
from datetime import datetime

load_dotenv()

# ── ANSI colours ─────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
CYAN   = "\033[96m"
BLUE   = "\033[94m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
WHITE  = "\033[97m"

BANNER = f"""{CYAN}{BOLD}
  ███╗   ██╗███████╗██╗  ██╗██╗   ██╗███████╗
  ████╗  ██║██╔════╝╚██╗██╔╝██║   ██║██╔════╝
  ██╔██╗ ██║█████╗   ╚███╔╝ ██║   ██║███████╗
  ██║╚██╗██║██╔══╝   ██╔██╗ ██║   ██║╚════██║
  ██║ ╚████║███████╗██╔╝ ██╗╚██████╔╝███████║
  ╚═╝  ╚═══╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚══════╝
{RESET}{DIM}  NAYTRO-NEXUS · Discord Bot{RESET}
"""

def log(symbol: str, color: str, label: str, msg: str):
    print(f"  {color}{BOLD}{symbol}{RESET} {DIM}{label:<10}{RESET} {WHITE}{msg}{RESET}")

def divider():
    print(f"  {DIM}{'─' * 48}{RESET}")

# ─────────────────────────────────────────────────────────────────────────────

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    divider()
    log("✓", GREEN,  "BOT",     f"{bot.user} (ID: {bot.user.id})")
    log("✓", GREEN,  "GUILDS",  str(len(bot.guilds)))
    log("◉", CYAN,   "TIME",    datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"))

    try:
        existing = await bot.http.get_global_commands(bot.application_id)
        entry_point = next((c for c in existing if c.get("type") == 4), None)

        all_commands = [c.to_dict(bot.tree) for c in bot.tree.get_commands()]
        if entry_point:
            all_commands.append(entry_point)

        synced = await bot.http.bulk_upsert_global_commands(bot.application_id, all_commands)
        log("✓", GREEN, "COMMANDS", f"{len(synced)} slash command(s) synced")
    except Exception as e:
        log("✗", RED, "SYNC", str(e))

    presence_text = "/nexhelp..."
    await bot.change_presence(
        activity=discord.CustomActivity(name=presence_text)
    )
    log("✓", GREEN, "PRESENCE", presence_text)

    divider()
    print()


@bot.event
async def on_command_error(ctx, error):
    log("✗", RED, "ERROR", f"'{ctx.command}': {error}")


async def load_cogs():
    loaded, failed = 0, 0
    for root, dirs, files in os.walk("cogs"):
        for filename in files:
            if filename.endswith(".py") and not filename.startswith("_"):
                path = os.path.join(root, filename)
                module = path.replace(os.sep, ".").removesuffix(".py")
                try:
                    await bot.load_extension(module)
                    log("✓", GREEN, "COG", module)
                    loaded += 1
                except Exception as e:
                    log("✗", RED, "COG FAIL", f"{module} — {e}")
                    failed += 1
    divider()
    status = f"{GREEN}{loaded} loaded{RESET}"
    if failed:
        status += f"  {RED}{failed} failed{RESET}"
    print(f"  {DIM}Cogs:{RESET}  {status}")


async def main():
    print(BANNER)
    divider()
    async with bot:
        await load_cogs()
        token = os.getenv("BOT_TOKEN")
        if not token:
            raise ValueError("BOT_TOKEN not found in .env file!")
        await bot.start(token)


asyncio.run(main())