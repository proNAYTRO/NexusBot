"""
/nexclanembed and /nexrules — clan showcase embed management.

Commands:

/nexclanembed edit
    Choose a clan and manage its showcase embed.

/nexclanembed list
    Show every clan and whether its showcase embed is live.

/nexclanembed remove
    Delete a clan's webhook embed and stored configuration.

/nexrules embed
    Manage the clan index embed.

Clan showcase embeds:

- Use live Clash of Clans API information.
- Automatically use the clan badge as the thumbnail.
- Clan name is a clickable Clash profile link.
- No emoji setting.
- No tier setting.
- No "Requirements" heading.
- Clan tag is not displayed.
- No war frequency.
- War W/L/D is displayed.
- CWL league is displayed.
- Capital League is displayed.
- Capital Hall level is displayed.
- Capital district levels are displayed.
- Town Hall composition is displayed.
- Clan showcase messages are sent through Discord webhooks.
- Multiple clans can use the same webhook.
- Each clan can use a different thread.

Environment variable:

    COC_API_KEY
"""

from __future__ import annotations

import os
from collections import Counter
from urllib.parse import quote

import aiohttp
import discord

from discord import app_commands
from discord.ext import commands

from ._clan_embed_store import (
    ClanEmbedStore,
    ClanEmbedEntry,
    IndexEmbedConfig,
    load_clan_tags,
)


# ---------------------------------------------------------------------------
# Clash API
# ---------------------------------------------------------------------------

COC_API_BASE = (
    "https://api.clashofclans.com/v1"
)


class ClashAPIError(Exception):
    pass


class ClashAPI:

    def __init__(
        self,
        api_key: str,
    ):
        self.api_key = api_key.strip()

    async def get_clan(
        self,
        clan_tag: str,
    ) -> dict:

        if not self.api_key:
            raise ClashAPIError(
                "COC_API_KEY is missing."
            )

        encoded_tag = quote(
            clan_tag.strip(),
            safe="",
        )

        url = (
            f"{COC_API_BASE}/clans/"
            f"{encoded_tag}"
        )

        headers = {
            "Authorization": (
                f"Bearer {self.api_key}"
            ),
            "Accept": "application/json",
        }

        timeout = aiohttp.ClientTimeout(
            total=15
        )

        async with aiohttp.ClientSession(
            headers=headers,
            timeout=timeout,
        ) as session:

            async with session.get(
                url
            ) as response:

                try:
                    data = await response.json()

                except Exception:
                    data = {}

                if response.status != 200:

                    reason = data.get(
                        "reason",
                        f"HTTP {response.status}",
                    )

                    raise ClashAPIError(
                        reason
                    )

                return data


# ---------------------------------------------------------------------------
# Clash API formatting
# ---------------------------------------------------------------------------

def build_clan_link(
    tag: str,
) -> str:

    encoded_tag = quote(
        tag.strip(),
        safe="",
    )

    return (
        "https://link.clashofclans.com/en"
        "?action=OpenClanProfile"
        f"&tag={encoded_tag}"
    )


def get_badge_url(
    clan: dict,
) -> str | None:

    badge_urls = (
        clan.get("badgeUrls")
        or {}
    )

    return (
        badge_urls.get("large")
        or badge_urls.get("medium")
        or badge_urls.get("small")
    )


def format_war_performance(
    clan: dict,
) -> str:

    # Clash API uses warWins / warLosses / warTies.
    wins = clan.get(
        "warWins",
        0,
    )

    losses = clan.get(
        "warLosses",
        0,
    )

    ties = clan.get(
        "warTies",
        0,
    )

    if clan.get(
        "isWarLogPublic",
        True,
    ) is False:

        return "War log private"

    return (
        f"**{wins}W** · "
        f"**{losses}L** · "
        f"**{ties}D**"
    )


def format_cwl_league(
    clan: dict,
) -> str:

    league = (
        clan.get("warLeague")
        or {}
    )

    return league.get(
        "name",
        "Unranked",
    )


def format_capital_league(
    clan: dict,
) -> str:

    league = (
        clan.get("capitalLeague")
        or {}
    )

    return league.get(
        "name",
        "Unranked",
    )


