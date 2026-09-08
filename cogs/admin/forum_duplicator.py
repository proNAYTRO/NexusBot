"""
Forum Duplicator cog for discord.py (2.4+ recommended, needs Forum Channels support).

Slash command:
    /duplicate_forum source:<forum channel> duplicate_permissions:<True/False> new_name:<optional>

What it does:
    - Creates a brand-new forum channel in the same category as `source`.
    - Copies forum-level settings: topic, nsfw, slowmode, default sort/layout,
      default reaction emoji, and all available tags (name + emoji + moderated flag).
    - Optionally copies `source`'s permission overwrites onto the new channel
      (same roles, not new ones) if duplicate_permissions=True.
    - Recreates every thread (active + archived) as a new forum post with the
      same name, same starter text, same image/file attachments, and the same
      tags applied. Replies inside each thread are NOT copied, only the
      starter post.

Usage:
    Put this file in your cogs folder and load it, e.g.:
        await bot.load_extension("cogs.forum_duplicator")

    Requires the bot to have Manage Channels permission in the guild, and the
    "Server Members"/message content intents are not needed for this cog.
"""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands


class ForumDuplicator(commands.Cog):
    """Duplicates a forum channel: settings, tags, and each thread's starter post."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="duplicate_forum",
        description="Duplicate a forum channel (posts, tags, optionally permissions) into a new forum channel.",
    )
    @app_commands.describe(
        source="The forum channel to duplicate",
        duplicate_permissions="Also copy the channel's permission overwrites (same roles)",
        new_name="Optional name for the new forum (defaults to '<original> (Copy)')",
    )
    @app_commands.checks.has_permissions(manage_channels=True)
    async def duplicate_forum(
        self,
        interaction: discord.Interaction,
        source: discord.ForumChannel,
        duplicate_permissions: bool = False,
        new_name: str = None,
    ):
        if interaction.guild is None:
            await interaction.response.send_message("This only works inside a server.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        guild = interaction.guild
        target_name = new_name or f"{source.name} (Copy)"

        # Tags must exist on the new forum before threads can use them.
        new_tags = [
            discord.ForumTag(name=tag.name, emoji=tag.emoji, moderated=tag.moderated)
            for tag in source.available_tags
        ]

        overwrites = source.overwrites if duplicate_permissions else None

        try:
            new_forum = await guild.create_forum(
                name=target_name,
                category=source.category,
                topic=source.topic,
                nsfw=source.nsfw,
                slowmode_delay=source.slowmode_delay,
                default_auto_archive_duration=source.default_auto_archive_duration,
                default_thread_slowmode_delay=source.default_thread_slowmode_delay,
                default_sort_order=source.default_sort_order,
                default_layout=source.default_layout,
                default_reaction_emoji=source.default_reaction_emoji,
                available_tags=new_tags,
                overwrites=overwrites,
                reason=f"Duplicated from #{source.name} by {interaction.user}",
            )
        except discord.HTTPException as e:
            await interaction.followup.send(f"Couldn't create the new forum: {e}")
            return

        # Map old tag id -> new tag object (list order is preserved on creation).
        tag_map = {old.id: new for old, new in zip(source.available_tags, new_forum.available_tags)}

        # Gather every thread: active ones plus archived ones.
        threads = list(source.threads)
        async for archived in source.archived_threads(limit=None):
            threads.append(archived)

        created, failed = 0, 0

        for thread in threads:
            starter = thread.starter_message
            if starter is None:
                try:
                    starter = await thread.fetch_message(thread.id)
                except (discord.NotFound, discord.HTTPException):
                    starter = None

            content = starter.content if starter and starter.content else "\u200b"

            files = []
            if starter:
                for att in starter.attachments:
                    try:
                        files.append(await att.to_file())
                    except discord.HTTPException:
                        pass

            applied_tags = [tag_map[t.id] for t in thread.applied_tags if t.id in tag_map]

            try:
                await new_forum.create_thread(
                    name=thread.name,
                    content=content,
                    files=files if files else discord.utils.MISSING,
                    applied_tags=applied_tags,
                )
                created += 1
            except discord.HTTPException:
                failed += 1

            await asyncio.sleep(1)  # go easy on rate limits for large forums

        summary = f"Done! Created {new_forum.mention} with {created} post(s) copied"
        if failed:
            summary += f", {failed} failed"
        summary += "."
        await interaction.followup.send(summary)

    @duplicate_forum.error
    async def duplicate_forum_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("You need Manage Channels to do that.", ephemeral=True)
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(ForumDuplicator(bot))
