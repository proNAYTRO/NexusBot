"""
Nexdle trading system -- discord.py cog.

Commands:
  /trade post              open the have/want menu and post an offer
  /trade cancel            cancel one or more of your own offers
  /trade notify <on/off>   DM you when someone matches your offer (off by default)
  /trade setup #channel    (admin) pick the channel for the live trade board

Drop this file, troop_data.py, and trade_store.py in the same folder,
then load it like any other cog:

    await bot.load_extension("trading_cog")
"""

import discord
from discord import app_commands
from discord.ext import commands

from ._troop_data import CATEGORIES, TROOPS, ANY_TROOP
from ._trade_store import TradeStore
from ._troop_emojis import troop_emoji_str, troop_emoji_partial

BOARD_COLOR = 0xF0B232


# ---------------------------------------------------------------------------
# Post-a-trade flow: category select -> have select -> want select -> modal
# ---------------------------------------------------------------------------

class CategorySelect(discord.ui.Select):
    def __init__(self, flow, current=None):
        options = [
            discord.SelectOption(label=label, value=key, default=(key == current))
            for key, label in CATEGORIES.items()
        ]
        super().__init__(placeholder="Category", options=options, row=0)
        self.flow = flow

    async def callback(self, interaction: discord.Interaction):
        if self.flow.category != self.values[0]:
            # category changed -> troop picks no longer valid, reset them
            self.flow.have = None
            self.flow.want = None
        self.flow.category = self.values[0]
        await self.flow.refresh(interaction)


class TroopSelect(discord.ui.Select):
    def __init__(self, flow, field, troops, placeholder, row, current=None):
        options = [
            discord.SelectOption(
                label=t, value=t, default=(t == current),
                emoji=troop_emoji_partial(t),
            )
            for t in troops[:25]
        ]
        super().__init__(placeholder=placeholder, options=options, row=row)
        self.flow = flow
        self.field = field

    async def callback(self, interaction: discord.Interaction):
        setattr(self.flow, self.field, self.values[0])
        await self.flow.refresh(interaction)


class PostButton(discord.ui.Button):
    def __init__(self, flow, disabled):
        super().__init__(label="Post trade", style=discord.ButtonStyle.primary, row=3, disabled=disabled)
        self.flow = flow

    async def callback(self, interaction: discord.Interaction):
        if self.flow.have == self.flow.want:
            await interaction.response.send_message(
                "Have and want can't be the same troop.", ephemeral=True
            )
            return
        await interaction.response.send_modal(QuantityModal(self.flow))


class QuantityModal(discord.ui.Modal, title="How many?"):
    have_qty = discord.ui.TextInput(label="Quantity you have", default="1", max_length=3)
    want_qty = discord.ui.TextInput(label="Quantity you want", default="1", max_length=3)

    def __init__(self, flow):
        super().__init__()
        self.flow = flow

    async def on_submit(self, interaction: discord.Interaction):
        try:
            have_qty = int(self.have_qty.value)
            want_qty = int(self.want_qty.value)
            if have_qty < 1 or want_qty < 1:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "Quantities need to be whole numbers of 1 or more.", ephemeral=True
            )
            return

        cog = self.flow.cog
        trade = await cog.store.add_trade(
            interaction.guild_id, interaction.user.id, self.flow.category,
            self.flow.have, have_qty, self.flow.want, want_qty,
        )
        have_e = troop_emoji_str(self.flow.have)
        want_e = troop_emoji_str(self.flow.want)
        await interaction.response.send_message(
            f"Trade posted: **{have_qty}x {have_e} {self.flow.have}** → "
            f"**{want_qty}x {want_e} {self.flow.want}**",
            ephemeral=True,
        )
        await cog.update_board(interaction.guild)
        await cog.check_matches(interaction.guild, trade)


class TradeFlowView(discord.ui.View):
    def __init__(self, cog, author: discord.abc.User):
        super().__init__(timeout=180)
        self.cog = cog
        self.author = author
        self.category = None
        self.have = None
        self.want = None
        self.rebuild_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("This isn't your trade menu.", ephemeral=True)
            return False
        return True

    def embed(self):
        e = discord.Embed(title="Post a trade", color=BOARD_COLOR)
        e.add_field(name="Category", value=CATEGORIES.get(self.category, "—"), inline=False)
        have_display = f"{troop_emoji_str(self.have)} {self.have}".strip() if self.have else "—"
        want_display = f"{troop_emoji_str(self.want)} {self.want}".strip() if self.want else "—"
        e.add_field(name="I have", value=have_display)
        e.add_field(name="I want", value=want_display)
        e.set_footer(text="Pick a category, then what you have and want. Quantities come last.")
        return e

    def rebuild_items(self):
        self.clear_items()
        self.add_item(CategorySelect(self, current=self.category))
        if self.category:
            troops = TROOPS[self.category]
            self.add_item(TroopSelect(self, "have", troops, "I have...", row=1, current=self.have))
            self.add_item(TroopSelect(self, "want", [ANY_TROOP] + troops, "I want...", row=2, current=self.want))
        complete = bool(self.category and self.have and self.want)
        self.add_item(PostButton(self, disabled=not complete))

    async def refresh(self, interaction: discord.Interaction):
        self.rebuild_items()
        await interaction.response.edit_message(embed=self.embed(), view=self)


