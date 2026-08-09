
"""/nexremind + /nexreminders — time-based reminders with live parse-preview.

The `when` parameter is free text, but its autocomplete parses what's typed *as
you type* and shows the resolved fire time ("✓ 2h30m → in 2h 30m"), so a user
sees whether the bot understood them before submitting. Accepted forms:

- durations: `30m`, `2h30m`, `1d12h`, `90s`, or a bare number (minutes); units
  (d/h/m/s, any spelling: `3hr`, `2 mins`, `45 seconds`) chain in any order with
  optional "and"/","/"&" — `3 hours and 2 mins`, `2 days, 4 hours` all work
- clock times, interpreted in **UTC**: `21:00`, `9pm`, `tomorrow 8:30am`
  (a past time today rolls to tomorrow automatically)

Reminders fire in the channel they were set in by default (`where: DM` opts
into a DM instead; each falls back to the other if sending fails), can repeat
daily/weekly, and persist across restarts via data/reminders.json — pending
ones are rescheduled on cog load, same asyncio-task pattern as capture.py's
end-of-war tasks. War-aware presets ("1h before war ends") are planned later.

No-command path: an on_message listener catches plain chat like
"remind me in 2h to use my war attacks" / "remind me at 9pm to donate" —
extract_nl_reminder() finds the time chunk, the rest of the sentence becomes
the reminder text, and the bot replies publicly with a random line from
CONFIRM_LINES. NL reminders are always one-shot + channel delivery; when one
fires it replies to the original message (fail_if_not_exists=False, so a
deleted message degrades to a plain send).
"""

import asyncio
import json
import os
import random
import re
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.emojis import emoji

NEXUS_RED = 0xE8173A
REMINDERS_FILE = "data/reminders.json"
MAX_PER_USER = 25
MAX_TEXT = 200
MAX_DAYS = 90
LIST_SHOWN = 10

# One duration term: a number + unit in any spelling (3h / 3hr / 3 hours / 2mins /
# 90 seconds ...). The lookahead stops a bare unit letter from eating the start of
# an ordinary word ("30 stars" is not 30 seconds).
_DUR_PART = r"\d+\s*(?:d(?:ays?)?|h(?:(?:ou)?rs?)?|m(?:in(?:ute)?s?)?|s(?:ec(?:ond)?s?)?)(?![A-Za-z])"
# Terms chain in any order with optional "and" / "," / "&" between them:
# "3 hours and 2 mins", "2 days, 4 hours", "2mins 60 seconds", "2h30m".
_DUR_SEP = r"\s*(?:,|\band\b|&)?\s*"
_DUR_FULL_RE = re.compile(rf"^\s*{_DUR_PART}(?:{_DUR_SEP}{_DUR_PART})*\s*$", re.IGNORECASE)
_DUR_TOKEN_RE = re.compile(r"(\d+)\s*([dhms])", re.IGNORECASE)
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
_BARE_MIN_RE = re.compile(r"^\s*(\d+)\s*$")
_CLOCK_RE = re.compile(
    r"^\s*(?:(tomorrow)\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*(?:utc)?\s*$",
    re.IGNORECASE,
)

# ── natural-language path ("remind me in 2h to use my attacks") ──────────────

_NL_TRIGGER_RE = re.compile(r"\bremind me\b", re.IGNORECASE)
# Time-chunk candidates searched in the text after "remind me", first parseable
# match wins. Anchored on "in"/"at"/"tomorrow" so digits inside the task text
# ("buy 30 eggs") can't be mistaken for a time.
_NL_CHUNK_RES = [
    re.compile(rf"\bin\s+({_DUR_PART}(?:{_DUR_SEP}{_DUR_PART})*)", re.IGNORECASE),
    re.compile(r"\b(tomorrow\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", re.IGNORECASE),
    # "at 5" alone is ambiguous (parse_when would read a bare number as
    # minutes), so the clock form requires a colon or an am/pm marker
    re.compile(r"\bat\s+(\d{1,2}:\d{2}\s*(?:am|pm)?|\d{1,2}\s*(?:am|pm))", re.IGNORECASE),
]
_NL_CONNECTOR_RE = re.compile(r"^\s*(?:to|that|about|,|-|—)\s*|\s*(?:to|that|about|,|-|—)\s*$", re.IGNORECASE)