def format_capital_info(
    clan: dict,
) -> str:

    capital = (
        clan.get("clanCapital")
        or {}
    )

    hall_level = capital.get(
        "capitalHallLevel",
        "—",
    )

    districts = (
        capital.get("districts")
        or []
    )

    if not districts:
        return (
            f"Hall Level **{hall_level}**"
        )

    district_lines = []

    for district in districts:

        district_name = district.get(
            "name",
            "Unknown",
        )

        district_level = district.get(
            "districtHallLevel",
            "—",
        )

        district_lines.append(
            f"{district_name} "
            f"**{district_level}**"
        )

    return (
        f"Hall Level **{hall_level}**\n"
        + " · ".join(district_lines)
    )


def format_townhall_composition(
    clan: dict,
) -> str:

    members = (
        clan.get("memberList")
        or []
    )

    town_halls = Counter()

    for member in members:

        level = member.get(
            "townHallLevel"
        )

        if level is not None:
            town_halls[int(level)] += 1

    if not town_halls:
        return "No member data available"

    ordered = sorted(
        town_halls.items(),
        key=lambda item: item[0],
        reverse=True,
    )

    return " · ".join(
        f"TH{level} × {count}"
        for level, count in ordered
    )


# ---------------------------------------------------------------------------
# Clan showcase embed
# ---------------------------------------------------------------------------

def build_clan_embed(
    name: str,
    entry: ClanEmbedEntry,
    clan: dict,
) -> discord.Embed:

    clan_name = (
        clan.get("name")
        or name
    )

    clan_tag = (
        clan.get("tag")
        or entry.tag
    )

    clan_link = build_clan_link(
        clan_tag
    )

    # ------------------------------------------------------------------
    # Clan name is the clickable hyperlink at the absolute top.
    # ------------------------------------------------------------------

    embed = discord.Embed(
        title=clan_name,
        url=clan_link,
        color=discord.Color.dark_purple(),
    )

    # ------------------------------------------------------------------
    # Description + entry information.
    #
    # IMPORTANT:
    # There is intentionally NO "Requirements" heading.
    # ------------------------------------------------------------------

    text_blocks = []

    if entry.description.strip():
        text_blocks.append(
            entry.description.strip()
        )

    if entry.requirements.strip():
        text_blocks.append(
            entry.requirements.strip()
        )

    if text_blocks:

        embed.description = (
            "\n\n".join(text_blocks)
        )

    else:

        embed.description = (
            "No description set."
        )

    # ------------------------------------------------------------------
    # Live clan badge.
    # ------------------------------------------------------------------

    badge_url = get_badge_url(
        clan
    )

    if badge_url:
        embed.set_thumbnail(
            url=badge_url
        )

    # ------------------------------------------------------------------
    # Live API information.
    # ------------------------------------------------------------------

    embed.add_field(
        name="War Performance",
        value=format_war_performance(
            clan
        ),
        inline=True,
    )

    embed.add_field(
        name="CWL League",
        value=format_cwl_league(
            clan
        ),
        inline=True,
    )

    embed.add_field(
        name="Capital League",
        value=format_capital_league(
            clan
        ),
        inline=True,
    )

    embed.add_field(
        name="Capital",
        value=format_capital_info(
            clan
        ),
        inline=False,
    )

    embed.add_field(
        name="Town Hall Composition",
        value=format_townhall_composition(
            clan
        ),
        inline=False,
    )

    return embed


# ---------------------------------------------------------------------------
# Index embed
# ---------------------------------------------------------------------------

def build_index_embed(
    all_clans: dict[str, ClanEmbedEntry],
) -> discord.Embed:

    embed = discord.Embed(
        title=(
            "Explore our clans "
            "to find the perfect fit"
        ),
        color=discord.Color.dark_purple(),
    )

    posted = {
        name: entry
        for name, entry in all_clans.items()
        if entry.is_posted
    }

    if not posted:

        embed.description = (
            "*No clans posted yet.*"
        )

        return embed

    lines = []

    for name, entry in posted.items():

        lines.append(
            f"**{name}**\n"
            f"{entry.requirements or '—'}"
        )

    embed.description = (
        "\n\n".join(lines)
    )

    return embed


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def status_line(
    name: str,
    entry: ClanEmbedEntry | None,
    guild_id: int,
) -> str:

    if entry and entry.is_posted:

        jump = (
            "https://discord.com/channels/"
            f"{guild_id}/"
            f"{entry.channel_id}/"
            f"{entry.message_id}"
        )

        return (
            f"● {name} — "
            f"[jump to embed]({jump})"
        )

    return (
        f"○ {name} — not set up"
    )


