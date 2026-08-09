import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import json
import os
import urllib.parse

from utils.emojis import emoji

COC_API_BASE = "https://api.clashofclans.com/v1"
CLAN_TAGS_FILE = "data/clan_tags.json"
ALLOWED_USER_ID = 1048729773926522981
NEXUS_RED = 0xE8173A

WAR_LEAGUE_EMOJI = {
    48000000: emoji("nex_clash"),      # Unranked
    48000001: emoji("Bronze_3"),       # Bronze League III
    48000002: emoji("Bronze_2"),       # Bronze League II
    48000003: emoji("Bronze_1"),       # Bronze League I
    48000004: emoji("Silver_3"),       # Silver League III
    48000005: emoji("Silver_2"),       # Silver League II
    48000006: emoji("Silver_1"),       # Silver League I
    48000007: emoji("Gold_3"),         # Gold League III
    48000008: emoji("Gold_2"),         # Gold League II
    48000009: emoji("Gold_1"),         # Gold League I
    48000010: emoji("Crystal_3"),      # Crystal League III
    48000011: emoji("Crystal_2"),      # Crystal League II
    48000012: emoji("Crystal_1"),      # Crystal League I
    48000013: emoji("Master_3"),       # Master League III
    48000014: emoji("Master_2"),       # Master League II
    48000015: emoji("Master_1"),       # Master League I
    48000016: emoji("Champ_3"),        # Champion League III
    48000017: emoji("Champ_2"),        # Champion League II
    48000018: emoji("Champ_1"),        # Champion League I
    48000019: emoji("Titan_3"),        # Titan League III
    48000020: emoji("Titan_2"),        # Titan League II
    48000021: emoji("Titan_1"),        # Titan League I
    48000022: emoji("Legend_L"),       # Legend League
}


class ClanLayout(discord.ui.LayoutView):
    def __init__(self, data: dict, tag: str):
        super().__init__(timeout=None)

        name: str = data["name"]
        members: int = data.get("members", 0)
        war_league_id = data.get("warLeague", {}).get("id", 0)
        war_league_name = data.get("warLeague", {}).get("name", "Not Placed")
        war_league_emoji = WAR_LEAGUE_EMOJI.get(war_league_id, emoji("nex_clash"))
        clan_points: int = data.get("clanPoints", 0)
        badge_url: str | None = data.get("badgeUrls", {}).get("medium")

        raw_tag = tag.lstrip("#")
        clan_url = f"https://link.clashofclans.com/en?action=OpenClanProfile&tag={raw_tag}"

        container = discord.ui.Container(accent_color=NEXUS_RED)

        header = discord.ui.TextDisplay(f"## {emoji('nn_nexus')} [{name}]({clan_url})\n-# {tag}")
        if badge_url:
            container.add_item(discord.ui.Section(header, accessory=discord.ui.Thumbnail(badge_url)))
        else:
            container.add_item(header)

        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        stats = (
            f"{emoji('nex_member')} **{members}/50** Members\n"
            f"{war_league_emoji} **{war_league_name}**\n"
            f"{emoji('nex_trophy')} **{clan_points:,}** Trophies"
        )
        container.add_item(discord.ui.TextDisplay(stats))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(
            discord.ui.Button(label="Open in Game", emoji="🔗", style=discord.ButtonStyle.link, url=clan_url)
        ))
        self.add_item(container)


class ClanCog(commands.Cog, name="Clan"):
    """Clan-related commands for NAYTRO-NEXUS."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_key: str = os.getenv("COC_API_KEY", "")  # matches coc.py

    # -- Helpers --------------------------------------------------------------

    def load_clans(self) -> list[dict]:
        """Load saved clan tags from disk."""
        if os.path.exists(CLAN_TAGS_FILE):
            with open(CLAN_TAGS_FILE, "r") as f:
                return json.load(f)
        return []

    def save_clans(self, clans: list[dict]) -> None:
        """Persist clan tags to disk."""
        os.makedirs(os.path.dirname(CLAN_TAGS_FILE), exist_ok=True)
        with open(CLAN_TAGS_FILE, "w") as f:
            json.dump(clans, f, indent=2)

    def normalize_tag(self, tag: str) -> str:
        """Ensure tag is uppercase and has a leading #."""
        tag = tag.strip().upper()
        if not tag.startswith("#"):
            tag = "#" + tag
        return tag

    async def fetch_clan(self, tag: str) -> dict | None:
        """Fetch a clan from the CoC API. Returns None on failure."""
        encoded = urllib.parse.quote(tag, safe="")
        url = f"{COC_API_BASE}/clans/{encoded}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    return None
        except Exception:
            return None

    # -- Autocomplete -----------------------------------------------------------

    async def clan_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Return saved clans as autocomplete suggestions, filtered by current input."""
        clans = self.load_clans()
        current_lower = current.lower()
        choices = []
        for clan in clans:
            tag: str = clan["tag"]
            name: str = clan["name"]
            label = f"{name} ({tag})"
            if current_lower in label.lower():
                choices.append(app_commands.Choice(name=label, value=tag))
        return choices[:25]

    # -- Commands -----------------------------------------------------------------

    @app_commands.command(
        name="nexaddclan",
        description="[Admin] Add a clan tag to the recommended list",
    )
    @app_commands.describe(tag="Clan tag to save (e.g. #GGRY80R0)")
    async def nexaddclan(self, interaction: discord.Interaction, tag: str) -> None:
        # Restrict to allowed user
        if interaction.user.id != ALLOWED_USER_ID:
            await interaction.response.send_message(
                f"{emoji('nex_error')} You don't have permission to use this command.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        tag = self.normalize_tag(tag)
        clans = self.load_clans()

        # Duplicate check
        if any(c["tag"] == tag for c in clans):
            await interaction.followup.send(
                f"{emoji('nex_warning')} `{tag}` is already in the saved list.", ephemeral=True
            )
            return

        # Validate tag exists and grab the clan name
        data = await self.fetch_clan(tag)
        if not data:
            await interaction.followup.send(
                f"{emoji('nex_error')} No clan found for `{tag}`. Check the tag and try again.",
                ephemeral=True,
            )
            return

        clan_name: str = data["name"]
        clans.append({"tag": tag, "name": clan_name})
        self.save_clans(clans)

        await interaction.followup.send(
            f"{emoji('nex_success')} **{clan_name}** (`{tag}`) has been added to the recommended clans list.",
            ephemeral=True,
        )

    @app_commands.command(
        name="nexclan",
        description="Look up a Clash of Clans clan",
    )
    @app_commands.describe(tag="Enter a clan tag or pick a saved clan")
    @app_commands.autocomplete(tag=clan_autocomplete)
    async def nexclan(self, interaction: discord.Interaction, tag: str) -> None:
        await interaction.response.defer()
        tag = self.normalize_tag(tag)

        data = await self.fetch_clan(tag)
        if not data:
            await interaction.followup.send(
                f"{emoji('nex_error')} No clan found for `{tag}`.", ephemeral=True
            )
            return

        await interaction.followup.send(view=ClanLayout(data, tag))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ClanCog(bot))
