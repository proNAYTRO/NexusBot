"""
/nexclan and /nexrules — clan showcase embed management.

Flow:
  /nexclan embed  -> dropdown of clans (from clan_tags.json, status computed
                      from clan_embeds.json) -> preview + Refresh / Edit Info /
                      Set Location buttons, all on the same ephemeral message.
  /nexclan list   -> every clan with a status flag + jump link if posted.
  /nexclan remove -> confirm, then delete message + clan_embeds.json entry.
  /nexrules embed -> same pattern, single target (the index embed).

No auto-pulled trophies/level/CWL-league. Icon is a manually-set static
emoji. Links are pasted directly into the description field as markdown.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from ._clan_embed_store import (
    ClanEmbedStore,
    ClanEmbedEntry,
    IndexEmbedConfig,
    load_clan_tags,
    VALID_TIERS,
)

TIER_COLORS = {
    "competitive": discord.Color.purple(),
    "semi-competitive": discord.Color.teal(),
}
TIER_LABELS = {
    "competitive": "Competitive",
    "semi-competitive": "Semi-Competitive",
}


# ---------------------------------------------------------------------------
# Embed builders
# ---------------------------------------------------------------------------

def build_clan_embed(name: str, entry: ClanEmbedEntry) -> discord.Embed:
    title = f"{entry.emoji} {name}".strip() if entry.emoji else name
    embed = discord.Embed(
        title=title,
        description=entry.description or "*No description set.*",
        color=TIER_COLORS.get(entry.tier, discord.Color.dark_gray()),
    )
    embed.add_field(
        name=TIER_LABELS.get(entry.tier, entry.tier.title()),
        value=entry.requirements or "*No requirements set.*",
        inline=False,
    )
    embed.add_field(name="Clan tag", value=entry.tag or "—", inline=True)
    return embed


def build_index_embed(all_clans: dict[str, ClanEmbedEntry]) -> discord.Embed:
    embed = discord.Embed(
        title="Explore our clans to find the perfect fit",
        color=discord.Color.dark_purple(),
    )
    posted = {n: e for n, e in all_clans.items() if e.is_posted}

    for tier_key in VALID_TIERS:
        tier_clans = {n: e for n, e in posted.items() if e.tier == tier_key}
        if not tier_clans:
            continue
        lines = []
        for name, entry in tier_clans.items():
            emoji = f"{entry.emoji} " if entry.emoji else ""
            lines.append(f"{emoji}**{name}**\n{entry.requirements or '—'}")
        embed.add_field(
            name=TIER_LABELS[tier_key],
            value="\n\n".join(lines),
            inline=False,
        )

    if not posted:
        embed.description = "*No clans posted yet.*"

    return embed


def status_line(name: str, entry: ClanEmbedEntry | None, guild_id: int) -> str:
    if entry and entry.is_posted:
        jump = f"https://discord.com/channels/{guild_id}/{entry.channel_id}/{entry.message_id}"
        return f"● **{name}** — [jump to embed]({jump})"
    return f"○ **{name}** — not set up"


# ---------------------------------------------------------------------------
# Modal — Edit Info
# ---------------------------------------------------------------------------

class EditInfoModal(discord.ui.Modal, title="Edit clan info"):
    def __init__(self, panel: "ClanPanelView", entry: ClanEmbedEntry):
        super().__init__()
        self.panel = panel

        self.emoji_input = discord.ui.TextInput(
            label="Emoji",
            default=entry.emoji,
            required=False,
            max_length=10,
        )
        self.tier_input = discord.ui.TextInput(
            label="Tier (competitive / semi-competitive)",
            default=entry.tier,
            required=True,
            max_length=20,
        )
        self.requirements_input = discord.ui.TextInput(
            label="Requirements block",
            style=discord.TextStyle.paragraph,
            default=entry.requirements,
            required=False,
            max_length=1024,
        )
        self.description_input = discord.ui.TextInput(
            label="Description (paste links here)",
            style=discord.TextStyle.paragraph,
            default=entry.description,
            required=False,
            max_length=4000,
        )

        for item in (
            self.emoji_input,
            self.tier_input,
            self.requirements_input,
            self.description_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        tier = self.tier_input.value.strip().lower()
        if tier not in VALID_TIERS:
            await interaction.response.send_message(
                f"Tier must be one of: {', '.join(VALID_TIERS)}", ephemeral=True
            )
            return

        entry = self.panel.store.get_or_create_clan(self.panel.clan_name, self.panel.tag)
        entry.emoji = self.emoji_input.value.strip()
        entry.tier = tier
        entry.requirements = self.requirements_input.value.strip()
        entry.description = self.description_input.value.strip()
        self.panel.store.upsert_clan(self.panel.clan_name, entry)

        await self.panel.publish_or_edit(interaction, entry)


# ---------------------------------------------------------------------------
# Location picker
# ---------------------------------------------------------------------------

class LocationSelect(discord.ui.ChannelSelect):
    def __init__(self, panel: "ClanPanelView"):
        super().__init__(
            placeholder="Choose a channel or thread…",
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
            ],
            min_values=1,
            max_values=1,
        )
        self.panel = panel

    async def callback(self, interaction: discord.Interaction):
        target = self.values[0]
        entry = self.panel.store.get_or_create_clan(self.panel.clan_name, self.panel.tag)

        if target.type in (discord.ChannelType.public_thread, discord.ChannelType.private_thread):
            entry.thread_id = target.id
            entry.channel_id = target.parent_id
        else:
            entry.channel_id = target.id
            entry.thread_id = None

        # Location changed -> old message (if any) is stale, drop it so the
        # next Refresh/edit posts fresh in the new spot.
        entry.message_id = None
        self.panel.store.upsert_clan(self.panel.clan_name, entry)

        await self.panel.publish_or_edit(interaction, entry)


class LocationView(discord.ui.View):
    def __init__(self, panel: "ClanPanelView"):
        super().__init__(timeout=180)
        self.add_item(LocationSelect(panel))


# ---------------------------------------------------------------------------
# Clan panel (preview + buttons) shown after a clan is picked
# ---------------------------------------------------------------------------

class ClanPanelView(discord.ui.View):
    def __init__(self, store: ClanEmbedStore, clan_name: str, tag: str):
        super().__init__(timeout=300)
        self.store = store
        self.clan_name = clan_name
        self.tag = tag

    async def _get_target_message(self, interaction: discord.Interaction, entry: ClanEmbedEntry):
        if entry.thread_id:
            return interaction.guild.get_channel_or_thread(entry.thread_id)
        return interaction.guild.get_channel(entry.channel_id) if entry.channel_id else None

    async def publish_or_edit(self, interaction: discord.Interaction, entry: ClanEmbedEntry):
        """Posts fresh or edits in place, then refreshes the ephemeral panel."""
        embed = build_clan_embed(self.clan_name, entry)

        if entry.channel_id:
            channel = await self._get_target_message(interaction, entry)
            if channel:
                if entry.message_id:
                    try:
                        msg = await channel.fetch_message(entry.message_id)
                        await msg.edit(embed=embed)
                    except discord.NotFound:
                        msg = await channel.send(embed=embed)
                        entry.message_id = msg.id
                        self.store.upsert_clan(self.clan_name, entry)
                else:
                    msg = await channel.send(embed=embed)
                    entry.message_id = msg.id
                    self.store.upsert_clan(self.clan_name, entry)

        await self._render(interaction, entry)

    async def _render(self, interaction: discord.Interaction, entry: ClanEmbedEntry):
        embed = build_clan_embed(self.clan_name, entry)
        location = "not set" if not entry.channel_id else (
            f"<#{entry.thread_id}>" if entry.thread_id else f"<#{entry.channel_id}>"
        )
        status = "posted" if entry.is_posted else "not yet posted"
        content = f"**{self.clan_name}** — {status} · location: {location}"

        self.refresh_btn.disabled = not entry.is_posted

        if interaction.response.is_done():
            await interaction.edit_original_response(content=content, embed=embed, view=self)
        else:
            await interaction.response.edit_message(content=content, embed=embed, view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, row=0)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = self.store.get_clan(self.clan_name)
        if not entry or not entry.is_posted:
            await interaction.response.send_message("Nothing posted yet.", ephemeral=True)
            return
        await self.publish_or_edit(interaction, entry)

    @discord.ui.button(label="Edit Info", style=discord.ButtonStyle.primary, row=0)
    async def edit_info_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        entry = self.store.get_or_create_clan(self.clan_name, self.tag)
        await interaction.response.send_modal(EditInfoModal(self, entry))

    @discord.ui.button(label="Set Location", style=discord.ButtonStyle.secondary, row=0)
    async def set_location_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Pick where this embed should live:", view=LocationView(self), ephemeral=True
        )


# ---------------------------------------------------------------------------
# Clan select dropdown (entry point)
# ---------------------------------------------------------------------------

class ClanSelect(discord.ui.Select):
    def __init__(self, store: ClanEmbedStore, tags: dict[str, str]):
        self.store = store
        self.tags = tags
        options = []
        for name, tag in tags.items():
            entry = store.get_clan(name)
            live = entry is not None and entry.is_posted
            options.append(
                discord.SelectOption(
                    label=name,
                    description="● live" if live else "○ not set up",
                    value=name,
                )
            )
        super().__init__(placeholder="Choose a clan…", options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        name = self.values[0]
        entry = self.store.get_or_create_clan(name, self.tags.get(name, ""))
        panel = ClanPanelView(self.store, name, self.tags.get(name, ""))
        panel.refresh_btn.disabled = not entry.is_posted

        embed = build_clan_embed(name, entry)
        location = "not set" if not entry.channel_id else (
            f"<#{entry.thread_id}>" if entry.thread_id else f"<#{entry.channel_id}>"
        )
        status = "posted" if entry.is_posted else "not yet posted"
        await interaction.response.edit_message(
            content=f"**{name}** — {status} · location: {location}",
            embed=embed,
            view=panel,
        )


class ClanSelectView(discord.ui.View):
    def __init__(self, store: ClanEmbedStore, tags: dict[str, str]):
        super().__init__(timeout=180)
        self.add_item(ClanSelect(store, tags))


# ---------------------------------------------------------------------------
# Index embed panel (/nexrules embed)
# ---------------------------------------------------------------------------

class IndexPanelView(discord.ui.View):
    def __init__(self, store: ClanEmbedStore):
        super().__init__(timeout=300)
        self.store = store

    async def _render(self, interaction: discord.Interaction, config: IndexEmbedConfig):
        embed = build_index_embed(self.store.all_clans())
        location = "not set" if not config.channel_id else (
            f"<#{config.thread_id}>" if config.thread_id else f"<#{config.channel_id}>"
        )
        status = "posted" if config.is_posted else "not yet posted"
        content = f"**Index embed** — {status} · location: {location}"
        self.refresh_btn.disabled = not config.is_posted

        if interaction.response.is_done():
            await interaction.edit_original_response(content=content, embed=embed, view=self)
        else:
            await interaction.response.edit_message(content=content, embed=embed, view=self)

    async def publish_or_edit(self, interaction: discord.Interaction, config: IndexEmbedConfig):
        embed = build_index_embed(self.store.all_clans())
        if config.channel_id:
            channel = (
                interaction.guild.get_channel_or_thread(config.thread_id)
                if config.thread_id
                else interaction.guild.get_channel(config.channel_id)
            )
            if channel:
                if config.message_id:
                    try:
                        msg = await channel.fetch_message(config.message_id)
                        await msg.edit(embed=embed)
                    except discord.NotFound:
                        msg = await channel.send(embed=embed)
                        config.message_id = msg.id
                        self.store.set_index_embed(config)
                else:
                    msg = await channel.send(embed=embed)
                    config.message_id = msg.id
                    self.store.set_index_embed(config)
        await self._render(interaction, config)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
    async def refresh_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = self.store.get_index_embed()
        if not config.is_posted:
            await interaction.response.send_message("Nothing posted yet.", ephemeral=True)
            return
        await self.publish_or_edit(interaction, config)

    @discord.ui.button(label="Set Location", style=discord.ButtonStyle.primary)
    async def set_location_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        select = discord.ui.ChannelSelect(
            placeholder="Choose a channel or thread…",
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.public_thread,
                discord.ChannelType.private_thread,
            ],
        )

        async def on_select(inner: discord.Interaction):
            target = select.values[0]
            config = self.store.get_index_embed()
            if target.type in (discord.ChannelType.public_thread, discord.ChannelType.private_thread):
                config.thread_id = target.id
                config.channel_id = target.parent_id
            else:
                config.channel_id = target.id
                config.thread_id = None
            config.message_id = None
            self.store.set_index_embed(config)
            await self.publish_or_edit(inner, config)

        select.callback = on_select
        view = discord.ui.View(timeout=180)
        view.add_item(select)
        await interaction.response.send_message("Pick where the index embed should live:", view=view, ephemeral=True)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class ClanEmbeds(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = ClanEmbedStore()

    nexclan = app_commands.Group(name="nexclanembed", description="Manage clan showcase embeds")
    nexrules = app_commands.Group(name="nexrules", description="Manage the clan rules index embed")

    @nexclan.command(name="edit", description="Create or edit a clan's showcase embed")
    async def nexclan_embed(self, interaction: discord.Interaction):
        tags = load_clan_tags()
        if not tags:
            await interaction.response.send_message(
                "No clans found in clan_tags.json.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "Choose a clan to edit:", view=ClanSelectView(self.store, tags), ephemeral=True
        )

    @nexclan.command(name="list", description="List all clans and their embed status")
    async def nexclan_list(self, interaction: discord.Interaction):
        tags = load_clan_tags()
        if not tags:
            await interaction.response.send_message(
                "No clans found in clan_tags.json.", ephemeral=True
            )
            return
        lines = [
            status_line(name, self.store.get_clan(name), interaction.guild.id)
            for name in tags
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @nexclan.command(name="remove", description="Remove a clan's posted embed and config")
    @app_commands.describe(clan="Clan name as it appears in clan_tags.json")
    async def nexclan_remove(self, interaction: discord.Interaction, clan: str):
        entry = self.store.get_clan(clan)
        if not entry:
            await interaction.response.send_message(f"No embed config found for **{clan}**.", ephemeral=True)
            return

        confirm_view = discord.ui.View(timeout=60)
        confirm_btn = discord.ui.Button(label="Delete", style=discord.ButtonStyle.danger)
        cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.secondary)

        async def do_confirm(inner: discord.Interaction):
            if entry.is_posted:
                channel = (
                    inner.guild.get_channel_or_thread(entry.thread_id)
                    if entry.thread_id
                    else inner.guild.get_channel(entry.channel_id)
                )
                if channel:
                    try:
                        msg = await channel.fetch_message(entry.message_id)
                        await msg.delete()
                    except discord.NotFound:
                        pass
            self.store.remove_clan(clan)
            await inner.response.edit_message(content=f"Removed **{clan}**.", view=None)

        async def do_cancel(inner: discord.Interaction):
            await inner.response.edit_message(content="Cancelled.", view=None)

        confirm_btn.callback = do_confirm
        cancel_btn.callback = do_cancel
        confirm_view.add_item(confirm_btn)
        confirm_view.add_item(cancel_btn)

        await interaction.response.send_message(
            f"This will delete the posted embed and stored config for **{clan}**. Continue?",
            view=confirm_view,
            ephemeral=True,
        )

    @nexrules.command(name="embed", description="Create or edit the clan rules index embed")
    async def nexrules_embed(self, interaction: discord.Interaction):
        panel = IndexPanelView(self.store)
        config = self.store.get_index_embed()
        panel.refresh_btn.disabled = not config.is_posted
        embed = build_index_embed(self.store.all_clans())
        location = "not set" if not config.channel_id else (
            f"<#{config.thread_id}>" if config.thread_id else f"<#{config.channel_id}>"
        )
        status = "posted" if config.is_posted else "not yet posted"
        await interaction.response.send_message(
            content=f"**Index embed** — {status} · location: {location}",
            embed=embed,
            view=panel,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(ClanEmbeds(bot))