# ---------------------------------------------------------------------------
# Edit Info Modal
# ---------------------------------------------------------------------------

class EditInfoModal(
    discord.ui.Modal,
    title="Edit Clan Info",
):

    def __init__(
        self,
        panel: "ClanPanelView",
        entry: ClanEmbedEntry,
    ):
        super().__init__()

        self.panel = panel

        # --------------------------------------------------------------
        # Description
        # --------------------------------------------------------------

        self.description_input = (
            discord.ui.TextInput(
                label="Description",
                style=discord.TextStyle.paragraph,
                default=entry.description,
                required=False,
                max_length=4000,
            )
        )

        # --------------------------------------------------------------
        # Requirements / entry information.
        #
        # The field itself can contain anything you want:
        # entry requirements, hit rate, wars required, etc.
        #
        # It will NOT be labelled "Requirements" on the embed.
        # --------------------------------------------------------------

        self.requirements_input = (
            discord.ui.TextInput(
                label="Entry Information",
                style=discord.TextStyle.paragraph,
                default=entry.requirements,
                required=False,
                max_length=1024,
            )
        )

        # --------------------------------------------------------------
        # Webhook
        # --------------------------------------------------------------

        self.webhook_input = (
            discord.ui.TextInput(
                label="Discord Webhook URL",
                placeholder=(
                    "https://discord.com/api/webhooks/..."
                ),
                default=entry.webhook_url,
                required=True,
                max_length=4000,
            )
        )

        self.add_item(
            self.description_input
        )

        self.add_item(
            self.requirements_input
        )

        self.add_item(
            self.webhook_input
        )

    async def on_submit(
        self,
        interaction: discord.Interaction,
    ):

        webhook_url = (
            self.webhook_input.value.strip()
        )

        if not webhook_url.startswith(
            "https://discord.com/api/webhooks/"
        ):

            await interaction.response.send_message(
                (
                    "That does not look like "
                    "a valid Discord webhook URL."
                ),
                ephemeral=True,
            )

            return

        entry = (
            self.panel.store.get_or_create_clan(
                self.panel.clan_name,
                self.panel.tag,
            )
        )

        entry.description = (
            self.description_input.value.strip()
        )

        entry.requirements = (
            self.requirements_input.value.strip()
        )

        entry.webhook_url = webhook_url

        self.panel.store.upsert_clan(
            self.panel.clan_name,
            entry,
        )

        await self.panel.publish_or_edit(
            interaction,
            entry,
        )


# ---------------------------------------------------------------------------
# Location picker
# ---------------------------------------------------------------------------

class LocationSelect(
    discord.ui.ChannelSelect
):

    def __init__(
        self,
        panel: "ClanPanelView",
    ):

        super().__init__(
            placeholder=(
                "Choose a channel or thread…"
            ),
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
            ],
            min_values=1,
            max_values=1,
        )

        self.panel = panel

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        target = self.values[0]

        entry = (
            self.panel.store.get_or_create_clan(
                self.panel.clan_name,
                self.panel.tag,
            )
        )

        if target.type in (
            discord.ChannelType.public_thread,
            discord.ChannelType.private_thread,
        ):

            entry.thread_id = target.id
            entry.channel_id = target.parent_id

        else:

            entry.channel_id = target.id
            entry.thread_id = None

        # Moving the clan means the old message is no longer
        # considered the current message.
        entry.message_id = None

        self.panel.store.upsert_clan(
            self.panel.clan_name,
            entry,
        )

        await self.panel.publish_or_edit(
            interaction,
            entry,
        )


class LocationView(
    discord.ui.View
):

    def __init__(
        self,
        panel: "ClanPanelView",
    ):

        super().__init__(
            timeout=180
        )

        self.add_item(
            LocationSelect(panel)
        )


# ---------------------------------------------------------------------------
# Clan panel
# ---------------------------------------------------------------------------

