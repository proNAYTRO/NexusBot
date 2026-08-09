import discord
from discord.ext import commands
from discord import app_commands, ui
import aiohttp
import asyncio
import json
import os
from urllib.parse import quote
from typing import Optional

from utils.emojis import emoji

COC_API_BASE = "https://api.clashofclans.com/v1"
DATA_FILE    = "data/linked_accounts.json"
MAX_LINKED   = 25
NEXUS_RED    = 0xE8173A

# --- Custom server emojis ------------------------------------------------------
EMOJI_BK       = emoji("BK")
EMOJI_AQ       = emoji("AQ")
EMOJI_GW       = emoji("GW")
EMOJI_RC       = emoji("RC")
EMOJI_DD       = emoji("DD")
EMOJI_WAR_OPT  = emoji("nex_clash")  # no dedicated war_opt art yet -- nex_clash stands in
EMOJI_MP       = emoji("MP")
EMOJI_NN       = emoji("nn_nexus")

# --- Data helpers ---------------------------------------------------------------

def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)


def save_data(data: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


# --- CoC API helpers -------------------------------------------------------------

def normalize_tag(tag: str) -> str:
    tag = tag.strip().upper()
    if not tag.startswith("#"):
        tag = "#" + tag
    return tag


async def fetch_player(
    session: aiohttp.ClientSession, tag: str, api_key: str
) -> Optional[dict]:
    encoded = quote(tag, safe="")
    url     = f"{COC_API_BASE}/players/{encoded}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status == 200:
                return await resp.json()
            return None
    except Exception:
        return None


async def fetch_all_players(tags: list, api_key: str) -> list:
    """Fetch all linked accounts concurrently."""
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *[fetch_player(session, t, api_key) for t in tags],
            return_exceptions=True,
        )
    return [r for r in results if isinstance(r, dict)]


# --- Layout data --------------------------------------------------------------------

ROLE_MAP = {
    "member":   "Member",
    "admin":    "Elder",
    "coLeader": "Co-Leader",
    "leader":   "Leader",
}

TH_EMOJIS = {
    1:  "🏚️",  2: "🏚️",  3: "🏠",  4: "🏠",  5: "🏡",
    6:  "🏡",  7: "🏘️",  8: "🏘️",  9: emoji("TH9"), 10: emoji("TH10"),
    11: emoji("TH11"), 12: emoji("TH12"), 13: emoji("TH13"), 14: emoji("TH14"), 15: emoji("TH15"),
    16: emoji("TH16"), 17: emoji("TH17"), 18: emoji("TH18"),
}


def clan_deeplink(clan_tag: str) -> str:
    return f"https://link.clashofclans.com/en?action=OpenClanProfile&tag={clan_tag.lstrip('#')}"


def _account_select_options(players: list) -> list[discord.SelectOption]:
    options = []
    for p in players[:MAX_LINKED]:
        th   = p.get("townHallLevel", 1)
        name = p.get("name", p["tag"])
        t    = p["tag"]
        options.append(
            discord.SelectOption(
                label=f"{name}  |  TH{th}  |  {t}",
                value=t,
                emoji=TH_EMOJIS.get(th, "🏰"),
            )
        )
    return options


# --- Views -------------------------------------------------------------------------

