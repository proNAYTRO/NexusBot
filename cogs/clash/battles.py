"""/nexbattles — paginated browser for a player's recent battle log.

Uses the undocumented GET /players/{tag}/battlelog endpoint: the 50 most
recent battles, attacks AND defenses, ranked and casual. Each entry carries
an armyShareCode (on defenses it's the *attacker's* army), which becomes a
CopyArmy deep-link button — same mechanism as /nexbasepost's Copy Layout.
No timestamps and no opponent names in the payload, so battles are shown
newest-first by list order with opponent tags only.
"""

import aiohttp
import discord
import os
from discord.ext import commands
from discord import app_commands, ui
from typing import Optional
from urllib.parse import quote

from utils.emojis import emoji
from cogs.clash.player import (
    COC_API_BASE,
    _account_select_options,
    fetch_all_players,
    fetch_player,
    load_data,
    normalize_tag,
)

NEXUS_RED = 0xE8173A
PER_PAGE  = 5

BATTLE_TYPE_LABELS = {
    "ranked":      "RANKED",
    "homeVillage": "CASUAL",
}


# --- CoC API helpers -------------------------------------------------------------

async def fetch_battlelog(
    session: aiohttp.ClientSession, tag: str, api_key: str
) -> Optional[list]:
    url = f"{COC_API_BASE}/players/{quote(tag, safe='')}/battlelog"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status == 200:
                return (await resp.json()).get("items", [])
            return None
    except Exception:
        return None


async def fetch_battlelog_for(tag: str, api_key: str) -> Optional[list]:
    async with aiohttp.ClientSession() as session:
        return await fetch_battlelog(session, tag, api_key)


# --- Formatting helpers ----------------------------------------------------------

def _battle_text(entry: dict) -> str:
    label   = BATTLE_TYPE_LABELS.get(entry.get("battleType", ""), entry.get("battleType", "?").upper())
    stars   = max(0, min(3, entry.get("stars", 0)))
    stars_s = "★" * stars + "☆" * (3 - stars)
    pct     = entry.get("destructionPercentage", 0)
    opp     = entry.get("opponentPlayerTag", "?")

    if entry.get("attack"):
        return f"{emoji('nex_clash')} **{label}**  {stars_s} {pct}%  —  vs `{opp}`"
    return f"{emoji('nex_home')} **{label} (defense)**  {stars_s} {pct}%  —  attacker `{opp}`"


def army_link(code: str) -> str:
    return f"https://link.clashofclans.com/en?action=CopyArmy&army={quote(code, safe='')}"


# --- Views -------------------------------------------------------------------------

