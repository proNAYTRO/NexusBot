"""/nexemoji — [Admin] browse every application emoji currently uploaded.

Groups utils/emojis.py's EMOJI_IDS by category and renders one paginated
Components V2 layout per category, so a re-upload/rename can be sanity-checked
visually without digging through the dict or the Developer Portal.
"""

import discord
from discord import app_commands
from discord.ext import commands

from cogs.cwl.capture import ADMIN_ID
from utils.emojis import EMOJI_IDS, emoji

NEXUS_RED = 0xE8173A
_HEROES = {"BK", "AQ", "GW", "RC", "DD", "MP"}
_LEAGUE_PREFIXES = {"Bronze", "Silver", "Gold", "Crystal", "Master", "Champ", "Titan", "Legend"}
_CATEGORY_ORDER = ["nex_* UI Pack", "Heroes", "Town Hall Levels", "War League Ranks", "Misc / Branding"]
_ROW_SIZE = 3


def _categorize(name: str) -> str:
    if name.startswith("nex_"):
        return "nex_* UI Pack"
    if name in _HEROES:
        return "Heroes"
    if name.startswith("TH"):
        return "Town Hall Levels"
    if name.split("_")[0] in _LEAGUE_PREFIXES:
        return "War League Ranks"
    return "Misc / Branding"


def _build_pages() -> list[tuple[str, list[str]]]:
    """One (category, sorted_names) tuple per non-empty category, in display order."""
    categories: dict[str, list[str]] = {}
    for name in EMOJI_IDS:
        categories.setdefault(_categorize(name), []).append(name)

    pages = []
    for cat in _CATEGORY_ORDER:
        names = sorted(categories.get(cat, []))
        if names:
            pages.append((cat, names))
    return pages


class EmojiPagerLayout(discord.ui.LayoutView):
    def __init__(self, pages: list[tuple[str, list[str]]], author_id: int):
        super().__init__(timeout=180)
        self.pages = pages
        self.idx = 0
        self.author_id = author_id
        self.total = sum(len(names) for _, names in pages)

        self.container = discord.ui.Container(accent_color=NEXUS_RED)
        self.add_item(self.container)

        self.first_btn = discord.ui.Button(emoji=emoji("nex_first"), style=discord.ButtonStyle.secondary)
        self.prev_btn = discord.ui.Button(emoji=emoji("nex_prev"), style=discord.ButtonStyle.secondary)
        self.next_btn = discord.ui.Button(emoji=emoji("nex_next"), style=discord.ButtonStyle.secondary)
        self.last_btn = discord.ui.Button(emoji=emoji("nex_last"), style=discord.ButtonStyle.secondary)

        self.first_btn.callback = self._go(lambda: 0)
        self.prev_btn.callback = self._go(lambda: self.idx - 1)
        self.next_btn.callback = self._go(lambda: self.idx + 1)
        self.last_btn.callback = self._go(lambda: len(self.pages) - 1)

        self.nav_row = discord.ui.ActionRow(self.first_btn, self.prev_btn, self.next_btn, self.last_btn)
        self.render()

    def render(self):
        cat, names = self.pages[self.idx]
        self.first_btn.disabled = self.prev_btn.disabled = (self.idx == 0)
        self.last_btn.disabled = self.next_btn.disabled = (self.idx == len(self.pages) - 1)

        self.container.clear_items()
        self.container.add_item(discord.ui.TextDisplay(f"## {emoji('nn_nexus')} Application Emojis\n-# {cat}"))
        self.container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        rows = []
        for i in range(0, len(names), _ROW_SIZE):
            chunk = names[i : i + _ROW_SIZE]
            rows.append("   ".join(f"{emoji(n)} `{n}`" for n in chunk))
        self.container.add_item(discord.ui.TextDisplay("\n".join(rows)))

        self.container.add_item(discord.ui.Separator())
        self.container.add_item(self.nav_row)
        self.container.add_item(discord.ui.TextDisplay(
            f"-# {len(names)} emoji(s)  •  Page {self.idx + 1}/{len(self.pages)}  •  {self.total} total"
        ))

    def _go(self, target_fn):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(f"{emoji('nex_error')} This menu isn't for you!", ephemeral=True)
                return
            self.idx = max(0, min(len(self.pages) - 1, target_fn()))
            self.render()
            await interaction.response.edit_message(view=self)
        return callback

    async def on_timeout(self):
        for b in self.nav_row.children:
            b.disabled = True


class NexEmoji(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="nexemoji", description="[Admin] Show every application emoji currently uploaded")
    async def nexemoji(self, interaction: discord.Interaction):
        if interaction.user.id != ADMIN_ID:
            await interaction.response.send_message(
                f"{emoji('nex_error')} You don't have permission to use this command.", ephemeral=True
            )
            return

        pages = _build_pages()
        if not pages:
            await interaction.response.send_message(f"{emoji('nex_warning')} No application emojis found.", ephemeral=True)
            return

        view = EmojiPagerLayout(pages, interaction.user.id)
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(NexEmoji(bot))