CONFIRM_LINES = [
    "Bet. Poking you {when} — **{text}** " + emoji("nex_timeout"),
    "Locked in. {when} I'm blowing up your pings about **{text}**.",
    "Say less. See you {when} 🫡 — **{text}**",
    "Noted, chief. {when} it's **{text}** o'clock.",
    "Carved into the war log: **{text}**, {when}.",
    "I never forget. Unlike you 😌 **{text}** — {when}.",
    "Timer's cooking ⏳ **{text}**, {when}.",
    "On it. If you ghost me {when}, that's on you — **{text}**.",
    "Alarm armed 🧨 **{text}** goes off {when}.",
    "Your personal war clock is set: **{text}**, {when}.",
]


def extract_nl_reminder(content: str, now: datetime) -> tuple[datetime | None, str] | None:
    """Pull (fire_at, text) out of a plain chat message.

    Returns None if the message isn't a reminder request at all,
    (None, "") if it clearly is one ("remind me...") but no time parsed —
    the caller replies with a hint in that case — else (fire_at, text).
    """
    trigger = _NL_TRIGGER_RE.search(content)
    if trigger is None:
        return None
    rest = content[trigger.end():]

    for chunk_re in _NL_CHUNK_RES:
        m = chunk_re.search(rest)
        if not m:
            continue
        time_text = re.sub(r"\bat\s+", "", m.group(1))  # "tomorrow at 8am" → "tomorrow 8am"
        fire_at = parse_when(time_text, now)
        if fire_at:
            text = (rest[: m.start()] + " " + rest[m.end():]).strip()
            # peel connector words off both ends ("to", "about", ...) until stable
            while True:
                stripped = _NL_CONNECTOR_RE.sub("", text).strip()
                if stripped == text:
                    break
                text = stripped
            return fire_at, text
    return None, ""


def parse_when(text: str, now: datetime) -> datetime | None:
    """Resolve a `when` string to an aware UTC datetime, or None if unparseable.
    Clock times are UTC; a past clock time today rolls forward to tomorrow."""
    m = _BARE_MIN_RE.match(text)
    if m:
        minutes = int(m.group(1))
        return now + timedelta(minutes=minutes) if minutes > 0 else None

    if _DUR_FULL_RE.match(text):
        total = sum(int(n) * _UNIT_SECONDS[u.lower()] for n, u in _DUR_TOKEN_RE.findall(text))
        return now + timedelta(seconds=total) if total > 0 else None

    m = _CLOCK_RE.match(text)
    if m:
        tomorrow, hour_s, min_s, ampm = m.groups()
        hour, minute = int(hour_s), int(min_s or 0)
        if ampm:
            if hour < 1 or hour > 12:
                return None
            hour = hour % 12 + (12 if ampm.lower() == "pm" else 0)
        elif min_s is None:
            return None  # a bare number was already treated as minutes above
        if hour > 23 or minute > 59:
            return None
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if tomorrow:
            target += timedelta(days=1)
        elif target <= now:
            target += timedelta(days=1)
        return target

    return None


def _humanize(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    if secs < 60:
        return "less than a minute"
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    return " ".join(parts)


def _load_store() -> dict:
    if not os.path.exists(REMINDERS_FILE):
        return {"next_id": 1, "reminders": []}
    with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_store(store: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)


class ReminderListLayout(discord.ui.LayoutView):
    """Ephemeral list of the caller's reminders, a Cancel button per row."""

    def __init__(self, cog: "Reminders", user_id: int):
        super().__init__(timeout=180)
        self.cog = cog
        self.user_id = user_id
        self._build()

    def _build(self):
        self.clear_items()
        container = discord.ui.Container(accent_color=NEXUS_RED)
        mine = [r for r in self.cog.store["reminders"] if r["user_id"] == self.user_id]
        container.add_item(discord.ui.TextDisplay(f"## {emoji('nn_nexus')} Your reminders ({len(mine)})"))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
        if not mine:
            container.add_item(discord.ui.TextDisplay("Nothing scheduled. Set one with `/nexremind`."))
        for r in mine[:LIST_SHOWN]:
            fire_at = datetime.fromisoformat(r["fire_at"])
            repeat = f" · repeats {r['repeat']}" if r["repeat"] != "none" else ""
            where = "DM" if r["dm"] else f"<#{r['channel_id']}>"
            btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger)
            btn.callback = self._make_cancel(r["id"])
            container.add_item(discord.ui.Section(
                discord.ui.TextDisplay(
                    f"**{r['text']}**\n"
                    f"-# {discord.utils.format_dt(fire_at, 'R')} · in {where}{repeat}"
                ),
                accessory=btn,
            ))
        if len(mine) > LIST_SHOWN:
            container.add_item(discord.ui.TextDisplay(f"-# …and {len(mine) - LIST_SHOWN} more"))
        self.add_item(container)

    def _make_cancel(self, reminder_id: int):
        async def cancel(interaction: discord.Interaction):
            self.cog.cancel_reminder(reminder_id)
            self._build()
            await interaction.response.edit_message(view=self)
        return cancel