# ---------------------------------------------------------------------------
# Cancel flow
# ---------------------------------------------------------------------------

class CancelSelect(discord.ui.Select):
    def __init__(self, cog, trades):
        options = [
            discord.SelectOption(
                label=f"{t['have_qty']}x {t['have']} → {t['want_qty']}x {t['want']}"[:100],
                value=t["id"],
                emoji=troop_emoji_partial(t["have"]),
            )
            for t in trades[:25]
        ]
        super().__init__(placeholder="Pick trades to cancel", options=options,
                          min_values=1, max_values=len(options))
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        await self.cog.store.remove_trades(interaction.guild_id, set(self.values))
        await self.cog.update_board(interaction.guild)
        await interaction.response.edit_message(content=f"Cancelled {len(self.values)} trade(s).", view=None)


class CancelView(discord.ui.View):
    def __init__(self, cog, trades):
        super().__init__(timeout=120)
        self.add_item(CancelSelect(cog, trades))


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class TradingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = TradeStore()

    trade = app_commands.Group(name="trade", description="Clash of Clans card trading")

    @trade.command(name="post", description="Post a new trade offer")
    async def trade_post(self, interaction: discord.Interaction):
        view = TradeFlowView(self, interaction.user)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    @trade.command(name="cancel", description="Cancel one or more of your active trades")
    async def trade_cancel(self, interaction: discord.Interaction):
        trades = self.store.get_user_trades(interaction.guild_id, interaction.user.id)
        if not trades:
            await interaction.response.send_message("You don't have any active trades.", ephemeral=True)
            return
        await interaction.response.send_message(
            "Pick which trades to cancel:", view=CancelView(self, trades), ephemeral=True
        )

    @trade.command(name="notify", description="DM you when a match for your trade shows up")
    @app_commands.describe(on="Turn DM notifications on or off")
    async def trade_notify(self, interaction: discord.Interaction, on: bool):
        await self.store.set_notify(interaction.guild_id, interaction.user.id, on)
        await interaction.response.send_message(
            f"Trade match DMs are now **{'on' if on else 'off'}**.", ephemeral=True
        )

    @trade.command(name="setup", description="(admin) Set the channel for the live trade board")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="Channel the trade board should live in")
    async def trade_setup(self, interaction: discord.Interaction, channel: discord.TextChannel):
        placeholder = discord.Embed(title="Setting up trade board...", color=BOARD_COLOR)
        msg = await channel.send(embed=placeholder)
        await self.store.set_board(interaction.guild_id, channel.id, msg.id)
        await self.update_board(interaction.guild)
        await interaction.response.send_message(f"Trade board set to {channel.mention}.", ephemeral=True)

    # ---------- board rendering ----------

    async def update_board(self, guild: discord.Guild):
        channel_id, message_id = await self.store.get_board(guild.id)
        if not channel_id:
            return
        channel = guild.get_channel(channel_id)
        if channel is None:
            return

        embeds = []
        for cat_key, cat_label in CATEGORIES.items():
            trades = self.store.get_trades(guild.id, cat_key)
            e = discord.Embed(title=cat_label, color=BOARD_COLOR)
            if not trades:
                e.description = "No active trades."
            else:
                lines = []
                for t in trades[:25]:
                    member = guild.get_member(t["user_id"])
                    name = member.display_name if member else f"<@{t['user_id']}>"
                    have_e = troop_emoji_str(t["have"])
                    want_e = troop_emoji_str(t["want"])
                    lines.append(
                        f"**{name}** — {t['have_qty']}x {have_e} {t['have']} → "
                        f"{t['want_qty']}x {want_e} {t['want']}"
                    )
                e.description = "\n".join(lines)
            embeds.append(e)

        try:
            message = await channel.fetch_message(message_id)
            await message.edit(embeds=embeds)
        except (discord.NotFound, discord.HTTPException):
            message = await channel.send(embeds=embeds)
            await self.store.set_board(guild.id, channel.id, message.id)

    # ---------- matching ----------

    async def check_matches(self, guild: discord.Guild, trade: dict):
        matches = self.store.find_matches(guild.id, trade)
        if not matches:
            return

        poster = guild.get_member(trade["user_id"])
        cat_label = CATEGORIES[trade["category"]]

        for other_trade in matches:
            other = guild.get_member(other_trade["user_id"])

            if poster and self.store.get_notify(guild.id, poster.id) and other:
                await self._safe_dm(
                    poster,
                    f"Match in **{cat_label}**: {other.display_name} has "
                    f"{other_trade['have_qty']}x {troop_emoji_str(other_trade['have'])} "
                    f"{other_trade['have']} and wants your "
                    f"{troop_emoji_str(trade['have'])} {trade['have']}.",
                )
            if other and self.store.get_notify(guild.id, other.id) and poster:
                await self._safe_dm(
                    other,
                    f"Match in **{cat_label}**: {poster.display_name} posted "
                    f"{trade['have_qty']}x {troop_emoji_str(trade['have'])} {trade['have']} "
                    f"looking for your {troop_emoji_str(other_trade['have'])} {other_trade['have']}.",
                )

    @staticmethod
    async def _safe_dm(member: discord.Member, content: str):
        try:
            await member.send(content)
        except discord.Forbidden:
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(TradingCog(bot))