class ClanPanelView(
    discord.ui.View
):

    def __init__(
        self,
        store: ClanEmbedStore,
        clan_name: str,
        tag: str,
        api: ClashAPI,
    ):

        super().__init__(
            timeout=300
        )

        self.store = store
        self.clan_name = clan_name
        self.tag = tag
        self.api = api

    async def get_clan_data(
        self,
    ) -> dict:

        return await self.api.get_clan(
            self.tag
        )

    async def publish_or_edit(
        self,
        interaction: discord.Interaction,
        entry: ClanEmbedEntry,
    ):

        # --------------------------------------------------------------
        # Configuration checks.
        # --------------------------------------------------------------

        if not entry.webhook_url:

            await self._render(
                interaction,
                entry,
                error=(
                    "No webhook URL configured. "
                    "Use Edit Info first."
                ),
            )

            return

        if not entry.channel_id:

            await self._render(
                interaction,
                entry,
                error=(
                    "No channel/thread configured. "
                    "Use Set Location first."
                ),
            )

            return

        # --------------------------------------------------------------
        # Fetch completely fresh Clash API information.
        # --------------------------------------------------------------

        try:

            clan = await self.get_clan_data()

        except ClashAPIError as exc:

            await self._render(
                interaction,
                entry,
                error=f"Clash API error: {exc}",
            )

            return

        embed = build_clan_embed(
            self.clan_name,
            entry,
            clan,
        )

        # --------------------------------------------------------------
        # Webhook session.
        # --------------------------------------------------------------

        timeout = aiohttp.ClientTimeout(
            total=20
        )

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            try:

                webhook = discord.Webhook.from_url(
                    entry.webhook_url,
                    session=session,
                )

                # ------------------------------------------------------
                # Resolve the thread.
                #
                # IMPORTANT:
                # The same webhook can be used for multiple threads.
                # Each clan's stored thread_id determines where its
                # message goes.
                # ------------------------------------------------------

                thread = None

                if entry.thread_id:

                    thread = (
                        interaction.guild.get_thread(
                            entry.thread_id
                        )
                        or interaction.guild.get_channel(
                            entry.thread_id
                        )
                    )

                # ------------------------------------------------------
                # Existing webhook message.
                # ------------------------------------------------------

                if entry.message_id:

                    try:

                        await webhook.edit_message(
                            entry.message_id,
                            embed=embed,
                            thread=thread,
                        )

                    except discord.NotFound:

                        # Message was deleted manually.
                        # Re-create it.
                        message = await webhook.send(
                            embed=embed,
                            thread=thread,
                            wait=True,
                        )

                        entry.message_id = (
                            message.id
                        )

                        self.store.upsert_clan(
                            self.clan_name,
                            entry,
                        )

                # ------------------------------------------------------
                # New webhook message.
                # ------------------------------------------------------

                else:

                    message = await webhook.send(
                        embed=embed,
                        thread=thread,
                        wait=True,
                    )

                    entry.message_id = (
                        message.id
                    )

                    self.store.upsert_clan(
                        self.clan_name,
                        entry,
                    )

            except discord.HTTPException as exc:

                await self._render(
                    interaction,
                    entry,
                    error=(
                        "Discord webhook error: "
                        f"{exc}"
                    ),
                )

                return

        await self._render(
            interaction,
            entry,
        )

    async def _render(
        self,
        interaction: discord.Interaction,
        entry: ClanEmbedEntry,
        error: str | None = None,
    ):

        # --------------------------------------------------------------
        # Build current preview.
        # --------------------------------------------------------------

        try:

            clan = await self.get_clan_data()

            embed = build_clan_embed(
                self.clan_name,
                entry,
                clan,
            )

        except Exception:

            # API unavailable — still show the editable content.
            embed = discord.Embed(
                title=self.clan_name,
                url=build_clan_link(
                    entry.tag
                ),
                description=(
                    entry.description
                    or "No description set."
                ),
                color=discord.Color.dark_purple(),
            )

            if entry.requirements:

                embed.description += (
                    "\n\n"
                    + entry.requirements
                )

        # --------------------------------------------------------------
        # Location
        # --------------------------------------------------------------

        location = "not set"

        if entry.channel_id:

            if entry.thread_id:
                location = (
                    f"<#{entry.thread_id}>"
                )

            else:
                location = (
                    f"<#{entry.channel_id}>"
                )

        # --------------------------------------------------------------
        # Status
        # --------------------------------------------------------------

        status = (
            "posted"
            if entry.is_posted
            else "not yet posted"
        )

        content = (
            f"**{self.clan_name}** — "
            f"{status} · "
            f"location: {location}"
        )

        if error:
            content += (
                f"\n⚠️ {error}"
            )

        self.refresh_btn.disabled = (
            not entry.is_posted
        )

        # --------------------------------------------------------------
        # Update ephemeral panel.
        # --------------------------------------------------------------

        if interaction.response.is_done():

            await interaction.edit_original_response(
                content=content,
                embed=embed,
                view=self,
            )

        else:

            await interaction.response.edit_message(
                content=content,
                embed=embed,
                view=self,
            )

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    @discord.ui.button(
        label="Refresh",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def refresh_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        entry = self.store.get_clan(
            self.clan_name
        )

        if not entry or not entry.is_posted:

            await interaction.response.send_message(
                "Nothing posted yet.",
                ephemeral=True,
            )

            return

        # This re-queries the Clash API.
        await self.publish_or_edit(
            interaction,
            entry,
        )

    # ------------------------------------------------------------------
    # Edit Info
    # ------------------------------------------------------------------

    @discord.ui.button(
        label="Edit Info",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def edit_info_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        entry = (
            self.store.get_or_create_clan(
                self.clan_name,
                self.tag,
            )
        )

        await interaction.response.send_modal(
            EditInfoModal(
                self,
                entry,
            )
        )

    # ------------------------------------------------------------------
    # Set Location
    # ------------------------------------------------------------------

    @discord.ui.button(
        label="Set Location",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def set_location_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        await interaction.response.send_message(
            (
                "Pick where this clan embed "
                "should live:"
            ),
            view=LocationView(self),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Clan selector
# ---------------------------------------------------------------------------

class ClanSelect(
    discord.ui.Select
):

    def __init__(
        self,
        store: ClanEmbedStore,
        tags: dict[str, str],
        api: ClashAPI,
    ):

        self.store = store
        self.tags = tags
        self.api = api

        options = []

        for name, tag in tags.items():

            entry = store.get_clan(name)

            live = (
                entry is not None
                and entry.is_posted
            )

            options.append(
                discord.SelectOption(
                    label=name,
                    description=(
                        "● live"
                        if live
                        else "○ not set up"
                    ),
                    value=name,
                )
            )

        super().__init__(
            placeholder="Choose a clan…",
            options=options[:25],
        )

    async def callback(
        self,
        interaction: discord.Interaction,
    ):

        name = self.values[0]

        tag = self.tags.get(
            name,
            "",
        )

        entry = (
            self.store.get_or_create_clan(
                name,
                tag,
            )
        )

        panel = ClanPanelView(
            self.store,
            name,
            tag,
            self.api,
        )

        panel.refresh_btn.disabled = (
            not entry.is_posted
        )

        # --------------------------------------------------------------
        # Load live API data for preview.
        # --------------------------------------------------------------

        try:

            clan = await self.api.get_clan(
                tag
            )

            embed = build_clan_embed(
                name,
                entry,
                clan,
            )

        except ClashAPIError as exc:

            embed = discord.Embed(
                title=name,
                url=build_clan_link(tag),
                description=(
                    entry.description
                    or "No description set."
                ),
                color=discord.Color.dark_purple(),
            )

            if entry.requirements:

                embed.description += (
                    "\n\n"
                    + entry.requirements
                )

            await interaction.response.edit_message(
                content=(
                    f"**{name}** — "
                    f"API unavailable: {exc}"
                ),
                embed=embed,
                view=panel,
            )

            return

        # --------------------------------------------------------------
        # Location
        # --------------------------------------------------------------

        location = "not set"

        if entry.channel_id:

            location = (
                f"<#{entry.thread_id}>"
                if entry.thread_id
                else f"<#{entry.channel_id}>"
            )

        status = (
            "posted"
            if entry.is_posted
            else "not yet posted"
        )

        await interaction.response.edit_message(
            content=(
                f"**{name}** — "
                f"{status} · "
                f"location: {location}"
            ),
            embed=embed,
            view=panel,
        )


class ClanSelectView(
    discord.ui.View
):

    def __init__(
        self,
        store: ClanEmbedStore,
        tags: dict[str, str],
        api: ClashAPI,
    ):

        super().__init__(
            timeout=180
        )

        self.add_item(
            ClanSelect(
                store,
                tags,
                api,
            )
        )


# ---------------------------------------------------------------------------
# /nexrules embed panel
# ---------------------------------------------------------------------------

class IndexPanelView(
    discord.ui.View
):

    def __init__(
        self,
        store: ClanEmbedStore,
    ):

        super().__init__(
            timeout=300
        )

        self.store = store

    async def _render(
        self,
        interaction: discord.Interaction,
        config: IndexEmbedConfig,
    ):

        embed = build_index_embed(
            self.store.all_clans()
        )

        location = "not set"

        if config.channel_id:

            location = (
                f"<#{config.thread_id}>"
                if config.thread_id
                else f"<#{config.channel_id}>"
            )

        status = (
            "posted"
            if config.is_posted
            else "not yet posted"
        )

        content = (
            f"**Index embed** — "
            f"{status} · "
            f"location: {location}"
        )

        self.refresh_btn.disabled = (
            not config.is_posted
        )

        if interaction.response.is_done():

            await interaction.edit_original_response(
                content=content,
                embed=embed,
                view=self,
            )

        else:

            await interaction.response.edit_message(
                content=content,
                embed=embed,
                view=self,
            )

    async def publish_or_edit(
        self,
        interaction: discord.Interaction,
        config: IndexEmbedConfig,
    ):

        embed = build_index_embed(
            self.store.all_clans()
        )

        if config.channel_id:

            channel = (
                interaction.guild.get_channel_or_thread(
                    config.thread_id
                )
                if config.thread_id
                else interaction.guild.get_channel(
                    config.channel_id
                )
            )

            if channel:

                if config.message_id:

                    try:

                        msg = await channel.fetch_message(
                            config.message_id
                        )

                        await msg.edit(
                            embed=embed
                        )

                    except discord.NotFound:

                        msg = await channel.send(
                            embed=embed
                        )

                        config.message_id = (
                            msg.id
                        )

                        self.store.set_index_embed(
                            config
                        )

                else:

                    msg = await channel.send(
                        embed=embed
                    )

                    config.message_id = (
                        msg.id
                    )

                    self.store.set_index_embed(
                        config
                    )

        await self._render(
            interaction,
            config,
        )

    @discord.ui.button(
        label="Refresh",
        style=discord.ButtonStyle.secondary,
    )
    async def refresh_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        config = (
            self.store.get_index_embed()
        )

        if not config.is_posted:

            await interaction.response.send_message(
                "Nothing posted yet.",
                ephemeral=True,
            )

            return

        await self.publish_or_edit(
            interaction,
            config,
        )

    @discord.ui.button(
        label="Set Location",
        style=discord.ButtonStyle.primary,
    )
    async def set_location_btn(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):

        select = discord.ui.ChannelSelect(
            placeholder=(
                "Choose a channel or thread…"
            ),
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
            ],
        )

        async def on_select(
            inner: discord.Interaction,
        ):

            target = select.values[0]

            config = (
                self.store.get_index_embed()
            )

            if target.type in (
                discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
            ):

                config.thread_id = target.id
                config.channel_id = (
                    target.parent_id
                )

            else:

                config.channel_id = target.id
                config.thread_id = None

            config.message_id = None

            self.store.set_index_embed(
                config
            )

            await self.publish_or_edit(
                inner,
                config,
            )

        select.callback = on_select

        view = discord.ui.View(
            timeout=180
        )

        view.add_item(select)

        await interaction.response.send_message(
            (
                "Pick where the index embed "
                "should live:"
            ),
            view=view,
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class ClanEmbeds(
    commands.Cog
):

    def __init__(
        self,
        bot: commands.Bot,
    ):

        self.bot = bot

        self.store = (
            ClanEmbedStore()
        )

        self.api = ClashAPI(
            os.getenv(
                "COC_API_KEY",
                "",
            )
        )

    # ------------------------------------------------------------------
    # IMPORTANT:
    # KEEPING YOUR ORIGINAL COMMAND NAME.
    #
    # This is /nexclanembed, NOT /nexclan.
    # ------------------------------------------------------------------

    nexclan = app_commands.Group(
        name="nexclanembed",
        description=(
            "Manage clan showcase embeds"
        ),
    )

    @nexclan.command(
        name="edit",
        description=(
            "Create or edit a clan showcase embed"
        ),
    )
    async def nexclan_embed(
        self,
        interaction: discord.Interaction,
    ):

        tags = load_clan_tags()

        if not tags:

            await interaction.response.send_message(
                (
                    "No clans found in "
                    "clan_tags.json."
                ),
                ephemeral=True,
            )

            return

        if not self.api.api_key:

            await interaction.response.send_message(
                (
                    "COC_API_KEY is missing "
                    "from the bot environment."
                ),
                ephemeral=True,
            )

            return

        await interaction.response.send_message(
            "Choose a clan to edit:",
            view=ClanSelectView(
                self.store,
                tags,
                self.api,
            ),
            ephemeral=True,
        )

    @nexclan.command(
        name="list",
        description=(
            "List all clans and their "
            "embed status"
        ),
    )
    async def nexclan_list(
        self,
        interaction: discord.Interaction,
    ):

        tags = load_clan_tags()

        if not tags:

            await interaction.response.send_message(
                (
                    "No clans found in "
                    "clan_tags.json."
                ),
                ephemeral=True,
            )

            return

        lines = [
            status_line(
                name,
                self.store.get_clan(name),
                interaction.guild.id,
            )
            for name in tags
        ]

        await interaction.response.send_message(
            "\n".join(lines),
            ephemeral=True,
        )

    @nexclan.command(
        name="remove",
        description=(
            "Remove a clan's posted "
            "embed and config"
        ),
    )
    @app_commands.describe(
        clan=(
            "Clan name as it appears "
            "in clan_tags.json"
        )
    )
    async def nexclan_remove(
        self,
        interaction: discord.Interaction,
        clan: str,
    ):

        entry = self.store.get_clan(
            clan
        )

        if not entry:

            await interaction.response.send_message(
                (
                    f"No embed config found "
                    f"for **{clan}**."
                ),
                ephemeral=True,
            )

            return

        confirm_view = discord.ui.View(
            timeout=60
        )

        confirm_btn = discord.ui.Button(
            label="Delete",
            style=discord.ButtonStyle.danger,
        )

        cancel_btn = discord.ui.Button(
            label="Cancel",
            style=discord.ButtonStyle.secondary,
        )

        async def do_confirm(
            inner: discord.Interaction,
        ):

            # ----------------------------------------------------------
            # Delete webhook message.
            # ----------------------------------------------------------

            if (
                entry.message_id
                and entry.webhook_url
            ):

                timeout = (
                    aiohttp.ClientTimeout(
                        total=15
                    )
                )

                async with aiohttp.ClientSession(
                    timeout=timeout
                ) as session:

                    try:

                        webhook = (
                            discord.Webhook.from_url(
                                entry.webhook_url,
                                session=session,
                            )
                        )

                        thread = None

                        if entry.thread_id:

                            thread = (
                                inner.guild.get_thread(
                                    entry.thread_id
                                )
                                or inner.guild.get_channel(
                                    entry.thread_id
                                )
                            )

                        await webhook.delete_message(
                            entry.message_id,
                            thread=thread,
                        )

                    except discord.NotFound:
                        pass

                    except discord.HTTPException:
                        pass

            self.store.remove_clan(
                clan
            )

            await inner.response.edit_message(
                content=(
                    f"Removed **{clan}**."
                ),
                view=None,
            )

        async def do_cancel(
            inner: discord.Interaction,
        ):

            await inner.response.edit_message(
                content="Cancelled.",
                view=None,
            )

        confirm_btn.callback = (
            do_confirm
        )

        cancel_btn.callback = (
            do_cancel
        )

        confirm_view.add_item(
            confirm_btn
        )

        confirm_view.add_item(
            cancel_btn
        )

        await interaction.response.send_message(
            (
                "This will delete the posted "
                "webhook embed and stored "
                f"config for **{clan}**. "
                "Continue?"
            ),
            view=confirm_view,
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /nexrules
    # ------------------------------------------------------------------

    nexrules = app_commands.Group(
        name="nexrules",
        description=(
            "Manage the clan rules "
            "index embed"
        ),
    )

    @nexrules.command(
        name="embed",
        description=(
            "Create or edit the clan "
            "rules index embed"
        ),
    )
    async def nexrules_embed(
        self,
        interaction: discord.Interaction,
    ):

        panel = IndexPanelView(
            self.store
        )

        config = (
            self.store.get_index_embed()
        )

        panel.refresh_btn.disabled = (
            not config.is_posted
        )

        embed = build_index_embed(
            self.store.all_clans()
        )

        location = "not set"

        if config.channel_id:

            location = (
                f"<#{config.thread_id}>"
                if config.thread_id
                else f"<#{config.channel_id}>"
            )

        status = (
            "posted"
            if config.is_posted
            else "not yet posted"
        )

        await interaction.response.send_message(
            content=(
                f"**Index embed** — "
                f"{status} · "
                f"location: {location}"
            ),
            embed=embed,
            view=panel,
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(
    bot: commands.Bot,
):

    await bot.add_cog(
        ClanEmbeds(bot)
    )