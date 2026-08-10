"""
Nexdle trading system -- discord.py cog.

Commands:
/trade post              open the have/want menu and post an offer
/trade cancel            cancel one or more of your own offers
/trade notify <on/off>   DM you when someone matches your offer (off by default)
/trade setup <ID>        (admin) pick a channel OR thread for the live trade board

The setup command accepts a Discord channel ID or thread ID.

Files required:
- trading_cog.py
- _troop_data.py
- _trade_store.py
- _troop_emojis.py

Load with:

    await bot.load_extension("trading_cog")
"""

import itertools

import discord
from discord import app_commands
from discord.ext import commands

from ._troop_data import CATEGORIES, TROOPS, ANY_TROOP
from ._trade_store import TradeStore
from ._troop_emojis import troop_emoji_str, troop_emoji_partial


BOARD_COLOR = 0xF0B232


# ---------------------------------------------------------------------------
# Post-a-trade flow
# category select -> have select -> want select -> post
# ---------------------------------------------------------------------------


class CategorySelect(discord.ui.Select):
    def __init__(self, flow, current=None):
        options = [
            discord.SelectOption(
                label=label,
                value=key,
                default=(key == current),
            )
            for key, label in CATEGORIES.items()
        ]

        super().__init__(
            placeholder="Category",
            options=options,
            row=0,
        )

        self.flow = flow

    async def callback(self, interaction: discord.Interaction):
        if self.flow.category != self.values[0]:
            # Category changed -> troop picks are no longer valid
            self.flow.have = []
            self.flow.want = []

        self.flow.category = self.values[0]

        await self.flow.refresh(interaction)


class TroopSelect(discord.ui.Select):
    """
    Multi-select troop picker.

    Lets the user pick several troops at once for "have" or "want"
    instead of posting one trade at a time. self.flow.have / .want
    are lists now -- PostButton turns them into one trade per
    have/want pairing.
    """

    def __init__(
        self,
        flow,
        field,
        troops,
        placeholder,
        row,
        current=None,
    ):
        current = current or []

        options = [
            discord.SelectOption(
                label=t,
                value=t,
                default=(t in current),
                emoji=troop_emoji_partial(t),
            )
            for t in troops[:25]
        ]

        super().__init__(
            placeholder=placeholder,
            options=options,
            min_values=1,
            max_values=len(options),
            row=row,
        )

        self.flow = flow
        self.field = field

    async def callback(self, interaction: discord.Interaction):
        setattr(self.flow, self.field, list(self.values))

        await self.flow.refresh(interaction)


MAX_TRADES_PER_POST = 25  # sanity cap so one click can't spam the board


class PostButton(discord.ui.Button):
    def __init__(self, flow, disabled):
        super().__init__(
            label="Post trade",
            style=discord.ButtonStyle.primary,
            row=3,
            disabled=disabled,
        )

        self.flow = flow

    async def callback(self, interaction: discord.Interaction):
        # -------------------------------------------------------------
        # One trade per (have, want) pairing, skipping same-troop pairs
        # -------------------------------------------------------------

        pairs = [
            (h, w)
            for h, w in itertools.product(self.flow.have, self.flow.want)
            if h != w
        ]

        if not pairs:
            await interaction.response.send_message(
                "Have and want can't be the same troop.",
                ephemeral=True,
            )
            return

        if len(pairs) > MAX_TRADES_PER_POST:
            await interaction.response.send_message(
                f"That's {len(pairs)} trades at once -- please select "
                f"fewer troops (max {MAX_TRADES_PER_POST} combinations "
                "per post).",
                ephemeral=True,
            )
            return

        cog = self.flow.cog

        posted = []

        for have, want in pairs:
            trade = await cog.store.add_trade(
                interaction.guild_id,
                interaction.user.id,
                self.flow.category,
                have,
                1,
                want,
                1,
            )

            posted.append(trade)

        lines = [
            f"**{troop_emoji_str(t['have'])} {t['have']}** → "
            f"**{troop_emoji_str(t['want'])} {t['want']}**"
            for t in posted
        ]

        summary = "\n".join(lines)

        await interaction.response.send_message(
            f"Posted {len(posted)} trade(s):\n{summary}",
            ephemeral=True,
        )

        await cog.update_board(interaction.guild)

        for trade in posted:
            await cog.check_matches(interaction.guild, trade)


