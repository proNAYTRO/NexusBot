"""/nexleague — the caller's Ranked (weekly league tournament) group standing.

Uses the ranked-mode endpoint /leaguegroup/{groupTag}/{seasonId}?playerTag=...
(the player payload's currentLeagueGroupTag / currentLeagueSeasonId), which
returns the full 100-player weekly group. Rank within the group is computed
here by trophy count — the API returns the members unordered.
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

# Legend I has no weekly group — the API marks it with this sentinel group tag
# and those players compete in the single global pool (ranked on the global
# leaderboard) instead.
GLOBAL_GROUP_TAG = "#0"
GLOBAL_RANK_DEPTH = 200


# --- CoC API helpers -------------------------------------------------------------

async def fetch_league_group(
    session: aiohttp.ClientSession, group_tag: str, season_id: int,
    player_tag: str, api_key: str,
) -> Optional[dict]:
    url = (
        f"{COC_API_BASE}/leaguegroup/{quote(group_tag, safe='')}/{season_id}"
        f"?playerTag={quote(player_tag, safe='')}"
    )
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


async def fetch_global_rank(
    session: aiohttp.ClientSession, tag: str, api_key: str
) -> Optional[int]:
    url = f"{COC_API_BASE}/locations/global/rankings/players?limit={GLOBAL_RANK_DEPTH}"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with session.get(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            if resp.status != 200:
                return None
            items = (await resp.json()).get("items", [])
            entry = next((e for e in items if e.get("tag") == tag), None)
            return entry.get("rank") if entry else None
    except Exception:
        return None


async def fetch_standing_for(player: dict, api_key: str) -> tuple[str, Optional[object]]:
    """Resolve a player's ranked standing data.

    Returns ('group', group_payload_or_None) for normal weekly groups,
    ('global', rank_or_None) for Legend I's global pool, or ('none', None)
    when the account isn't placed in Ranked this season.
    """
    group_tag = player.get("currentLeagueGroupTag")
    season_id = player.get("currentLeagueSeasonId")
    if not group_tag or not season_id:
        return ("none", None)
    async with aiohttp.ClientSession() as session:
        if group_tag == GLOBAL_GROUP_TAG:
            return ("global", await fetch_global_rank(session, player["tag"], api_key))
        return ("group", await fetch_league_group(session, group_tag, season_id, player["tag"], api_key))


# --- Views -------------------------------------------------------------------------

class LeagueStandingLayout(discord.ui.LayoutView):
    """Ranked group standing card. Doubles as the account switcher when the
    caller has more than one linked account — same in-place re-render pattern
    as PlayerProfileLayout."""

    def __init__(self, players: list[dict], standings: dict[str, tuple], api_key: str):
        super().__init__(timeout=120 if len(players) > 1 else None)
        self.players = {p["tag"]: p for p in players}
        self.standings = standings  # tag -> fetch_standing_for() result, cached across switches
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
        name = player.get("name", "Unknown")
        tag  = player.get("tag", "")

        league      = player.get("leagueTier") or {}
        league_name = league.get("name", "Unranked")
        league_icon = (league.get("iconUrls") or {}).get("small")

        self.container.clear_items()

        header = discord.ui.TextDisplay(
            f"## {emoji('nn_nexus')} Ranked Standing — {name}\n-# `{tag}`  ·  {league_name}"
        )
        if league_icon:
            self.container.add_item(discord.ui.Section(header, accessory=discord.ui.Thumbnail(league_icon)))
        else:
            self.container.add_item(header)

        self.container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        self._render_standing(player)

        if self.select:
            self.container.add_item(discord.ui.Separator())
            self.container.add_item(discord.ui.ActionRow(self.select))

    def _render_standing(self, player: dict) -> None:
        tag = player.get("tag", "")
        kind, data = self.standings.get(tag, ("none", None))

        if kind == "none":
            self.container.add_item(discord.ui.TextDisplay(
                f"{emoji('nex_warning')} This account isn't placed in a Ranked group this season."
            ))
            return

        if kind == "global":
            self._render_global(player, data)
            return

        group = data
        if not group:
            self.container.add_item(discord.ui.TextDisplay(
                f"{emoji('nex_error')} Couldn't fetch this account's league group right now."
            ))
            return

        members = group.get("members", [])
        me = next((m for m in members if m.get("playerTag") == tag), None)
        if not me:
            self.container.add_item(discord.ui.TextDisplay(
                f"{emoji('nex_error')} This account wasn't found in its league group."
            ))
            return

        trophies = me.get("leagueTrophies", 0)
        rank     = 1 + sum(1 for m in members if m.get("leagueTrophies", 0) > trophies)
        atk_w    = me.get("attackWinCount", 0)
        atk_l    = me.get("attackLoseCount", 0)
        def_w    = me.get("defenseWinCount", 0)
        def_l    = me.get("defenseLoseCount", 0)
        clan     = me.get("clanName") or "No Clan"

        self.container.add_item(discord.ui.TextDisplay(
            f"{emoji('nex_trophy')} Group Rank: **#{rank}** of **{len(members)}**  ·  **{trophies:,}** trophies"
        ))
        self.container.add_item(discord.ui.TextDisplay(
            f"{emoji('nex_clash')} Attacks: **{atk_w}**W · **{atk_l}**L    "
            f"{emoji('nex_home')} Defenses: **{def_w}**W · **{def_l}**L"
        ))
        self.container.add_item(discord.ui.TextDisplay(f"{emoji('nex_member')} Clan: {clan}"))

        footer = f"-# Group `{player.get('currentLeagueGroupTag')}`{self._season_suffix(player)}"
        self.container.add_item(discord.ui.TextDisplay(footer))

    def _render_global(self, player: dict, rank: Optional[int]) -> None:
        """Legend I: no weekly group — show the player's global leaderboard rank."""
        trophies = player.get("trophies", 0)
        clan     = (player.get("clan") or {}).get("name", "No Clan")

        if rank:
            place = f"Global Rank: **#{rank}**"
        else:
            place = f"Global Rank: outside top **{GLOBAL_RANK_DEPTH}**"
        self.container.add_item(discord.ui.TextDisplay(
            f"{emoji('nex_trophy')} {place}  ·  **{trophies:,}** trophies"
        ))
        self.container.add_item(discord.ui.TextDisplay(
            f"{emoji('nex_info')} Legend I competes in the single global pool — no weekly group."
        ))
        self.container.add_item(discord.ui.TextDisplay(f"{emoji('nex_member')} Clan: {clan}"))
        self.container.add_item(discord.ui.TextDisplay(
            f"-# Global Legend pool{self._season_suffix(player)}"
        ))

    @staticmethod
    def _season_suffix(player: dict) -> str:
        season_id = player.get("currentLeagueSeasonId")
        if isinstance(season_id, int) and season_id > 1_500_000_000:
            return f"  ·  Season started <t:{season_id}:D>"
        return ""

    async def on_select(self, interaction: discord.Interaction):
        tag = self.select.values[0]
        await interaction.response.defer()
        player = self.players.get(tag)
        if not player:
            await interaction.followup.send(
                f"{emoji('nex_error')} Couldn't fetch data for `{tag}`.", ephemeral=True
            )
            return
        if tag not in self.standings:
            self.standings[tag] = await fetch_standing_for(player, self.api_key)
        self.render(player)
        await interaction.edit_original_response(view=self)

    async def on_timeout(self):
        if self.select:
            self.select.disabled = True


# --- Cog ---------------------------------------------------------------------------

class League(commands.Cog):
    """Ranked league standing commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_key: str = os.getenv("COC_API_KEY", "")

    @app_commands.command(
        name="nexleague",
        description="View a Ranked league group standing",
    )
    @app_commands.describe(tag="Player tag to look up (defaults to your linked accounts)")
    async def nexleague(self, interaction: discord.Interaction, tag: Optional[str] = None):
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

        first     = players[0]
        standings = {first["tag"]: await fetch_standing_for(first, self.api_key)}
        await interaction.followup.send(view=LeagueStandingLayout(players, standings, self.api_key))


async def setup(bot: commands.Bot):
    await bot.add_cog(League(bot))