class PlayerProfileLayout(discord.ui.LayoutView):
    """Player profile card. Doubles as the account switcher when the caller
    has more than one linked account -- the select menu re-renders this same
    container in place rather than sending a new message."""

    def __init__(self, players: list[dict], api_key: str):
        super().__init__(timeout=120 if len(players) > 1 else None)
        self.players = players
        self.api_key = api_key

        self.container = discord.ui.Container(accent_color=NEXUS_RED)
        self.add_item(self.container)

        self.select: Optional[ui.Select] = None
        if len(players) > 1:
            self.select = ui.Select(
                placeholder="🔁  Switch account...",
                options=_account_select_options(players),
                min_values=1,
                max_values=1,
            )
            self.select.callback = self.on_select

        self.render(players[0])

    def render(self, player: dict) -> None:
        name     = player.get("name", "Unknown")
        tag      = player.get("tag", "")
        th       = player.get("townHallLevel", 0)
        trophies = player.get("trophies", 0)
        best     = player.get("bestTrophies", 0)
        war_raw  = player.get("warPreference", "in")
        war_pref = f"{EMOJI_WAR_OPT} Opted In" if war_raw == "in" else f"{EMOJI_WAR_OPT} Opted Out"

        clan      = player.get("clan")
        clan_role = ROLE_MAP.get(player.get("role", ""), "Member") if clan else ""
        clan_display = (
            f"[{clan['name']}]({clan_deeplink(clan['tag'])}) - {clan_role}" if clan else "No Clan"
        )

        league      = player.get("leagueTier")
        league_name = league["name"] if league else "Unranked"
        league_icon = league.get("iconUrls", {}).get("small") if league else None

        heroes = {h["name"]: h["level"] for h in player.get("heroes", [])}
        hero_parts = []
        if "Barbarian King"  in heroes: hero_parts.append(f"{EMOJI_BK} `{heroes['Barbarian King']}`")
        if "Archer Queen"    in heroes: hero_parts.append(f"{EMOJI_AQ} `{heroes['Archer Queen']}`")
        if "Grand Warden"    in heroes: hero_parts.append(f"{EMOJI_GW} `{heroes['Grand Warden']}`")
        if "Royal Champion"  in heroes: hero_parts.append(f"{EMOJI_RC} `{heroes['Royal Champion']}`")
        if "Dragon Duke"     in heroes: hero_parts.append(f"{EMOJI_DD} `{heroes['Dragon Duke']}`")
        if "Minion Prince"   in heroes: hero_parts.append(f"{EMOJI_MP} `{heroes['Minion Prince']}`")
        hero_str = "  ".join(hero_parts) if hero_parts else "None unlocked yet"

        th_emoji = TH_EMOJIS.get(th, "🏰")

        self.container.clear_items()

        header = discord.ui.TextDisplay(f"## {emoji('nn_nexus')} {name}\n-# `{tag}`  ·  {th_emoji} TH{th}  ·  {league_name}")
        if league_icon:
            self.container.add_item(discord.ui.Section(header, accessory=discord.ui.Thumbnail(league_icon)))
        else:
            self.container.add_item(header)

        self.container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        self.container.add_item(discord.ui.TextDisplay(
            f"{emoji('nex_trophy')} **{trophies:,}** Trophies  ·  Best **{best:,}**\n{war_pref}"
        ))
        self.container.add_item(discord.ui.TextDisplay(f"{emoji('nex_member')} **Clan:** {clan_display}"))
        self.container.add_item(discord.ui.TextDisplay(f"**Heroes:** {hero_str}"))

        if self.select:
            self.container.add_item(discord.ui.Separator())
            self.container.add_item(discord.ui.ActionRow(self.select))

    async def on_select(self, interaction: discord.Interaction):
        tag = self.select.values[0]
        await interaction.response.defer()
        async with aiohttp.ClientSession() as session:
            player = await fetch_player(session, tag, self.api_key)
        if player:
            self.render(player)
            await interaction.edit_original_response(view=self)
        else:
            await interaction.followup.send(
                f"{emoji('nex_error')} Couldn't fetch data for `{tag}`.", ephemeral=True
            )

    async def on_timeout(self):
        if self.select:
            self.select.disabled = True


