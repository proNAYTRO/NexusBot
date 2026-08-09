import os
import re
import asyncio
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional
from collections import defaultdict

from cogs.cwl.capture import FAMILY_CLANS
from cogs.clash.player import load_data as load_linked_accounts
from utils.db import get_seasons
from utils.clashking import fetch_player_warhits, warhits_to_attacks
from utils.emojis import emoji

NEXUS_RED = 0xE8173A
SEASON_RE = re.compile(r"^\d{4}-\d{2}$")
WAR_TYPE_LABELS = {"regular": "Regular", "cwl": "CWL", "both": "Regular and CWL"}
COC_API_BASE = "https://api.clashofclans.com/v1"
HTTP_TIMEOUT = aiohttp.ClientTimeout(connect=5, sock_read=20)


def _norm(tag: str) -> str:
    t = tag.strip().upper()
    return t if t.startswith("#") else "#" + t


def _season_label(season: str) -> str:
    try:
        return datetime.strptime(season, "%Y-%m").strftime("%b %Y")
    except ValueError:
        return season


def _is_farm_hit(a: dict) -> bool:
    return a["stars"] == 1 and a["destruction"] < 50


def _th_bucket(a: dict) -> str:
    diff = a["attacker_th"] - a["defender_th"]
    if diff == 0:
        return "equal"
    if diff == 1:
        return "+1"
    if diff == 2:
        return "+2"
    return "other"


def _short_name(name: str, max_len: int = 12) -> str:
    """Truncate long in-game names so the table stays narrow enough to fit
    a mobile screen's embed width without forcing horizontal scroll."""
    return name if len(name) <= max_len else name[: max_len - 1] + "…"


def _last_n_wars(attacks: list[dict], n: int) -> set:
    """war_ids of the n most recently-started wars represented in attacks."""
    seen = {}
    for a in attacks:
        seen.setdefault(a["war_id"], a["start_time"])
    ordered = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)
    return {war_id for war_id, _ in ordered[:n]}


def build_leaderboard_layout(subject_name: str, attacks: list[dict],
                             min_stars: int, war_type: str,
                             scope_label: str) -> discord.ui.LayoutView:
    """Render the ClashPerk-style ranked attack success-rate table as a
    Components V2 layout."""
    by_player = defaultdict(lambda: {"hits": 0, "attempts": 0, "th": 0, "name": "?"})
    for a in attacks:
        p = by_player[a["attacker_tag"]]
        p["attempts"] += 1
        if a["stars"] >= min_stars:
            p["hits"] += 1
        p["th"] = a["attacker_th"]
        p["name"] = a["attacker_name"] or "?"

    rows = []
    for d in by_player.values():
        rate = d["hits"] / d["attempts"] * 100 if d["attempts"] else 0.0
        rows.append((rate, d["hits"], d["attempts"], d["th"], d["name"]))
    rows.sort(key=lambda r: (-r[0], -r[1]))

    # Each row is its own single-backtick inline-code span (not one shared ```
    # block) — Discord renders that as an individual pill-shaped row with its own
    # background, matching the /nexroster list look instead of one big rectangle.
    head = f"{'#':<4}{'TH':<4}{'RATE%':<7}{'HITS':<7}NAME"
    lines = [f"`{head}`"]
    for i, (rate, hits, attempts, th, name) in enumerate(rows[:25], start=1):
        # Strip stray backticks so a name can't prematurely close the inline
        # code span and break that row's formatting.
        nm = _short_name((name or "?").replace("`", "'"))
        row = f"{i:<4}{str(th):<4}{f'{rate:.1f}':<7}{f'{hits}/{attempts}':<7}{nm}"
        lines.append(f"`{row}`")

    war_count = len({a["war_id"] for a in attacks})
    now_unix = int(datetime.now(timezone.utc).timestamp())

    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_color=NEXUS_RED)
    container.add_item(discord.ui.TextDisplay(
        f"## {emoji('nn_nexus')} {subject_name}\n-# {min_stars}★ Attack Success Rates"
    ))
    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
    container.add_item(discord.ui.TextDisplay("\n".join(lines)))
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(
        f"-# War Type: {WAR_TYPE_LABELS[war_type]} ({scope_label}, {war_count} wars) "
        f"• <t:{now_unix}:R>"
    ))
    view.add_item(container)
    return view


