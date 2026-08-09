"""/nexbasepost — turn a base-seller PDF into ClashPerk-style layout posts (admin).

Upload a base pack PDF; the bot extracts each base (screenshot + name/author/CC +
the real `OpenLayout` deep-link — see utils/base_pdf.py), you review and tweak them
privately, then it posts the approved ones to the current channel as an image + a
`Name | Author | CC` title + a **Copy Layout** link button (opens the base in-game).

All PDF parsing is in the cog-free utils/base_pdf.py; this cog is only the Discord
review-and-post UI. Admin-gated like the other admin commands (`ADMIN_ID`).

Built on Discord's Components V2 (`discord.ui.LayoutView` + `Container`/`TextDisplay`/
`MediaGallery`/`Separator`/`ActionRow`, discord.py >= 2.6) instead of classic
Embed+View — a message created with a `LayoutView` gets the `IS_COMPONENTS_V2` flag
set automatically, and that flag is permanent: every later edit to that same message
must also pass a `LayoutView` (never `content=`/`embed=`), which is why the paginated
review, the posted messages, and the final summary edit are each their own
`LayoutView` subclass below rather than a single embed being mutated in place.
"""

import io
import traceback

import discord
from discord import app_commands
from discord.ext import commands

from cogs.cwl.capture import ADMIN_ID
from cogs.cwl.verify import NEXUS_RED
from utils.base_pdf import extract_bases
from utils.emojis import emoji

TITLE_MAX = 256


def _short(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _make_file(cand, idx: int):
    """A fresh discord.File for a candidate (a File's stream is consumed on send,
    so never reuse one — build a new one per message)."""
    if not cand.image_bytes:
        return None, None
    ext = cand.image_ext if cand.image_ext in ("png", "jpeg", "jpg") else "png"
    fname = f"base_{idx}.{ext}"
    return discord.File(io.BytesIO(cand.image_bytes), filename=fname), fname


class PostedBaseLayout(discord.ui.LayoutView):
    """Components V2 layout for a single posted base: title, image, and a static
    Copy Layout link button. Link buttons carry no custom_id and are handled
    client-side, so they never expire or need persistence."""

    def __init__(self, c, fname: str | None):
        super().__init__(timeout=None)
        container = discord.ui.Container(accent_color=NEXUS_RED)
        container.add_item(discord.ui.TextDisplay(
            f"{emoji('nn_nexus')} {_short(c.composed_title() or 'Base layout', TITLE_MAX)}"
        ))
        if fname:
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media=f"attachment://{fname}")
            container.add_item(gallery)
        container.add_item(discord.ui.ActionRow(
            discord.ui.Button(label="Copy Layout", emoji="📋", style=discord.ButtonStyle.link, url=c.link)
        ))
        self.add_item(container)


class SummaryLayout(discord.ui.LayoutView):
    """Replaces the review message once posting finishes. The review message was
    created with a LayoutView, so its IS_COMPONENTS_V2 flag is permanent — this
    edit must stay a LayoutView too, it can't revert to plain content=."""

    def __init__(self, message: str):
        super().__init__(timeout=None)
        container = discord.ui.Container(accent_color=NEXUS_RED)
        container.add_item(discord.ui.TextDisplay(message))
        self.add_item(container)


class BaseEditModal(discord.ui.Modal, title="Edit base details"):
    """Fix a misparsed name / author / CC before posting."""

    def __init__(self, view: "BaseReviewLayout"):
        super().__init__()
        self._view = view
        c = view.current
        self.name_in = discord.ui.TextInput(
            label="Name", default=c.name, required=False, max_length=120)
        self.author_in = discord.ui.TextInput(
            label="Author / Builder", default=c.author, required=False, max_length=120)
        self.cc_in = discord.ui.TextInput(
            label="CC troops", default=c.cc, required=False, max_length=250,
            style=discord.TextStyle.paragraph)
        self.add_item(self.name_in)
        self.add_item(self.author_in)
        self.add_item(self.cc_in)

    async def on_submit(self, interaction: discord.Interaction):
        c = self._view.current
        c.name = self.name_in.value.strip()
        c.author = self.author_in.value.strip()
        c.cc = self.cc_in.value.strip()
        await self._view.refresh(interaction)


