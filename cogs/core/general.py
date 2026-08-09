"""General-purpose utility commands every bot is expected to have —
/nexping, /nexbotinfo, /nexserverinfo, /nexuserinfo, /nexavatar.

No moderation commands (kick/ban/mute) on purpose — the family runs those
through other bots. Everything here is open to all users and read-only.

Rendered with Components V2 (LayoutView + Container), same conventions as
nexhelp.py: NEXUS_RED accent.
"""

import platform
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.emojis import emoji

NEXUS_RED = 0xE8173A


def _panel(title: str, body: str, *, thumbnail: str | None = None) -> discord.ui.LayoutView:
    """One-container layout: title + body text, optional thumbnail accessory."""
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_color=NEXUS_RED)
    if thumbnail:
        container.add_item(discord.ui.Section(
            discord.ui.TextDisplay(f"## {emoji('nn_nexus')} {title}\n{body}"),
            accessory=discord.ui.Thumbnail(media=thumbnail),
        ))
    else:
        container.add_item(discord.ui.TextDisplay(f"## {emoji('nn_nexus')} {title}\n{body}"))
    view.add_item(container)
    return view


class General(commands.Cog):
    """Basic info/utility slash commands."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.started_at = datetime.now(timezone.utc)

    # ── /nexping ─────────────────────────────────────────────────────────────

    @app_commands.command(name="nexping", description="Bot latency and uptime")
    async def nexping(self, interaction: discord.Interaction):
        t0 = time.perf_counter()
        await interaction.response.defer()
        rest_ms = (time.perf_counter() - t0) * 1000
        ws_ms = self.bot.latency * 1000
        body = (
            f"{emoji('nex_ping')} **Gateway:** {ws_ms:.0f} ms\n"
            f"{emoji('nex_refresh')} **REST round-trip:** {rest_ms:.0f} ms\n"
            f"{emoji('nex_online')} **Online since:** {discord.utils.format_dt(self.started_at, 'R')}"
        )
        await interaction.followup.send(view=_panel("Pong!", body))

    # ── /nexbotinfo ──────────────────────────────────────────────────────────

    @app_commands.command(name="nexbotinfo", description="About NAYTRO-NEXUS: uptime, servers, versions")
    async def nexbotinfo(self, interaction: discord.Interaction):
        members = sum(g.member_count or 0 for g in self.bot.guilds)
        body = (
            f"{emoji('nex_home')} **Servers:** {len(self.bot.guilds)}\n"
            f"{emoji('nex_member')} **Members reached:** {members:,}\n"
            f"{emoji('nex_online')} **Online since:** {discord.utils.format_dt(self.started_at, 'R')}\n"
            f"{emoji('nex_settings')} **discord.py:** {discord.__version__} · **Python:** {platform.python_version()}\n"
            f"{emoji('nex_info')} Serving the Krewe family of Clash of Clans clans — try `/nexhelp`"
        )
        thumb = self.bot.user.display_avatar.url if self.bot.user else None
        await interaction.response.send_message(
            view=_panel(f"{emoji('nn_nexus')} NAYTRO-NEXUS", body, thumbnail=thumb)
        )

    # ── /nexserverinfo ───────────────────────────────────────────────────────

    @app_commands.command(name="nexserverinfo", description="Stats for this server")
    async def nexserverinfo(self, interaction: discord.Interaction):
        g = interaction.guild
        if g is None:
            await interaction.response.send_message("This command only works in a server.", ephemeral=True)
            return
        text_ch = len(g.text_channels)
        voice_ch = len(g.voice_channels)
        body = (
            f"{emoji('nex_member')} **Members:** {g.member_count:,}\n"
            f"{emoji('nex_settings')} **Owner:** <@{g.owner_id}>\n"
            f"{emoji('nex_info')} **Created:** {discord.utils.format_dt(g.created_at, 'D')} "
            f"({discord.utils.format_dt(g.created_at, 'R')})\n"
            f"{emoji('nex_menu')} **Channels:** {text_ch} text · {voice_ch} voice\n"
            f"{emoji('nex_branch')} **Roles:** {len(g.roles)} · **Emojis:** {len(g.emojis)}\n"
            f"{emoji('nex_boost')} **Boosts:** {g.premium_subscription_count} (level {g.premium_tier})"
        )
        await interaction.response.send_message(
            view=_panel(g.name, body, thumbnail=g.icon.url if g.icon else None)
        )

    # ── /nexuserinfo ─────────────────────────────────────────────────────────

    @app_commands.command(name="nexuserinfo", description="Info about a member (or yourself)")
    @app_commands.describe(user="Whose info to show — defaults to you")
    async def nexuserinfo(self, interaction: discord.Interaction, user: discord.Member | None = None):
        member = user or interaction.user
        lines = [
            f"{emoji('nex_member')} **Username:** {member.name} · **ID:** `{member.id}`",
            f"{emoji('nex_info')} **Account created:** {discord.utils.format_dt(member.created_at, 'D')} "
            f"({discord.utils.format_dt(member.created_at, 'R')})",
        ]
        if isinstance(member, discord.Member):
            if member.joined_at:
                lines.append(
                    f"{emoji('nex_home')} **Joined server:** {discord.utils.format_dt(member.joined_at, 'D')} "
                    f"({discord.utils.format_dt(member.joined_at, 'R')})"
                )
            roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
            if roles:
                shown = roles[:10]
                more = f" +{len(roles) - 10} more" if len(roles) > 10 else ""
                lines.append(f"{emoji('nex_branch')} **Roles ({len(roles)}):** {' '.join(shown)}{more}")
        await interaction.response.send_message(
            view=_panel(member.display_name, "\n".join(lines), thumbnail=member.display_avatar.url)
        )

    # ── /nexavatar ───────────────────────────────────────────────────────────

    @app_commands.command(name="nexavatar", description="Show a member's avatar full-size")
    @app_commands.describe(user="Whose avatar to show — defaults to you")
    async def nexavatar(self, interaction: discord.Interaction, user: discord.Member | None = None):
        member = user or interaction.user
        view = discord.ui.LayoutView(timeout=None)
        container = discord.ui.Container(accent_color=NEXUS_RED)
        container.add_item(discord.ui.TextDisplay(f"## {emoji('nn_nexus')} {member.display_name}'s avatar"))
        gallery = discord.ui.MediaGallery()
        gallery.add_item(media=member.display_avatar.with_size(1024).url)
        container.add_item(gallery)
        view.add_item(container)
        await interaction.response.send_message(view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(General(bot))