class BattleLogLayout(discord.ui.LayoutView):
    """Paginated battle-log card. Doubles as the account switcher when the
    caller has more than one linked account — same in-place re-render pattern
    as LeagueStandingLayout."""

    def __init__(self, players: list[dict], logs: dict[str, Optional[list]],
                 api_key: str, battle_type: str = "all", side: str = "all"):
        super().__init__(timeout=180)
        self.players     = {p["tag"]: p for p in players}
        self.logs        = logs  # tag -> battlelog items, cached across switches
        self.api_key     = api_key
        self.battle_type = battle_type
        self.side        = side
        self.current_tag = players[0]["tag"]
        self.page        = 0

        self.container = discord.ui.Container(accent_color=NEXUS_RED)
        self.add_item(self.container)

        self.prev_btn = ui.Button(emoji=emoji("nex_prev"), style=discord.ButtonStyle.secondary)
        self.next_btn = ui.Button(emoji=emoji("nex_next"), style=discord.ButtonStyle.secondary)
        self.prev_btn.callback = self.on_prev
        self.next_btn.callback = self.on_next

        self.select: Optional[ui.Select] = None
        if len(players) > 1:
            self.select = ui.Select(
                placeholder="🔁  Switch account...",
                options=_account_select_options(players),
                min_values=1,
                max_values=1,
            )
            self.select.callback = self.on_select

        self.render()

    # -- filtering ----------------------------------------------------------------

    def _filtered(self) -> Optional[list]:
        entries = self.logs.get(self.current_tag)
        if entries is None:
            return None
        if self.battle_type == "ranked":
            entries = [e for e in entries if e.get("battleType") == "ranked"]
        elif self.battle_type == "casual":
            entries = [e for e in entries if e.get("battleType") != "ranked"]
        if self.side == "attacks":
            entries = [e for e in entries if e.get("attack")]
        elif self.side == "defenses":
            entries = [e for e in entries if not e.get("attack")]
        return entries

    def _filter_labels(self) -> str:
        parts = []
        if self.battle_type != "all":
            parts.append(self.battle_type.capitalize())
        if self.side != "all":
            parts.append(self.side.capitalize())
        return "".join(f"  ·  {p}" for p in parts)

    # -- rendering ----------------------------------------------------------------

    def render(self) -> None:
        player = self.players[self.current_tag]
        name   = player.get("name", "Unknown")
        tag    = player.get("tag", "")

        league      = player.get("leagueTier") or {}
        league_name = league.get("name", "Unranked")
        league_icon = (league.get("iconUrls") or {}).get("small")

        self.container.clear_items()

        header = discord.ui.TextDisplay(
            f"## {emoji('nn_nexus')} Battle Log — {name}\n-# `{tag}`  ·  {league_name}"
        )
        if league_icon:
            self.container.add_item(discord.ui.Section(header, accessory=discord.ui.Thumbnail(league_icon)))
        else:
            self.container.add_item(header)
        self.container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        entries = self._filtered()
        if entries is None:
            self.container.add_item(discord.ui.TextDisplay(
                f"{emoji('nex_error')} Couldn't fetch this account's battle log right now."
            ))
            self._render_controls(pages=0)
            return
        if not entries:
            self.container.add_item(discord.ui.TextDisplay(
                f"{emoji('nex_warning')} No recent battles match this filter."
            ))
            self._render_controls(pages=0)
            return

        pages = (len(entries) + PER_PAGE - 1) // PER_PAGE
        self.page = max(0, min(self.page, pages - 1))
        start = self.page * PER_PAGE

        for entry in entries[start:start + PER_PAGE]:
            text = discord.ui.TextDisplay(_battle_text(entry))
            code = entry.get("armyShareCode")
            if code:
                self.container.add_item(discord.ui.Section(
                    text,
                    accessory=ui.Button(label="Copy Army", style=discord.ButtonStyle.link, url=army_link(code)),
                ))
            else:
                self.container.add_item(text)

        self.container.add_item(discord.ui.TextDisplay(
            f"-# Page {self.page + 1}/{pages}  ·  {len(entries)} battles{self._filter_labels()}"
        ))
        self._render_controls(pages)

    def _render_controls(self, pages: int) -> None:
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= pages - 1
        self.container.add_item(discord.ui.Separator())
        if pages > 1:
            self.container.add_item(discord.ui.ActionRow(self.prev_btn, self.next_btn))
        if self.select:
            self.container.add_item(discord.ui.ActionRow(self.select))

    # -- callbacks ----------------------------------------------------------------

    async def on_prev(self, interaction: discord.Interaction):
        self.page -= 1
        self.render()
        await interaction.response.edit_message(view=self)

    async def on_next(self, interaction: discord.Interaction):
        self.page += 1
        self.render()
        await interaction.response.edit_message(view=self)

    async def on_select(self, interaction: discord.Interaction):
        tag = self.select.values[0]
        await interaction.response.defer()
        if tag not in self.logs:
            self.logs[tag] = await fetch_battlelog_for(tag, self.api_key)
        self.current_tag = tag
        self.page = 0
        self.render()
        await interaction.edit_original_response(view=self)

    async def on_timeout(self):
        self.prev_btn.disabled = True
        self.next_btn.disabled = True
        if self.select:
            self.select.disabled = True


# --- Cog ---------------------------------------------------------------------------

class Battles(commands.Cog):
    """Battle log commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_key: str = os.getenv("COC_API_KEY", "")

    @app_commands.command(
        name="nexbattles",
        description="Browse a player's recent battles (attacks and defenses) with copyable armies",
    )
    @app_commands.describe(
        tag="Player tag to look up (defaults to your linked accounts)",
        type="Filter by battle type",
        side="Filter by attacks or defenses",
    )
    @app_commands.choices(
        type=[
            app_commands.Choice(name="All", value="all"),
            app_commands.Choice(name="Ranked", value="ranked"),
            app_commands.Choice(name="Casual", value="casual"),
        ],
        side=[
            app_commands.Choice(name="All", value="all"),
            app_commands.Choice(name="Attacks", value="attacks"),
            app_commands.Choice(name="Defenses", value="defenses"),
        ],
    )
    async def nexbattles(
        self,
        interaction: discord.Interaction,
        tag: Optional[str] = None,
        type: Optional[str] = "all",
        side: Optional[str] = "all",
    ):
        if tag:
            await interaction.response.defer()
            tag = normalize_tag(tag)
            async with aiohttp.ClientSession() as session:
                player = await fetch_player(session, tag, self.api_key)
            if not player:
                await interaction.followup.send(
                    f"{emoji('nex_error')} No player found for `{tag}`. Double-check the tag!",
                    ephemeral=True,
                )
                return
            players = [player]
        else:
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

        first = players[0]
        logs  = {first["tag"]: await fetch_battlelog_for(first["tag"], self.api_key)}
        view  = BattleLogLayout(players, logs, self.api_key,
                                battle_type=type or "all", side=side or "all")
        await interaction.followup.send(view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(Battles(bot))