class BaseReviewLayout(discord.ui.LayoutView):
    """Ephemeral paginated review of the parsed bases. Every base defaults to
    approved; skip the bad ones, edit the misparsed ones, then post."""

    def __init__(self, candidates: list, channel, author_id: int, intro: str):
        super().__init__(timeout=900)
        self.cands = candidates
        self.approved = [True] * len(candidates)
        self.channel = channel
        self.author_id = author_id
        self.idx = 0

        self.add_item(discord.ui.TextDisplay(intro))

        self.container = discord.ui.Container(accent_color=NEXUS_RED)
        self.add_item(self.container)

        self.prev_btn = discord.ui.Button(emoji=emoji("nex_prev"), style=discord.ButtonStyle.secondary)
        self.next_btn = discord.ui.Button(emoji=emoji("nex_next"), style=discord.ButtonStyle.secondary)
        self.edit_btn = discord.ui.Button(label="Edit", emoji="✏️", style=discord.ButtonStyle.primary)
        self.toggle_btn = discord.ui.Button(style=discord.ButtonStyle.secondary)
        self.post_btn = discord.ui.Button(emoji="📮", style=discord.ButtonStyle.success)
        self.prev_btn.callback = self._prev
        self.next_btn.callback = self._next
        self.edit_btn.callback = self._edit
        self.toggle_btn.callback = self._toggle
        self.post_btn.callback = self._post

        self.nav_row = discord.ui.ActionRow(self.prev_btn, self.next_btn, self.edit_btn)
        self.action_row2 = discord.ui.ActionRow(self.toggle_btn, self.post_btn)
        self._sync()

    @property
    def current(self):
        return self.cands[self.idx]

    def _sync(self):
        n = len(self.cands)
        self.prev_btn.disabled = self.idx <= 0
        self.next_btn.disabled = self.idx >= n - 1
        if self.approved[self.idx]:
            self.toggle_btn.label, self.toggle_btn.emoji = "Skip this base", emoji("nex_close")
            self.toggle_btn.style = discord.ButtonStyle.secondary
        else:
            self.toggle_btn.label, self.toggle_btn.emoji = "Include this base", emoji("nex_success")
            self.toggle_btn.style = discord.ButtonStyle.success
        approved = sum(self.approved)
        self.post_btn.label = f"Post {approved} to channel"
        self.post_btn.disabled = approved == 0

    def render(self):
        """Rebuild the container's contents for the current candidate. Returns
        (file, fname) for the caller to attach alongside this view."""
        c = self.current
        self._sync()
        file, fname = _make_file(c, self.idx)
        status = f"{emoji('nex_success')} will post" if self.approved[self.idx] else f"{emoji('nex_close')} skipped"

        self.container.clear_items()
        self.container.add_item(discord.ui.TextDisplay(
            _short(c.composed_title() or "(no title yet — Edit to add one)", TITLE_MAX)
        ))
        fields = (
            f"**Name:** {_short(c.name or '—', 256)}\n"
            f"**Author:** {_short(c.author or '—', 256)}\n"
            f"**Status:** {status}\n"
            f"**CC troops:** {_short(c.cc or '—', 1024)}\n"
            f"**Layout link:** [OpenLayout]({c.link})"
        )
        self.container.add_item(discord.ui.TextDisplay(fields))
        if c.raw_lines:
            self.container.add_item(discord.ui.TextDisplay(
                f"**Extracted text:** {_short(' • '.join(c.raw_lines), 500)}"
            ))
        if fname:
            gallery = discord.ui.MediaGallery()
            gallery.add_item(media=f"attachment://{fname}")
            self.container.add_item(gallery)
        else:
            self.container.add_item(discord.ui.TextDisplay(
                f"{emoji('nex_warning')} No image could be extracted for this base."
            ))
        self.container.add_item(discord.ui.Separator())
        self.container.add_item(self.nav_row)
        self.container.add_item(self.action_row2)
        self.container.add_item(discord.ui.TextDisplay(f"-# Base {self.idx + 1}/{len(self.cands)}"))
        return file, fname

    async def refresh(self, interaction: discord.Interaction):
        file, fname = self.render()
        await interaction.response.edit_message(
            view=self, attachments=[file] if file else [])

    async def _guard(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This review isn't yours.", ephemeral=True)
            return False
        return True

    async def _prev(self, interaction):
        if not await self._guard(interaction):
            return
        self.idx = max(0, self.idx - 1)
        await self.refresh(interaction)

    async def _next(self, interaction):
        if not await self._guard(interaction):
            return
        self.idx = min(len(self.cands) - 1, self.idx + 1)
        await self.refresh(interaction)

    async def _edit(self, interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.send_modal(BaseEditModal(self))

    async def _toggle(self, interaction):
        if not await self._guard(interaction):
            return
        self.approved[self.idx] = not self.approved[self.idx]
        await self.refresh(interaction)

    async def _post(self, interaction):
        if not await self._guard(interaction):
            return
        await interaction.response.defer()
        posted, failed = 0, 0
        for c, ok in zip(self.cands, self.approved):
            if not ok:
                continue
            file, fname = _make_file(c, posted)
            try:
                await self.channel.send(
                    view=PostedBaseLayout(c, fname),
                    file=file if file else discord.utils.MISSING,
                )
                posted += 1
            except Exception:
                traceback.print_exc()
                failed += 1

        self.stop()
        msg = f"{emoji('nex_success')} Posted **{posted}** base(s) to {self.channel.mention}."
        if failed:
            msg += f"\n{emoji('nex_warning')} {failed} failed to post (see bot console)."
        await interaction.edit_original_response(view=SummaryLayout(msg), attachments=[])

    async def on_timeout(self):
        for row in (self.nav_row, self.action_row2):
            for b in row.children:
                b.disabled = True


class Bases(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="nexbasepost",
        description="[Admin] Post base layouts from a seller PDF (image + Copy Layout button)",
    )
    @app_commands.describe(pdf="Base-pack PDF with layout links + screenshots")
    async def nexbasepost(self, interaction: discord.Interaction, pdf: discord.Attachment):
        if interaction.user.id not in (1048729773926522981, 970651701726040074):
            await interaction.response.send_message(f"{emoji('nex_error')} Admin only.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)  # private review

        fn = (pdf.filename or "").lower()
        if not fn.endswith(".pdf") and (pdf.content_type or "") != "application/pdf":
            await interaction.followup.send(f"{emoji('nex_error')} Please upload a **PDF** file.", ephemeral=True)
            return
        try:
            data = await pdf.read()
            cands = extract_bases(data)
        except Exception as ex:
            traceback.print_exc()
            await interaction.followup.send(
                f"{emoji('nex_error')} Couldn't read that PDF: `{type(ex).__name__}: {ex}`", ephemeral=True)
            return

        if not cands:
            await interaction.followup.send(
                f"{emoji('nex_error')} No Clash **layout links** found in that PDF. It may only contain base "
                "*images* with no embedded `OpenLayout` links — those can't become Copy "
                "Layout buttons.",
                ephemeral=True,
            )
            return

        intro = f"Found **{len(cands)}** base(s) in `{pdf.filename}`. Review below, then post."
        view = BaseReviewLayout(cands, interaction.channel, interaction.user.id, intro)
        file, fname = view.render()
        await interaction.followup.send(
            view=view,
            file=file if file else discord.utils.MISSING,
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Bases(bot))