class DataVerify(commands.Cog):
    """Self-service ClashPerk-style attack success-rate leaderboard (Town Hall /
    rate% / hits / name). Sourced entirely from ClashKing's global war history —
    not the local capture DB, which was found to be missing CWL windows the
    roster metric depends on (see utils/roster_scoring.py). ClashKing's per-war
    side attribution is corrected in utils/clashking.py's warhits_to_attacks()
    (attributes to whichever side is actually a family clan, not blindly to
    war_data.clan), so results are trustworthy for family play."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def season_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        current_lower = current.lower()
        choices = []
        for season in get_seasons():
            label = f"Since {_season_label(season)}"
            if current_lower in label.lower() or current_lower in season.lower():
                choices.append(app_commands.Choice(name=label, value=season))
        return choices[:25]

    async def clan_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        current_lower = current.lower()
        choices = []
        for clan_tag, name in FAMILY_CLANS.items():
            label = f"{name} ({clan_tag})"
            if current_lower in label.lower():
                choices.append(app_commands.Choice(name=label, value=clan_tag))
        return choices[:25]

    @app_commands.command(
        name="nexstats",
        description="Attack success-rate leaderboard: yours, another member's, or a family clan's",
    )
    @app_commands.describe(
        clan="View a family clan's leaderboard instead of your own linked accounts",
        user="View another member's linked accounts instead of your own (ignored if clan is given)",
        war_type="Which war type(s) to include",
        season="Include data since this season (e.g. Jul 2026)",
        days="Only include wars from the last N days",
        wars="Only include the last N wars (overrides season/days if given)",
        defender_th="Only count attacks against this defender Town Hall level",
        compare="Attacker TH vs defender TH matchup",
        attempt="Fresh (first hit on a base) vs cleanup (repeat hit)",
        min_stars="Stars needed to count as a 'hit' in the rate (default 3)",
        filter_farm_hits="Exclude 1★ / <50% destruction attacks",
        family_only="On: only wars played in a family clan. Off: full ClashKing history (e.g. a fresh transfer's old clan).",
    )
    @app_commands.autocomplete(season=season_autocomplete, clan=clan_autocomplete)
    async def nexstats(
        self,
        interaction: discord.Interaction,
        clan: Optional[str] = None,
        user: Optional[discord.Member] = None,
        war_type: Literal["regular", "cwl", "both"] = "both",
        season: Optional[str] = None,
        days: Optional[int] = None,
        wars: Optional[int] = None,
        defender_th: Optional[int] = None,
        compare: Literal["all", "equal", "+1", "+2"] = "all",
        attempt: Literal["both", "fresh", "cleanup"] = "both",
        min_stars: Optional[int] = None,
        filter_farm_hits: bool = False,
        family_only: bool = True,
    ):
        # ── Resolve "since" scope: season > days (season wins if both given) ─
        scope_label = "All time"
        since = None
        since_unix = 0
        if season:
            if not SEASON_RE.match(season):
                await interaction.response.send_message(
                    f"{emoji('nex_error')} `season` must be `YYYY-MM` (e.g. `2026-07`) — pick a suggestion "
                    "from the autocomplete list."
                )
                return
            since_dt = datetime(int(season[:4]), int(season[5:7]), 1, tzinfo=timezone.utc)
            since = f"{season}-01T00:00:00"
            scope_label = f"Since {_season_label(season)}"
        elif days:
            since_dt = datetime.now(timezone.utc) - timedelta(days=days)
            since = since_dt.strftime("%Y-%m-%dT%H:%M:%S")
            scope_label = f"Last {days} days"
        else:
            since_dt = None
        # Bound the ClashKing fetch server-side with a 3-day buffer so a war whose
        # prep started before `since` isn't dropped; the exact `since` filter runs
        # in warhits_to_attacks and mirrors local start_time semantics.
        if since_dt is not None:
            since_unix = max(0, int(since_dt.timestamp()) - 3 * 86400)

        await interaction.response.defer()

        db_war_type = None if war_type == "both" else war_type

        # ── Resolve subject → the set of player tags to query on ClashKing ───
        is_clan = clan is not None
        if is_clan:
            clan = _norm(clan)
            if clan not in FAMILY_CLANS:
                await interaction.followup.send(
                    f"{emoji('nex_error')} `{clan}` isn't one of the family clans I track. "
                    "Pick one from the autocomplete list."
                )
                return
            subject_desc = f"`{FAMILY_CLANS[clan]}`"
            subject_name = FAMILY_CLANS[clan]
            tags = await self._clan_member_tags(clan)
            if not tags:
                await interaction.followup.send(
                    f"{emoji('nex_error')} Couldn't fetch the current member list for `{FAMILY_CLANS[clan]}`."
                )
                return
        else:
            target_user = user or interaction.user
            linked = load_linked_accounts()
            tags = linked.get(str(target_user.id), [])
            if not tags:
                if target_user.id == interaction.user.id:
                    await interaction.followup.send(
                        f"{emoji('nex_error')} You don't have any linked accounts. Use `/nexlink` first, "
                        "or pass `clan` to view a family clan's leaderboard instead."
                    )
                else:
                    await interaction.followup.send(
                        f"{emoji('nex_error')} **{target_user.display_name}** doesn't have any linked accounts."
                    )
                return
            subject_desc = (
                "your linked accounts" if target_user.id == interaction.user.id
                else f"**{target_user.display_name}**'s linked accounts"
            )
            subject_name = target_user.display_name

        # ── Fetch every tag's war history from ClashKing concurrently ────────
        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(
                *[fetch_player_warhits(session, t, since_unix=since_unix) for t in tags],
                return_exceptions=True,
            )

        attacks: list[dict] = []
        for items in results:
            if isinstance(items, list):
                attacks.extend(
                    warhits_to_attacks(items, war_type=db_war_type, since=since,
                                       family_clans=FAMILY_CLANS)
                )

        # Clan mode: keep only attacks made while on *this* family clan, so the
        # board reflects the clan's own wars (not each member's whole history).
        # Player/user mode: `family_only` (default on) restricts to family-clan
        # wars; turn it off to see the account's full ClashKing history (e.g.
        # a fresh transfer's play at their old clan).
        if is_clan:
            attacks = [a for a in attacks if a["family_clan_tag"] == clan]
        elif family_only:
            attacks = [a for a in attacks if a["family_clan_tag"] in FAMILY_CLANS]

        if defender_th is not None:
            attacks = [a for a in attacks if a["defender_th"] == defender_th]

        if not attacks:
            await interaction.followup.send(
                f"{emoji('nex_info')} No attacks found on ClashKing for {subject_desc} with these filters."
            )
            return

        # ── Last N wars ──────────────────────────────────────────────────────
        if wars:
            keep_war_ids = _last_n_wars(attacks, wars)
            attacks = [a for a in attacks if a["war_id"] in keep_war_ids]
            scope_label = f"Last {wars} wars"

        # ── TH matchup compare ───────────────────────────────────────────────
        if compare != "all":
            attacks = [a for a in attacks if _th_bucket(a) == compare]

        # ── Final attack-level filters ───────────────────────────────────────
        if attempt != "both":
            want_fresh = 1 if attempt == "fresh" else 0
            attacks = [a for a in attacks if a["is_fresh"] == want_fresh]

        if filter_farm_hits:
            attacks = [a for a in attacks if not _is_farm_hit(a)]

        if not attacks:
            await interaction.followup.send(
                f"{emoji('nex_info')} No attacks found on ClashKing for {subject_desc} with these filters."
            )
            return

        threshold = min_stars if min_stars is not None else 3
        view = build_leaderboard_layout(
            subject_name, attacks, threshold, war_type, scope_label
        )
        await interaction.followup.send(view=view)

    # ── Helpers ──────────────────────────────────────────────────────────────

    async def _clan_member_tags(self, clan_tag: str) -> list[str]:
        """Current member tags of a clan, via the CoC API (used for clan-mode
        fan-out to per-member ClashKing lookups)."""
        api_key = os.getenv("COC_API_KEY", "")
        url = f"{COC_API_BASE}/clans/{clan_tag.replace('#', '%23')}"
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=HTTP_TIMEOUT) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
        except Exception:
            return []
        return [m["tag"] for m in data.get("memberList", [])]


async def setup(bot: commands.Bot):
    await bot.add_cog(DataVerify(bot))
