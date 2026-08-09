import discord
from discord.ext import commands
from discord import app_commands

from utils.emojis import emoji

NEXUS_RED = 0xE8173A

OVERVIEW = "overview"


def _categories() -> dict[str, dict]:
    """Category key -> {icon, label, blurb, body}. Built per-invocation so the
    emoji() lookups always reflect the currently loaded application emojis."""
    return {
        OVERVIEW: {
            "icon": emoji("nex_menu"),
            "label": "Overview",
            "blurb": "All categories at a glance",
            "body": "",  # rendered specially
        },
        "linking": {
            "icon": emoji("nex_verified"),
            "label": "Account Linking",
            "blurb": "Link CoC accounts to your Discord profile",
            "body": (
                "`/nexlink <tag>` — link a Clash of Clans account to your Discord profile\n"
                "`/nexunlink` — remove one of your linked accounts\n"
                "`/nexprofile` — view your linked CoC profile(s), with a dropdown to switch accounts"
            ),
        },
        "clash": {
            "icon": emoji("nex_clash"),
            "label": "Clash Tools",
            "blurb": "Clan lookups, ranked standings, battles, war stats",
            "body": (
                "`/nexclan <tag>` — look up a clan: members, CWL league, trophies (autocompletes saved clans)\n"
                "`/nexleague [tag]` — your place in this week's Ranked league group: rank, trophies, W/L "
                "(defaults to your linked accounts)\n"
                "`/nexbattles [tag]` — browse your 50 most recent battles — attacks *and* defenses — with a "
                "**Copy Army** button per battle; filter by ranked/casual and side\n"
                "`/nexstats` — attack success-rate leaderboard, ClashPerk-style "
                "(pass `user` to check someone else, or `clan` for a whole family clan's roster; "
                "filter by war type, season, TH matchup, fresh/cleanup and more)"
            ),
        },
        "reminders": {
            "icon": emoji("nex_bell"),
            "label": "Reminders & To-Dos",
            "blurb": "Reminders, repeats, and your personal to-do list",
            "body": (
                "`/nexremind <when> <text>` — set a reminder (`30m`, `2h30m`, `9pm`, `tomorrow 8am`; "
                "optional daily/weekly repeat, channel or DM)\n"
                "`/nexreminders` — list and cancel your reminders\n"
                "…or just say **remind me in 2h to use my attacks** in chat — no command needed\n"
                "`/nextodo` — your personal to-do list: check off, edit, delete, share a snapshot\n"
                "`/nextodo task:<text>` — quick-add a task (optional priority + due date: `fri`, `3d`, `2026-08-01`)"
            ),
        },
        "stealing": {
            "icon": emoji("nex_unlocked"),
            "label": "Emote & Sticker Stealing",
            "blurb": "Steal emotes and stickers into this server",
            "body": (
                "`!nteal` — reply to a message with custom emotes to steal them **all** into this server "
                "*(you and the bot both need Manage Emojis)*\n"
                "`!nayteal` — reply to a message with a **sticker** to steal it into this server "
                "*(bot needs Manage Expressions)*"
            ),
        },
        "general": {
            "icon": emoji("nex_info"),
            "label": "General",
            "blurb": "Bot, server, and member info",
            "body": (
                "`/nexping` — bot latency and uptime\n"
                "`/nexbotinfo` — about the bot: servers, uptime, versions\n"
                "`/nexserverinfo` — stats for this server\n"
                "`/nexuserinfo [user]` — account/join dates and roles for a member\n"
                "`/nexavatar [user]` — a member's avatar full-size\n"
                "`/nexhelp` — show this help"
            ),
        },
        "admin_clash": {
            "icon": emoji("nex_refresh"),
            "label": "Admin — Clash",
            "blurb": "War capture, rosters, base posts (admin only)",
            "body": (
                "`!nexcapture` — manually run a war data capture cycle now\n"
                "`!nexstatus` — show last capture time and any errors per family clan\n"
                "`/nexaddclan <tag>` — save a clan tag + name for `/nexclan` autocomplete\n"
                "`/nexroster` — recommend a CWL roster allocation from ClashPerk signup exports "
                "(`mode`, `size`, `clans`, `pine_assignment` to steer it)\n"
                "`/nexbasepost <pdf>` — turn a base-seller PDF into layout posts with **Copy Layout** buttons "
                "(private review first, then post)"
            ),
        },
        "admin_server": {
            "icon": emoji("nex_settings"),
            "label": "Admin — Server",
            "blurb": "Roles, custom messages, button panels (admin only)",
            "body": (
                "`/nexcopyperms <channel> <source> <target>` — clone one role's channel permission "
                "overwrite onto another role\n"
                "`/nexmsg create|import|list|preview|delete` — manage saved custom messages "
                "(simple form, Discohook JSON, or Components V2 export)\n"
                "`/nexpanel <name>` — post a saved message, optionally with buttons that show other saved "
                "messages (`as_name`/`as_avatar` for a custom identity)\n"
                "`/nexemoji` — browse every application emoji currently uploaded"
            ),
        },
    }


class HelpLayout(discord.ui.LayoutView):
    """Category-paged help. One Container re-rendered in place when the category
    dropdown changes — same pattern as LeagueStandingLayout's account switcher."""

    def __init__(self):
        super().__init__(timeout=300)
        self.categories = _categories()

        self.container = discord.ui.Container(accent_color=NEXUS_RED)
        self.add_item(self.container)

        self.select = discord.ui.Select(
            placeholder="📂  Pick a category...",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=cat["label"],
                    value=key,
                    description=cat["blurb"],
                    emoji=cat["icon"],
                )
                for key, cat in self.categories.items()
            ],
        )
        self.select.callback = self.on_select

        self.render(OVERVIEW)

    def render(self, key: str) -> None:
        cat = self.categories.get(key) or self.categories[OVERVIEW]
        self.container.clear_items()

        self.container.add_item(discord.ui.TextDisplay(
            f"## {emoji('nn_nexus')} NAYTRO-NEXUS\n-# {cat['icon']} {cat['label']}"
        ))
        self.container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        if key == OVERVIEW:
            lines = [
                f"**{c['icon']} {c['label']}** — {c['blurb']}"
                for k, c in self.categories.items()
                if k != OVERVIEW
            ]
            self.container.add_item(discord.ui.TextDisplay(
                "Pick a category from the dropdown below to see its commands.\n\n" + "\n".join(lines)
            ))
        else:
            self.container.add_item(discord.ui.TextDisplay(cat["body"]))

        # Keep the picked category shown as selected when the menu re-renders.
        for option in self.select.options:
            option.default = option.value == key

        self.container.add_item(discord.ui.Separator())
        self.container.add_item(discord.ui.ActionRow(self.select))

    async def on_select(self, interaction: discord.Interaction):
        self.render(self.select.values[0])
        await interaction.response.edit_message(view=self)

    async def on_timeout(self):
        self.select.disabled = True


class NexHelp(commands.Cog):
    """Slash command reference for NAYTRO-NEXUS."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="nexhelp", description="Show all NAYTRO-NEXUS commands")
    async def nexhelp(self, interaction: discord.Interaction):
        await interaction.response.send_message(view=HelpLayout())


async def setup(bot: commands.Bot):
    await bot.add_cog(NexHelp(bot))