class UnlinkLayout(discord.ui.LayoutView):
    """Dropdown to pick which CoC account to unlink."""

    def __init__(self, tags: list, user_id: str):
        super().__init__(timeout=60)
        self.user_id = user_id

        self.container = discord.ui.Container(accent_color=NEXUS_RED)
        self.add_item(self.container)

        self.select = ui.Select(
            placeholder="Choose an account to unlink...",
            options=[
                discord.SelectOption(label=tag, value=tag, emoji=emoji("nex_delete"))
                for tag in tags[:MAX_LINKED]
            ],
        )
        self.select.callback = self.on_select
        self._render_prompt()

    def _render_prompt(self) -> None:
        self.container.clear_items()
        self.container.add_item(discord.ui.TextDisplay(
            f"{emoji('nex_dropdown')} Select the account you want to unlink:"
        ))
        self.container.add_item(discord.ui.ActionRow(self.select))

    async def on_select(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                f"{emoji('nex_error')} This menu isn't for you!", ephemeral=True
            )
            return

        tag  = self.select.values[0]
        data = load_data()

        self.container.clear_items()
        if self.user_id in data and tag in data[self.user_id]:
            data[self.user_id].remove(tag)
            if not data[self.user_id]:
                del data[self.user_id]
            save_data(data)
            self.container.add_item(discord.ui.TextDisplay(
                f"{emoji('nex_success')} `{tag}` has been unlinked from your Discord account."
            ))
        else:
            self.container.add_item(discord.ui.TextDisplay(
                f"{emoji('nex_warning')} That tag wasn't found in your linked accounts."
            ))

        self.stop()
        await interaction.response.edit_message(view=self)

    async def on_timeout(self):
        self.select.disabled = True


# --- Cog ---------------------------------------------------------------------------

class CoC(commands.Cog):
    """Clash of Clans integration commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_key: str = os.getenv("COC_API_KEY", "")

    # -- /nexlink ---------------------------------------------------------------

    @app_commands.command(
        name="nexlink",
        description="Link a Clash of Clans account to your Discord profile",
    )
    @app_commands.describe(tag="Your player tag (e.g. #2PP)")
    async def nexlink(self, interaction: discord.Interaction, tag: str):
        await interaction.response.defer(ephemeral=True)
        tag = normalize_tag(tag)

        async with aiohttp.ClientSession() as session:
            player = await fetch_player(session, tag, self.api_key)

        if not player:
            await interaction.followup.send(
                f"{emoji('nex_error')} No player found for `{tag}`. Double-check your tag!",
                ephemeral=True,
            )
            return

        data = load_data()
        uid  = str(interaction.user.id)

        if uid not in data:
            data[uid] = []
        if tag in data[uid]:
            await interaction.followup.send(
                f"{emoji('nex_warning')} `{tag}` is already linked to your account.", ephemeral=True
            )
            return
        if len(data[uid]) >= MAX_LINKED:
            await interaction.followup.send(
                f"{emoji('nex_error')} You've hit the limit of **{MAX_LINKED} linked accounts**.",
                ephemeral=True,
            )
            return

        data[uid].append(tag)
        save_data(data)

        await interaction.followup.send(
            f"{emoji('nex_success')} **{player['name']}** (`{tag}`) has been linked to your Discord!\n"
            f"-# You now have {len(data[uid])} account(s) linked. Use `/nexprofile` to view.",
            ephemeral=True,
        )

    # -- /nexunlink -------------------------------------------------------------

    @app_commands.command(
        name="nexunlink",
        description="Unlink a Clash of Clans account from your Discord profile",
    )
    async def nexunlink(self, interaction: discord.Interaction):
        data = load_data()
        uid  = str(interaction.user.id)
        tags = data.get(uid, [])

        if not tags:
            await interaction.response.send_message(
                f"{emoji('nex_error')} You don't have any linked accounts. Use `/nexlink` first!",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(view=UnlinkLayout(tags, uid), ephemeral=True)

    # -- /nexprofile --------------------------------------------------------------

    @app_commands.command(
        name="nexprofile",
        description="View your linked Clash of Clans profile",
    )
    async def nexprofile(self, interaction: discord.Interaction):
        data = load_data()
        uid  = str(interaction.user.id)
        tags = data.get(uid, [])

        if not tags:
            await interaction.response.send_message(
                f"{emoji('nex_error')} No linked accounts found. Use `/nexlink <tag>` to get started!",
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        players = await fetch_all_players(tags, self.api_key)

        if not players:
            await interaction.followup.send(
                f"{emoji('nex_error')} Couldn't fetch any of your accounts. Contact Naytro for support.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(view=PlayerProfileLayout(players, self.api_key))


async def setup(bot: commands.Bot):
    await bot.add_cog(CoC(bot))
