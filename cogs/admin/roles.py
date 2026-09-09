"""Server-management commands: copy a role's channel permission overwrite to another role."""

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.cwl.capture import ADMIN_ID
from utils.emojis import emoji


class RolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="nexcopyperms",
        description="[Admin] Copy one role's permission overwrites in a channel to another role",
    )
    @app_commands.describe(
        channel="Channel (or category) whose permissions to copy FROM",
        source="Role to copy permissions FROM",
        target="Role to apply the copied permissions TO",
        destination_channel="Optional: apply the copied permissions to a different channel instead",
    )
    async def nexcopyperms(
        self,
        interaction: discord.Interaction,
        channel: discord.abc.GuildChannel,
        source: discord.Role,
        target: discord.Role,
        destination_channel: Optional[discord.abc.GuildChannel] = None,
    ) -> None:
        if interaction.user.id != ADMIN_ID:
            await interaction.response.send_message(
                f"{emoji('nex_error')} You don't have permission to use this command.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        write_channel = destination_channel or channel

        if source == target and write_channel == channel:
            await interaction.followup.send(
                f"{emoji('nex_error')} Source and target are the same role — nothing to copy.",
                ephemeral=True,
            )
            return

        if source not in channel.overwrites:
            await interaction.followup.send(
                f"{emoji('nex_warning')} {source.mention} has no permissions set in "
                f"{channel.mention} — nothing copied.",
                ephemeral=True,
            )
            return

        me = interaction.guild.me
        if not write_channel.permissions_for(me).manage_roles:
            await interaction.followup.send(
                f"{emoji('nex_error')} I need the **Manage Roles** permission in "
                f"{write_channel.mention} to edit its permission overwrites.",
                ephemeral=True,
            )
            return

        overwrite = channel.overwrites_for(source)
        had_existing = target in write_channel.overwrites

        try:
            await write_channel.set_permissions(
                target,
                overwrite=overwrite,
                reason=(
                    f"/nexcopyperms by {interaction.user} "
                    f"(copied from @{source.name} in #{channel.name})"
                ),
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"{emoji('nex_error')} Discord refused the edit — make sure my role is high "
                f"enough and has **Manage Roles** in {write_channel.mention}.",
                ephemeral=True,
            )
            return

        n_allow = sum(1 for _, value in overwrite if value is True)
        n_deny = sum(1 for _, value in overwrite if value is False)
        lines = [
            f"{emoji('nex_success')} Copied {source.mention}'s permissions from {channel.mention} to "
            f"{target.mention} in {write_channel.mention} — {n_allow} allowed, {n_deny} denied."
        ]
        if had_existing:
            lines.append(f"-# Replaced {target.name}'s existing settings in {write_channel.mention}.")
        await interaction.followup.send("\n".join(lines), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RolesCog(bot))