class TradeFlowView(discord.ui.View):
    def __init__(self, cog, author: discord.abc.User):
        super().__init__(timeout=180)

        self.cog = cog
        self.author = author

        self.category = None
        self.have = []
        self.want = []

        self.rebuild_items()

    async def interaction_check(
        self,
        interaction: discord.Interaction,
    ) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message(
                "This isn't your trade menu.",
                ephemeral=True,
            )
            return False

        return True

    def embed(self):
        e = discord.Embed(
            title="Post a trade",
            color=BOARD_COLOR,
        )

        e.add_field(
            name="Category",
            value=CATEGORIES.get(self.category, "—"),
            inline=False,
        )

        def troop_list_display(troops):
            if not troops:
                return "—"

            return "\n".join(
                f"{troop_emoji_str(t)} {t}".strip() for t in troops
            )

        e.add_field(
            name="I have",
            value=troop_list_display(self.have),
        )

        e.add_field(
            name="I want",
            value=troop_list_display(self.want),
        )

        pair_count = len(
            [(h, w) for h, w in itertools.product(self.have, self.want) if h != w]
        )

        e.set_footer(
            text=(
                "Pick a category, then select multiple troops for "
                "have/want if you like -- one trade is posted per "
                f"combination ({pair_count} trade(s) will be posted)."
                if pair_count
                else "Pick a category, then what you have and want. "
                "You can select more than one of each."
            )
        )

        return e

    def rebuild_items(self):
        self.clear_items()

        self.add_item(
            CategorySelect(
                self,
                current=self.category,
            )
        )

        if self.category:
            troops = TROOPS[self.category]

            self.add_item(
                TroopSelect(
                    self,
                    "have",
                    troops,
                    "I have...",
                    row=1,
                    current=self.have,
                )
            )

            self.add_item(
                TroopSelect(
                    self,
                    "want",
                    [ANY_TROOP] + troops,
                    "I want...",
                    row=2,
                    current=self.want,
                )
            )

        complete = bool(
            self.category
            and self.have
            and self.want
            and any(h != w for h in self.have for w in self.want)
        )

        self.add_item(
            PostButton(
                self,
                disabled=not complete,
            )
        )

    async def refresh(self, interaction: discord.Interaction):
        self.rebuild_items()

        await interaction.response.edit_message(
            embed=self.embed(),
            view=self,
        )


# ---------------------------------------------------------------------------
# Cancel flow
# ---------------------------------------------------------------------------


class CancelSelect(discord.ui.Select):
    def __init__(self, cog, trades):
        options = [
            discord.SelectOption(
                label=f"{t['have']} → {t['want']}"[:100],
                value=t["id"],
                emoji=troop_emoji_partial(t["have"]),
            )
            for t in trades[:25]
        ]

        super().__init__(
            placeholder="Pick trades to cancel",
            options=options,
            min_values=1,
            max_values=len(options),
        )

        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        await self.cog.store.remove_trades(
            interaction.guild_id,
            set(self.values),
        )

        await self.cog.update_board(interaction.guild)

        await interaction.response.edit_message(
            content=f"Cancelled {len(self.values)} trade(s).",
            view=None,
        )


