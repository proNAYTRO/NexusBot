"""
NexusBot - /nexcountdowns
Voice channel war timers (like ClashKing), one channel per selected clan.

Drop this in:  cogs/clash/countdowns.py   (main.py auto-loads it)
Uses:          data/clan_tags.json  (your saved clans from /nexclan)
Needs:         bot role with Manage Channels + Manage Roles
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

log = logging.getLogger("nexusbot.countdowns")

NEXUS_RED = 0xE8173A
ADMIN_ID = 1048729773926522981

API = "https://api.clashofclans.com/v1"
DATA_FILE = Path("data/countdowns.json")
CLAN_TAGS_FILE = Path("data/clan_tags.json")
CATEGORY_NAME = "⏳ Clan Countdowns"
SELECT_ID = "nex:countdowns:select"


# ---------------------------------------------------------------- helpers
def norm(tag: str) -> str:
    return "#" + tag.strip().upper().lstrip("#")


def fmt(seconds: float) -> str:
    seconds = max(int(seconds), 0)
    d, r = divmod(seconds, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


def parse_coc_time(s: str) -> datetime:
    # CoC format: 20260627T043543.000Z
    return datetime.strptime(s, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)


def load_data() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except json.JSONDecodeError:
            log.warning("countdowns.json was corrupt, starting fresh")
    return {}


def save_data(data: dict) -> None:
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, indent=2))


def parse_saved(raw, guild_id: int) -> dict[str, str | None]:
    """Read data/clan_tags.json whatever shape it is.
    Returns {tag: name_or_None}. If this misreads your file, tell me the
    shape and I'll tighten it."""
    out: dict[str, str | None] = {}
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                out[norm(item)] = None
            elif isinstance(item, dict) and item.get("tag"):
                out[norm(item["tag"])] = item.get("name")
    elif isinstance(raw, dict):
        if any(str(k).startswith("#") for k in raw):
            for tag, v in raw.items():
                if not str(tag).startswith("#"):
                    continue
                if isinstance(v, str):
                    out[norm(tag)] = v
                elif isinstance(v, dict):
                    out[norm(tag)] = v.get("name")
                else:
                    out[norm(tag)] = None
        elif str(guild_id) in raw:
            return parse_saved(raw[str(guild_id)], guild_id)
        else:
            for k, v in raw.items():
                if not str(k).isdigit() and isinstance(v, (list, dict)):
                    out.update(parse_saved(v, guild_id))
    return out


# ---------------------------------------------------------------- UI
class ClanSelect(discord.ui.Select):
    def __init__(self, cog: "Countdowns", options: list[discord.SelectOption]):
        super().__init__(
            custom_id=SELECT_ID,
            placeholder="Select clans for war countdowns...",
            min_values=0,
            max_values=len(options),
            options=options,
        )
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        if not self.cog.allowed(interaction.user):
            return await interaction.response.send_message(
                "You need **Manage Server** to change this.", ephemeral=True
            )

        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        names = await self.cog.saved_clans(guild.id)
        selected = {v for v in self.values if v in names}

        try:
            await self.cog.apply_selection(guild, selected, names)
        except discord.Forbidden:
            return await interaction.followup.send(
                "I'm missing permissions. Give me **Manage Channels** and "
                "**Manage Roles**.",
                ephemeral=True,
            )

        embed, view = await self.cog.build_panel(guild)
        await interaction.message.edit(embed=embed, view=view)
        await interaction.followup.send("Countdowns updated ✅", ephemeral=True)


class ClanView(discord.ui.View):
    def __init__(self, cog: "Countdowns", options: list[discord.SelectOption]):
        super().__init__(timeout=None)  # persistent: survives restarts
        self.add_item(ClanSelect(cog, options))


