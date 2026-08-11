"""
/emojisync — Synchronise application emojis into the dedicated
Nexus emoji-hosting server.

Destination server:
1534628041571963050

Features:
- Imports all application emojis.
- Preserves emoji names.
- Preserves animated/static status.
- Skips emojis that already exist.
- Safe to run repeatedly.
- Reports created, skipped, and failed emojis.
"""

from __future__ import annotations

import aiohttp
import discord
import asyncio

from discord import app_commands
from discord.ext import commands


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

EMOJI_SERVER_ID = 1534628041571963050


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class EmojiSync(commands.Cog):

    def __init__(
        self,
        bot: commands.Bot,
    ):
        self.bot = bot

    # -----------------------------------------------------------------------
    # /emojisync
    # -----------------------------------------------------------------------

    @app_commands.command(
        name="emojisync",
        description=(
            "Sync all application emojis to the emoji server"
        ),
    )
    async def emojisync(
        self,
        interaction: discord.Interaction,
    ):

        # ---------------------------------------------------------------
        # Owner-only
        # ---------------------------------------------------------------

        if not await self.bot.is_owner(
            interaction.user
        ):

            await interaction.response.send_message(
                "You are not allowed to use this command.",
                ephemeral=True,
            )

            return

        await interaction.response.defer(
            ephemeral=True
        )

        # ---------------------------------------------------------------
        # Find destination server
        # ---------------------------------------------------------------

        guild = self.bot.get_guild(
            EMOJI_SERVER_ID
        )

        if guild is None:

            await interaction.followup.send(
                (
                    "❌ I am not in the emoji-hosting server.\n"
                    f"Server ID: `{EMOJI_SERVER_ID}`"
                ),
                ephemeral=True,
            )

            return

        # ---------------------------------------------------------------
        # Check bot permissions
        # ---------------------------------------------------------------

        me = guild.me

        if me is None:

            try:

                me = await guild.fetch_member(
                    self.bot.user.id
                )

            except discord.HTTPException as exc:

                await interaction.followup.send(
                    (
                        "❌ Could not retrieve my member "
                        "information.\n"
                        f"`{type(exc).__name__}: {exc}`"
                    ),
                    ephemeral=True,
                )

                return

        if not me.guild_permissions.manage_emojis:

            await interaction.followup.send(
                (
                    "❌ I need **Manage Expressions** "
                    f"in **{guild.name}**."
                ),
                ephemeral=True,
            )

            return

        # ---------------------------------------------------------------
        # Fetch application emojis
        # ---------------------------------------------------------------

        try:

            application_emojis = (
                await self.bot.fetch_application_emojis()
            )

        except discord.HTTPException as exc:

            await interaction.followup.send(
                (
                    "❌ Failed to fetch application emojis.\n"
                    f"`{type(exc).__name__}: {exc}`"
                ),
                ephemeral=True,
            )

            return

        if not application_emojis:

            await interaction.followup.send(
                "⚠️ No application emojis were found.",
                ephemeral=True,
            )

            return

        # ---------------------------------------------------------------
        # Fetch existing guild emojis
        # ---------------------------------------------------------------

        try:

            guild_emojis = (
                await guild.fetch_emojis()
            )

        except discord.HTTPException as exc:

            await interaction.followup.send(
                (
                    "❌ Failed to fetch emojis from "
                    f"**{guild.name}**.\n"
                    f"`{type(exc).__name__}: {exc}`"
                ),
                ephemeral=True,
            )

            return

        existing = {
            emoji.name: emoji
            for emoji in guild_emojis
        }

        created = []
        skipped = []
        failed = []

        # ---------------------------------------------------------------
        # Download + create
        # ---------------------------------------------------------------

        timeout = aiohttp.ClientTimeout(
            total=30
        )

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            for source in application_emojis:

                name = source.name

                # -------------------------------------------------------
                # Already exists
                # -------------------------------------------------------

                if name in existing:

                    skipped.append(
                        existing[name]
                    )

                    continue

                # -------------------------------------------------------
                # Application emoji CDN
                # -------------------------------------------------------

                extension = (
                    "gif"
                    if source.animated
                    else "png"
                )

                url = (
                    "https://cdn.discordapp.com/emojis/"
                    f"{source.id}.{extension}"
                )

                # -------------------------------------------------------
                # Download
                # -------------------------------------------------------

                try:

                    async with session.get(
                        url
                    ) as response:

                        if response.status != 200:

                            failed.append(
                                (
                                    name,
                                    f"HTTP {response.status}",
                                )
                            )

                            continue

                        image = await response.read()

                except (
                    aiohttp.ClientError,
                    asyncio.TimeoutError,
                ) as exc:

                    failed.append(
                        (
                            name,
                            str(exc),
                        )
                    )

                    continue

                # -------------------------------------------------------
                # Create guild emoji
                # -------------------------------------------------------

                try:

                    new_emoji = (
                        await guild.create_custom_emoji(
                            name=name,
                            image=image,
                            reason=(
                                "Nexus application emoji "
                                "synchronisation"
                            ),
                        )
                    )

                    created.append(
                        new_emoji
                    )

                    existing[name] = new_emoji

                except discord.HTTPException as exc:

                    failed.append(
                        (
                            name,
                            (
                                f"Discord HTTP "
                                f"{exc.status}: {exc}"
                            ),
                        )
                    )

                except Exception as exc:

                    failed.append(
                        (
                            name,
                            (
                                f"{type(exc).__name__}: "
                                f"{exc}"
                            ),
                        )
                    )

        # ---------------------------------------------------------------
        # Result
        # ---------------------------------------------------------------

        lines = [
            "## Emoji Sync Complete",
            "",
            f"**Server:** {guild.name}",
            f"**Application emojis:** {len(application_emojis)}",
            "",
            f"✅ Created: **{len(created)}**",
            f"⏭️ Already existed: **{len(skipped)}**",
            f"❌ Failed: **{len(failed)}**",
        ]

        # ---------------------------------------------------------------
        # Created
        # ---------------------------------------------------------------

        if created:

            lines.extend(
                [
                    "",
                    "### Created",
                ]
            )

            for emoji in created:

                lines.append(
                    f"{emoji} "
                    f"`{emoji.name}` "
                    f"`{emoji.id}`"
                )

        # ---------------------------------------------------------------
        # Failed
        # ---------------------------------------------------------------

        if failed:

            lines.extend(
                [
                    "",
                    "### Failed",
                ]
            )

            for name, reason in failed:

                lines.append(
                    f"`{name}` — {reason}"
                )

        # ---------------------------------------------------------------
        # Send result in chunks
        # ---------------------------------------------------------------

        chunks = []
        current = ""

        for line in lines:

            if (
                current
                and len(current) + len(line) + 1 > 1900
            ):

                chunks.append(current)
                current = line

            else:

                if current:
                    current += "\n"

                current += line

        if current:
            chunks.append(current)

        for chunk in chunks:

            await interaction.followup.send(
                chunk,
                ephemeral=True,
            )


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

async def setup(
    bot: commands.Bot,
):

    await bot.add_cog(
        EmojiSync(bot)
    )