class CancelView(discord.ui.View):
    def __init__(self, cog, trades):
        super().__init__(timeout=120)

        self.add_item(
            CancelSelect(
                cog,
                trades,
            )
        )


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class TradingCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = TradeStore()

    # -----------------------------------------------------------------------
    # /trade post
    # -----------------------------------------------------------------------

    trade = app_commands.Group(
        name="trade",
        description="Clash of Clans card trading",
    )

    @trade.command(
        name="post",
        description="Post a new trade offer",
    )
    async def trade_post(
        self,
        interaction: discord.Interaction,
    ):
        view = TradeFlowView(
            self,
            interaction.user,
        )

        await interaction.response.send_message(
            embed=view.embed(),
            view=view,
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /trade cancel
    # -----------------------------------------------------------------------

    @trade.command(
        name="cancel",
        description="Cancel one or more of your active trades",
    )
    async def trade_cancel(
        self,
        interaction: discord.Interaction,
    ):
        trades = self.store.get_user_trades(
            interaction.guild_id,
            interaction.user.id,
        )

        if not trades:
            await interaction.response.send_message(
                "You don't have any active trades.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "Pick which trades to cancel:",
            view=CancelView(
                self,
                trades,
            ),
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /trade notify
    # -----------------------------------------------------------------------

    @trade.command(
        name="notify",
        description="DM you when a match for your trade shows up",
    )
    @app_commands.describe(
        on="Turn DM notifications on or off",
    )
    async def trade_notify(
        self,
        interaction: discord.Interaction,
        on: bool,
    ):
        await self.store.set_notify(
            interaction.guild_id,
            interaction.user.id,
            on,
        )

        await interaction.response.send_message(
            f"Trade match DMs are now **{'on' if on else 'off'}**.",
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # /trade setup
    #
    # IMPORTANT:
    # This accepts a Discord ID as text so it can accept BOTH:
    #
    # - Text channel IDs
    # - Thread IDs
    #
    # Example:
    # /trade setup 123456789012345678
    # -----------------------------------------------------------------------

    @trade.command(
        name="setup",
        description="(admin) Set the channel or thread for the live trade board",
    )
    @app_commands.checks.has_permissions(
        manage_guild=True,
    )
    @app_commands.describe(
        destination="ID of the text channel or thread",
    )
    async def trade_setup(
        self,
        interaction: discord.Interaction,
        destination: str,
    ):
        # ---------------------------------------------------------------
        # Convert supplied ID into an integer
        # ---------------------------------------------------------------

        try:
            destination_id = int(destination)

        except ValueError:
            await interaction.response.send_message(
                "❌ Please provide a valid Discord channel or thread ID.",
                ephemeral=True,
            )
            return

        # ---------------------------------------------------------------
        # Try cache first
        # ---------------------------------------------------------------

        channel = interaction.guild.get_channel(
            destination_id,
        )

        # ---------------------------------------------------------------
        # If not cached, fetch it from Discord
        #
        # This is especially useful for threads.
        # ---------------------------------------------------------------

        if channel is None:
            try:
                channel = await interaction.guild.fetch_channel(
                    destination_id,
                )

            except discord.NotFound:
                await interaction.response.send_message(
                    "❌ I couldn't find a channel or thread with that ID.",
                    ephemeral=True,
                )
                return

            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ I don't have permission to access that channel or thread.",
                    ephemeral=True,
                )
                return

            except discord.HTTPException:
                await interaction.response.send_message(
                    "❌ Discord returned an error while looking up that ID.",
                    ephemeral=True,
                )
                return

        # ---------------------------------------------------------------
        # Only allow TextChannel or Thread
        # ---------------------------------------------------------------

        if not isinstance(
            channel,
            (
                discord.TextChannel,
                discord.Thread,
            ),
        ):
            await interaction.response.send_message(
                "❌ That ID isn't a text channel or thread.",
                ephemeral=True,
            )
            return

        # ---------------------------------------------------------------
        # Create initial placeholder message
        # ---------------------------------------------------------------

        placeholder = discord.Embed(
            title="Setting up trade board...",
            color=BOARD_COLOR,
        )

        try:
            msg = await channel.send(
                embed=placeholder,
            )

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I don't have permission to send messages in that channel/thread.",
                ephemeral=True,
            )
            return

        except discord.HTTPException:
            await interaction.response.send_message(
                "❌ Discord rejected the message. Make sure the thread is active and I have permission to send messages.",
                ephemeral=True,
            )
            return

        # ---------------------------------------------------------------
        # Save board location
        #
        # The existing TradeStore can keep calling this value
        # channel_id even when it contains a thread ID.
        # ---------------------------------------------------------------

        await self.store.set_board(
            interaction.guild_id,
            channel.id,
            msg.id,
        )

        # ---------------------------------------------------------------
        # Populate the board
        # ---------------------------------------------------------------

        await self.update_board(
            interaction.guild,
        )

        # ---------------------------------------------------------------
        # Confirm setup
        # ---------------------------------------------------------------

        await interaction.response.send_message(
            f"✅ Trade board set to {channel.mention}.",
            ephemeral=True,
        )

    # -----------------------------------------------------------------------
    # Board rendering
    # -----------------------------------------------------------------------

    async def update_board(
        self,
        guild: discord.Guild,
    ):
        channel_id, message_id = await self.store.get_board(
            guild.id,
        )

        if not channel_id:
            return

        # ---------------------------------------------------------------
        # Try Discord.py cache first
        # ---------------------------------------------------------------

        channel = guild.get_channel(
            channel_id,
        )

        # ---------------------------------------------------------------
        # If not cached, fetch it.
        #
        # This allows thread IDs to work reliably.
        # ---------------------------------------------------------------

        if channel is None:
            try:
                channel = await guild.fetch_channel(
                    channel_id,
                )

            except (
                discord.NotFound,
                discord.Forbidden,
                discord.HTTPException,
            ):
                return

        # ---------------------------------------------------------------
        # Only allow TextChannel or Thread
        # ---------------------------------------------------------------

        if not isinstance(
            channel,
            (
                discord.TextChannel,
                discord.Thread,
            ),
        ):
            return

        # ---------------------------------------------------------------
        # Build embeds
        # ---------------------------------------------------------------

        embeds = []

        for cat_key, cat_label in CATEGORIES.items():
            trades = self.store.get_trades(
                guild.id,
                cat_key,
            )

            e = discord.Embed(
                title=cat_label,
                color=BOARD_COLOR,
            )

            if not trades:
                e.description = "No active trades."

            else:
                lines = []

                for t in trades[:25]:
                    member = guild.get_member(
                        t["user_id"],
                    )

                    name = (
                        member.display_name
                        if member
                        else f"<@{t['user_id']}>"
                    )

                    have_e = troop_emoji_str(
                        t["have"],
                    )

                    want_e = troop_emoji_str(
                        t["want"],
                    )

                    lines.append(
                        f"**{name}** — "
                        f"{have_e} {t['have']} → "
                        f"{want_e} {t['want']}"
                    )

                e.description = "\n".join(
                    lines,
                )

            embeds.append(e)

        # ---------------------------------------------------------------
        # Edit existing board message
        # ---------------------------------------------------------------

        try:
            message = await channel.fetch_message(
                message_id,
            )

            await message.edit(
                embeds=embeds,
            )

        # ---------------------------------------------------------------
        # If the old board message disappeared,
        # create a new one.
        # ---------------------------------------------------------------

        except (
            discord.NotFound,
            discord.HTTPException,
        ):
            try:
                message = await channel.send(
                    embeds=embeds,
                )

                await self.store.set_board(
                    guild.id,
                    channel.id,
                    message.id,
                )

            except (
                discord.Forbidden,
                discord.HTTPException,
            ):
                return

    # -----------------------------------------------------------------------
    # Matching
    # -----------------------------------------------------------------------

    async def check_matches(
        self,
        guild: discord.Guild,
        trade: dict,
    ):
        matches = self.store.find_matches(
            guild.id,
            trade,
        )

        if not matches:
            return

        poster = guild.get_member(
            trade["user_id"],
        )

        cat_label = CATEGORIES[
            trade["category"]
        ]

        for other_trade in matches:
            other = guild.get_member(
                other_trade["user_id"],
            )

            if (
                poster
                and self.store.get_notify(
                    guild.id,
                    poster.id,
                )
                and other
            ):
                await self._safe_dm(
                    poster,
                    f"Match in **{cat_label}**: "
                    f"{other.display_name} has "
                    f"{troop_emoji_str(other_trade['have'])} "
                    f"{other_trade['have']} and wants your "
                    f"{troop_emoji_str(trade['have'])} "
                    f"{trade['have']}.",
                )

            if (
                other
                and self.store.get_notify(
                    guild.id,
                    other.id,
                )
                and poster
            ):
                await self._safe_dm(
                    other,
                    f"Match in **{cat_label}**: "
                    f"{poster.display_name} posted "
                    f"{troop_emoji_str(trade['have'])} "
                    f"{trade['have']} looking for your "
                    f"{troop_emoji_str(other_trade['have'])} "
                    f"{other_trade['have']}.",
                )

    # -----------------------------------------------------------------------
    # Safe DM helper
    # -----------------------------------------------------------------------

    @staticmethod
    async def _safe_dm(
        member: discord.Member,
        content: str,
    ):
        try:
            await member.send(
                content,
            )

        except discord.Forbidden:
            pass


# ---------------------------------------------------------------------------
# Cog setup
# ---------------------------------------------------------------------------


async def setup(bot: commands.Bot):
    await bot.add_cog(
        TradingCog(bot),
    )