# ---------------------------------------------------------------- cog
class Countdowns(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data: dict = load_data()
        # data[guild_id] = {"category_id": int|None,
        #                   "clans": {tag: {"channel_id": int, "name": str}}}
        self.session: aiohttp.ClientSession | None = None
        self.name_cache: dict[str, str] = {}
        self.sem = asyncio.Semaphore(5)

    async def cog_load(self):
        self.session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {os.getenv('COC_API_KEY')}"},
            timeout=aiohttp.ClientTimeout(total=15),
        )
        # Re-register the dropdown so old panels keep working after restarts
        self.bot.add_view(
            ClanView(self, [discord.SelectOption(label="loading", value="-")])
        )
        self.update_loop.start()

    async def cog_unload(self):
        self.update_loop.cancel()
        if self.session:
            await self.session.close()

    def allowed(self, user) -> bool:
        return user.id == ADMIN_ID or user.guild_permissions.manage_guild

    # ---- CoC API
    async def api_get(self, path: str):
        try:
            async with self.sem:
                async with self.session.get(f"{API}{path}") as r:
                    data = await r.json() if r.status == 200 else None
                    return r.status, data
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return 0, None

    async def clan_name(self, tag: str) -> str:
        if tag not in self.name_cache:
            status, data = await self.api_get(f"/clans/{quote(tag)}")
            self.name_cache[tag] = data["name"] if data else tag
        return self.name_cache[tag]

    async def saved_clans(self, guild_id: int) -> dict[str, str]:
        """{tag: name} from data/clan_tags.json (your /nexclan saved clans)."""
        if not CLAN_TAGS_FILE.exists():
            return {}
        try:
            raw = json.loads(CLAN_TAGS_FILE.read_text())
        except json.JSONDecodeError:
            return {}
        saved = parse_saved(raw, guild_id)
        for tag, name in saved.items():
            if name:
                self.name_cache.setdefault(tag, name)
        missing = [t for t, n in saved.items() if not n]
        if missing:
            await asyncio.gather(*(self.clan_name(t) for t in missing))
        return {t: self.name_cache.get(t, t) for t in saved}

    async def war_text(self, tag: str):
        """Timer text, or None to skip this update (API hiccup)."""
        status, data = await self.api_get(f"/clans/{quote(tag)}/currentwar")
        if status == 403:
            return "War log private"
        if status != 200 or data is None:
            return None

        now = datetime.now(timezone.utc)
        state = data.get("state")
        if state == "preparation":
            return f"Prep {fmt((parse_coc_time(data['startTime']) - now).total_seconds())}"
        if state == "inWar":
            return f"War {fmt((parse_coc_time(data['endTime']) - now).total_seconds())}"
        if state == "warEnded":
            return "War ended"
        return "Not in war"

    # ---- panel
    async def build_panel(self, guild: discord.Guild):
        cfg = self.data.get(str(guild.id), {"clans": {}})
        active = set(cfg["clans"])
        clans = list((await self.saved_clans(guild.id)).items())[:25]  # Discord max

        embed = discord.Embed(
            title="⏳ Nex Countdowns",
            description=(
                "Pick clans to get a **war timer** voice channel.\n"
                "Updates every 10 minutes.\n\n"
                "**Editing:** rename, move, or change permissions on the "
                "channels any time. Keep a `|` in the name and everything "
                "before it stays."
            ),
            colour=NEXUS_RED,
        )
        if active:
            embed.add_field(
                name="Active",
                value="\n".join(f"• {c['name']}" for c in cfg["clans"].values()),
            )

        if not clans:
            embed.add_field(
                name="No saved clans", value="Save a clan with `/nexclan` first."
            )
            return embed, None

        options = [
            discord.SelectOption(
                label=name[:100], value=tag, description=tag, default=tag in active
            )
            for tag, name in clans
        ]
        return embed, ClanView(self, options)

    # ---- channels
    async def get_category(self, guild: discord.Guild, cfg: dict):
        category = guild.get_channel(cfg.get("category_id") or 0)
        if category is None:
            category = await guild.create_category(
                CATEGORY_NAME,
                overwrites={
                    guild.default_role: discord.PermissionOverwrite(
                        view_channel=True, connect=False
                    )
                },
            )
            cfg["category_id"] = category.id
        return category

    async def create_channel(self, guild, cfg, clan_name: str, text: str):
        category = await self.get_category(guild, cfg)
        return await guild.create_voice_channel(
            f"{clan_name} | {text}"[:100],
            category=category,
            overwrites={
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=True, connect=False
                )
            },
            reason="Nex countdowns",
        )

    async def apply_selection(self, guild, selected: set, names: dict):
        cfg = self.data.setdefault(str(guild.id), {"category_id": None, "clans": {}})

        # deselected clans -> delete their channel
        for tag in list(cfg["clans"]):
            if tag not in selected:
                ch = guild.get_channel(cfg["clans"][tag]["channel_id"])
                if ch:
                    await ch.delete(reason="Nex countdowns: clan deselected")
                del cfg["clans"][tag]

        # new clans -> create channel
        for tag in selected:
            if tag not in cfg["clans"]:
                ch = await self.create_channel(guild, cfg, names[tag], "Loading...")
                cfg["clans"][tag] = {"channel_id": ch.id, "name": names[tag]}

        save_data(self.data)
        await self.update_guild(guild)  # fill timers right away

    async def update_channel(self, guild, cfg, tag, info, text):
        channel = guild.get_channel(info["channel_id"])

        # self-heal: channel was deleted but the clan is still selected
        if channel is None:
            channel = await self.create_channel(guild, cfg, info["name"], text)
            info["channel_id"] = channel.id
            save_data(self.data)

        # Everything before the pipe is kept, so users can customise it
        if "|" in channel.name:
            prefix = channel.name.split("|", 1)[0].strip()
        else:
            prefix = info["name"]

        new_name = f"{prefix} | {text}"[:100]
        if new_name != channel.name:  # skip no-op edits (rename rate limits)
            await channel.edit(name=new_name, reason="Nex countdown update")

    async def update_guild(self, guild, results: dict | None = None):
        cfg = self.data.get(str(guild.id))
        if not cfg:
            return
        if results is None:
            tags = list(cfg["clans"])
            texts = await asyncio.gather(*(self.war_text(t) for t in tags))
            results = dict(zip(tags, texts))

        for tag, info in list(cfg["clans"].items()):
            text = results.get(tag)
            if text is None:
                continue
            try:
                await self.update_channel(guild, cfg, tag, info, text)
            except (discord.Forbidden, discord.HTTPException) as e:
                log.warning("Countdown update failed for %s: %s", tag, e)
            await asyncio.sleep(0.5)

    # ---- polling
    @tasks.loop(minutes=10)
    async def update_loop(self):
        all_tags = list({t for g in self.data.values() for t in g["clans"]})
        texts = await asyncio.gather(*(self.war_text(t) for t in all_tags))
        results = dict(zip(all_tags, texts))  # one API call per clan, shared

        for gid in list(self.data):
            guild = self.bot.get_guild(int(gid))
            if guild:
                await self.update_guild(guild, results)

    @update_loop.before_loop
    async def before_loop(self):
        await self.bot.wait_until_ready()

    # ---- command
    @app_commands.command(
        name="nexcountdowns",
        description="Set up voice channel war timers for your clans",
    )
    @app_commands.guild_only()
    async def nexcountdowns(self, interaction: discord.Interaction):
        if not self.allowed(interaction.user):
            return await interaction.response.send_message(
                "You need **Manage Server** to use this.", ephemeral=True
            )
        embed, view = await self.build_panel(interaction.guild)
        await interaction.response.send_message(embed=embed, view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Countdowns(bot))
