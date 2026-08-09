"""Sticker stealing: reply to a message containing a sticker with !nayteal to
clone it into this server's sticker list."""

import io

import discord
from discord.ext import commands

from utils.emojis import emoji


class StickersCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="nayteal")
    async def nayteal(self, ctx: commands.Context):
        if ctx.guild is None:
            return

        ref = ctx.message.reference
        if ref is None:
            await ctx.send(f"{emoji('nex_error')} Reply to a message that has a sticker on it.")
            return

        ref_msg = ref.resolved
        if not isinstance(ref_msg, discord.Message):
            try:
                ref_msg = await ctx.channel.fetch_message(ref.message_id)
            except discord.HTTPException:
                await ctx.send(f"{emoji('nex_error')} Couldn't fetch the replied-to message.")
                return

        if not ref_msg.stickers:
            await ctx.send(f"{emoji('nex_error')} That message doesn't have a sticker on it.")
            return

        item = ref_msg.stickers[0]

        if item.format is discord.StickerFormatType.lottie:
            await ctx.send(
                f"{emoji('nex_error')} **{item.name}** is a Lottie (official Discord) sticker — "
                "only verified servers can upload those, so it can't be stolen."
            )
            return

        me = ctx.guild.me
        if not me.guild_permissions.manage_expressions:
            await ctx.send(
                f"{emoji('nex_error')} I need the **Manage Expressions** permission to create stickers."
            )
            return

        if len(ctx.guild.stickers) >= ctx.guild.sticker_limit:
            await ctx.send(
                f"{emoji('nex_error')} No free sticker slots — this server is at "
                f"{ctx.guild.sticker_limit}/{ctx.guild.sticker_limit}."
            )
            return

        msg = await ctx.send(f"{emoji('nex_loading')} Stealing **{item.name}**...")

        # Full sticker fetch gets the related emoji/description; fall back gracefully
        # for stickers from servers the bot can't see.
        sticker_emoji = "😀"
        description = ""
        try:
            full = await item.fetch()
            if isinstance(full, discord.GuildSticker):
                sticker_emoji = full.emoji or sticker_emoji
            elif isinstance(full, discord.StandardSticker) and full.tags:
                sticker_emoji = full.tags[0]
            description = full.description or ""
        except discord.HTTPException:
            pass

        try:
            data = await item.read()
        except discord.HTTPException:
            await msg.edit(content=f"{emoji('nex_error')} Couldn't download the sticker file.")
            return

        # Sticker names must be 2-30 characters.
        name = item.name[:30]
        if len(name) < 2:
            name = f"{name}_sticker"[:30]

        file = discord.File(io.BytesIO(data), filename=f"{name}.{item.format.file_extension}")

        try:
            new_sticker = await ctx.guild.create_sticker(
                name=name,
                description=description,
                emoji=sticker_emoji,
                file=file,
                reason=f"!nayteal by {ctx.author}",
            )
        except discord.Forbidden:
            await msg.edit(
                content=f"{emoji('nex_error')} Discord refused the upload — check my **Manage Expressions** permission."
            )
            return
        except discord.HTTPException as e:
            await msg.edit(content=f"{emoji('nex_error')} Upload failed: {e.text or e}")
            return

        await msg.edit(content=f"{emoji('nex_success')} Stole **{new_sticker.name}** into this server.")
        try:
            await ctx.send(stickers=[new_sticker])
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StickersCog(bot))
