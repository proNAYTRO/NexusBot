import discord
from discord.ext import commands
import re
import aiohttp

from utils.emojis import emoji


class EmoteStealer(commands.Cog):
    """Steal custom emojis from messages and add them to your server."""

    def __init__(self, bot):
        self.bot = bot

    # ── Helpers ──────────────────────────────────────────────────────────────

    def parse_emojis(self, text: str) -> list[dict]:
        """
        Extract all custom emojis from a string.
        Returns a list of dicts: {animated, name, id}
        """
        pattern = r"<(a?):(\w+):(\d+)>"
        matches = re.findall(pattern, text)
        seen = set()
        emojis = []
        for animated_flag, name, emoji_id in matches:
            if emoji_id not in seen:
                seen.add(emoji_id)
                emojis.append({
                    "animated": animated_flag == "a",
                    "name": name,
                    "id": emoji_id,
                })
        return emojis

    def emoji_url(self, emoji: dict) -> str:
        ext = "gif" if emoji["animated"] else "png"
        return f"https://cdn.discordapp.com/emojis/{emoji['id']}.{ext}?size=128"

    async def fetch_image(self, url: str) -> bytes | None:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.read()
        return None

    async def add_emoji_to_guild(
        self, guild: discord.Guild, name: str, image: bytes
    ) -> discord.Emoji | None:
        try:
            return await guild.create_custom_emoji(name=name, image=image)
        except discord.Forbidden:
            return None
        except discord.HTTPException:
            return None

    # ── Command ───────────────────────────────────────────────────────────────

    @commands.command(name="nteal")
    @commands.has_permissions(manage_emojis=True)
    @commands.bot_has_permissions(manage_emojis=True)
    async def nteal(self, ctx: commands.Context):
        """
        Reply to a message containing custom emotes and run !steal.
        Steals ALL custom emotes found and adds them to this server.
        """

        # Must be used as a reply
        ref = ctx.message.reference
        if ref is None:
            await ctx.send(f"{emoji('nex_error')} Reply to a message that contains custom emotes, then run `!nteal`.")
            return

        # Resolve the replied-to message
        try:
            target = ref.resolved or await ctx.channel.fetch_message(ref.message_id)
        except (discord.NotFound, discord.HTTPException):
            await ctx.send(f"{emoji('nex_error')} Couldn't fetch that message.")
            return

        emojis = self.parse_emojis(target.content)

        if not emojis:
            await ctx.send(f"{emoji('nex_error')} No custom emotes found in that message.")
            return

        status_msg = await ctx.send(f"{emoji('nex_search')} Found **{len(emojis)}** emote(s). Stealing...")

        added, failed, skipped = [], [], []

        for em in emojis:
            # Skip if emoji already exists in this guild (by ID match in name convention)
            if any(str(e.id) == em["id"] for e in ctx.guild.emojis):
                skipped.append(em["name"])
                continue

            image = await self.fetch_image(self.emoji_url(em))
            if image is None:
                failed.append(em["name"])
                continue

            result = await self.add_emoji_to_guild(ctx.guild, em["name"], image)
            if result:
                added.append(f"<{'a' if em['animated'] else ''}:{result.name}:{result.id}>")
            else:
                failed.append(em["name"])

        # ── Build result embed ────────────────────────────────────────────────
        embed = discord.Embed(title="🎭 Emote Steal Results", color=discord.Color.green())

        if added:
            embed.add_field(
                name=f"{emoji('nex_success')} Added ({len(added)})",
                value=" ".join(added) if added else "—",
                inline=False,
            )
        if skipped:
            embed.add_field(
                name=f"{emoji('nex_info')} Already in server ({len(skipped)})",
                value=", ".join(f"`{n}`" for n in skipped),
                inline=False,
            )
        if failed:
            embed.add_field(
                name=f"{emoji('nex_error')} Failed ({len(failed)})",
                value=", ".join(f"`{n}`" for n in failed),
                inline=False,
            )

        if not added and not failed and not skipped:
            embed.description = "Nothing was processed."

        await status_msg.edit(content=None, embed=embed)

    # ── Error handler ─────────────────────────────────────────────────────────

    @nteal.error
    async def nteal_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(f"{emoji('nex_error')} You need the **Manage Emojis** permission to use this command.")
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.send(f"{emoji('nex_error')} I need the **Manage Emojis** permission to add emotes.")
        else:
            await ctx.send(f"{emoji('nex_error')} Unexpected error: `{error}`")


async def setup(bot):
    await bot.add_cog(EmoteStealer(bot))