class Reminders(commands.Cog):
    """Personal reminders — one-shot or recurring, channel or DM."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = _load_store()
        self._tasks: dict[int, asyncio.Task] = {}
        for r in list(self.store["reminders"]):
            self._schedule(r)

    def cog_unload(self):
        for task in self._tasks.values():
            task.cancel()

    # ── scheduling ───────────────────────────────────────────────────────────

    def _schedule(self, r: dict):
        self._tasks[r["id"]] = asyncio.create_task(self._run(r))

    async def _run(self, r: dict):
        fire_at = datetime.fromisoformat(r["fire_at"])
        now = datetime.now(timezone.utc)
        late = fire_at <= now - timedelta(minutes=5)  # bot was down past the fire time
        if fire_at > now:
            await discord.utils.sleep_until(fire_at)
        await self._deliver(r, late=late)

        if r["repeat"] != "none":
            step = timedelta(days=1 if r["repeat"] == "daily" else 7)
            next_at = datetime.fromisoformat(r["fire_at"])
            now = datetime.now(timezone.utc)
            while next_at <= now:  # catch up if we were offline over several cycles
                next_at += step
            r["fire_at"] = next_at.isoformat()
            _save_store(self.store)
            self._schedule(r)
        else:
            self.store["reminders"] = [x for x in self.store["reminders"] if x["id"] != r["id"]]
            _save_store(self.store)
            self._tasks.pop(r["id"], None)

    async def _deliver(self, r: dict, *, late: bool):
        note = " *(delayed — I was offline)*" if late else ""
        repeat = f"\n-# repeats {r['repeat']} — cancel with /nexreminders" if r["repeat"] != "none" else ""
        user_line = f"{emoji('nex_timeout')} <@{r['user_id']}> Reminder:{note} **{r['text']}**{repeat}"
        dm_line = f"{emoji('nex_timeout')} Reminder:{note} **{r['text']}**{repeat}"

        channel = self.bot.get_channel(r["channel_id"])
        user = self.bot.get_user(r["user_id"])
        reference = None
        if r.get("message_id"):  # NL reminders reply to the message that set them
            reference = discord.MessageReference(
                message_id=r["message_id"], channel_id=r["channel_id"], fail_if_not_exists=False,
            )
        primary, fallback = (
            (self._send_dm(user, dm_line), self._send_channel(channel, user_line, reference))
            if r["dm"] else
            (self._send_channel(channel, user_line, reference), self._send_dm(user, dm_line))
        )
        if not await primary:
            await fallback

    @staticmethod
    async def _send_channel(channel, content: str, reference=None) -> bool:
        if channel is None:
            return False
        try:
            await channel.send(content, reference=reference)
            return True
        except discord.HTTPException:
            return False

    @staticmethod
    async def _send_dm(user, content: str) -> bool:
        if user is None:
            return False
        try:
            await user.send(content)
            return True
        except discord.HTTPException:
            return False

    def cancel_reminder(self, reminder_id: int):
        task = self._tasks.pop(reminder_id, None)
        if task:
            task.cancel()
        self.store["reminders"] = [x for x in self.store["reminders"] if x["id"] != reminder_id]
        _save_store(self.store)

    # ── natural-language listener ────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.content.startswith("!"):
            return
        now = datetime.now(timezone.utc)
        result = extract_nl_reminder(message.content, now)
        if result is None:
            return
        fire_at, text = result
        if fire_at is None:
            await message.reply(
                "I heard `remind me` but couldn't find a time — try "
                "`remind me in 30m to …`, `remind me at 9pm to …`, or `/nexremind`.",
                mention_author=False,
            )
            return
        if fire_at - now > timedelta(days=MAX_DAYS):
            await message.reply(f"That's too far out — reminders max out at {MAX_DAYS} days.", mention_author=False)
            return
        mine = sum(1 for r in self.store["reminders"] if r["user_id"] == message.author.id)
        if mine >= MAX_PER_USER:
            await message.reply(
                f"You already have {MAX_PER_USER} reminders — clear some with `/nexreminders`.",
                mention_author=False,
            )
            return

        r = {
            "id": self.store["next_id"],
            "user_id": message.author.id,
            "channel_id": message.channel.id,
            "text": (text or "…you never said what 🤔")[:MAX_TEXT],
            "fire_at": fire_at.isoformat(),
            "repeat": "none",
            "dm": False,
            "message_id": message.id,
        }
        self.store["next_id"] += 1
        self.store["reminders"].append(r)
        _save_store(self.store)
        self._schedule(r)

        line = random.choice(CONFIRM_LINES).format(
            when=discord.utils.format_dt(fire_at, "R"), text=r["text"],
        )
        await message.reply(line, mention_author=False)

    # ── /nexremind ───────────────────────────────────────────────────────────

    @app_commands.command(name="nexremind", description="Set a reminder — e.g. when: 2h30m, 9pm, tomorrow 8am")
    @app_commands.describe(
        when="When to fire: 30m, 2h30m, 3 hours and 2 mins, 21:00, 9pm, tomorrow 8am (clock times are UTC)",
        text="What to remind you about",
        repeat="Repeat this reminder",
        where="Where the reminder fires (default: this channel)",
    )
    @app_commands.choices(
        repeat=[
            app_commands.Choice(name="No repeat", value="none"),
            app_commands.Choice(name="Daily", value="daily"),
            app_commands.Choice(name="Weekly", value="weekly"),
        ],
        where=[
            app_commands.Choice(name="This channel", value="channel"),
            app_commands.Choice(name="DM", value="dm"),
        ],
    )
    async def nexremind(
        self,
        interaction: discord.Interaction,
        when: str,
        text: str,
        repeat: str = "none",
        where: str = "channel",
    ):
        now = datetime.now(timezone.utc)
        fire_at = parse_when(when, now)
        if fire_at is None:
            await interaction.response.send_message(
                "I couldn't understand that time. Try `30m`, `2h30m`, `1d12h`, `21:00`, `9pm`, or `tomorrow 8am`.",
                ephemeral=True,
            )
            return
        if fire_at - now > timedelta(days=MAX_DAYS):
            await interaction.response.send_message(f"Reminders can be at most {MAX_DAYS} days out.", ephemeral=True)
            return
        mine = sum(1 for r in self.store["reminders"] if r["user_id"] == interaction.user.id)
        if mine >= MAX_PER_USER:
            await interaction.response.send_message(
                f"You already have {MAX_PER_USER} reminders — cancel some with `/nexreminders`.", ephemeral=True,
            )
            return

        r = {
            "id": self.store["next_id"],
            "user_id": interaction.user.id,
            "channel_id": interaction.channel_id,
            "text": text[:MAX_TEXT],
            "fire_at": fire_at.isoformat(),
            "repeat": repeat,
            "dm": where == "dm",
        }
        self.store["next_id"] += 1
        self.store["reminders"].append(r)
        _save_store(self.store)
        self._schedule(r)

        repeat_note = "" if repeat == "none" else f", repeating **{repeat}**"
        where_note = "by DM" if where == "dm" else "here"
        await interaction.response.send_message(
            f"{emoji('nex_timeout')} Got it — I'll remind you {where_note} {discord.utils.format_dt(fire_at, 'R')}"
            f" ({discord.utils.format_dt(fire_at, 't')}){repeat_note}: **{r['text']}**",
            ephemeral=True,
        )

    @nexremind.autocomplete("when")
    async def when_autocomplete(self, interaction: discord.Interaction, current: str):
        now = datetime.now(timezone.utc)
        if current.strip():
            fire_at = parse_when(current, now)
            if fire_at and fire_at - now <= timedelta(days=MAX_DAYS):
                label = f"✓ {current.strip()} → in {_humanize(fire_at - now)}"
                return [app_commands.Choice(name=label[:100], value=current[:100])]
            return [app_commands.Choice(name=f"✗ '{current[:60]}' — try 30m, 2h30m, 9pm, tomorrow 8am", value=current[:100])]
        return [
            app_commands.Choice(name=ex, value=ex)
            for ex in ("30m", "1h", "2h30m", "1d", "21:00", "tomorrow 8am")
        ]

    # ── /nexreminders ────────────────────────────────────────────────────────

    @app_commands.command(name="nexreminders", description="List and cancel your reminders")
    async def nexreminders(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            view=ReminderListLayout(self, interaction.user.id), ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Reminders(bot))
