"""/nextodo — personal to-do lists with an interactive checklist view.

One command, two ways in:

- `/nextodo` — opens your list (ephemeral): check tasks off with per-row
  Done/Undo buttons, ➕ add via modal, ✏️ edit via a dropdown-prefilled modal,
  a 🗑 delete mode that swaps the row buttons to Delete, 🧹 clear completed,
  and 📮 share (posts a read-only snapshot of the list to the channel).
- `/nextodo task:…` — quick-add without opening the modal; optional
  `priority` (high/medium/low) and `due` free text. The `due` parameter
  live-parses in its autocomplete ("✓ fri → Fri, Jul 17"), same trick as
  /nexremind's `when`.

Due dates accept: today, tomorrow, Nd (e.g. 3d), weekday names (fri /
friday → next occurrence), and explicit dates (2026-08-01, 8/1). Open tasks
sort high→low priority, then soonest due, then oldest; completed tasks sink
to the bottom struck through. Persists to data/todos.json.
"""

import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.emojis import emoji

NEXUS_RED = 0xE8173A
TODOS_FILE = "data/todos.json"
MAX_PER_USER = 50
MAX_TEXT = 150
PAGE_SIZE = 7
SNAPSHOT_SHOWN = 15

PRIO_ORDER = {"high": 0, "medium": 1, "low": 2}
PRIO_DOT = {"high": "nex_dnd", "medium": "nex_idle", "low": "nex_online"}

_DUE_DAYS_RE = re.compile(r"^(\d{1,3})\s*d(?:ays?)?$", re.IGNORECASE)
_DUE_DATE_RE = re.compile(r"^(?:(\d{4})-)?(\d{1,2})[-/](\d{1,2})$")
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def parse_due(text: str, today: date) -> date | None:
    """Resolve a due string to a date, or None if unparseable."""
    t = text.strip().lower()
    if not t:
        return None
    if t in ("today", "tod"):
        return today
    if t in ("tomorrow", "tmr", "tom"):
        return today + timedelta(days=1)

    m = _DUE_DAYS_RE.match(t)
    if m:
        return today + timedelta(days=int(m.group(1)))

    if t.isalpha() and t[:3] in _WEEKDAYS:
        ahead = (_WEEKDAYS[t[:3]] - today.weekday()) % 7  # 0 → today
        return today + timedelta(days=ahead)

    m = _DUE_DATE_RE.match(t)
    if m:
        year_s, mon_s, day_s = m.groups()
        try:
            if year_s:
                return date(int(year_s), int(mon_s), int(day_s))
            d = date(today.year, int(mon_s), int(day_s))
            return d if d >= today else d.replace(year=today.year + 1)
        except ValueError:
            return None
    return None


def _due_label(due_iso: str, today: date) -> str:
    due = date.fromisoformat(due_iso)
    pretty = due.strftime("%b %d").replace(" 0", " ")
    if due < today:
        return f"{emoji('nex_warning')} overdue ({pretty})"
    if due == today:
        return "due **today**"
    if due == today + timedelta(days=1):
        return "due tomorrow"
    return f"due {pretty} (in {(due - today).days}d)"


def _progress_bar(done: int, total: int, width: int = 8) -> str:
    filled = round(done / total * width) if total else 0
    cells = []
    for i in range(width):
        if i == 0:
            name = "nex_bar_lstart_full" if filled > 0 else "nex_bar_lstart_empty"
        elif i == width - 1:
            name = "nex_bar_rend_full" if filled >= width else "nex_bar_rend_empty"
        else:
            name = "nex_bar_mid_full" if i < filled else "nex_bar_mid_empty"
        cells.append(emoji(name))
    return "".join(cells)


def _item_meta(item: dict, today: date) -> str:
    if item["done"]:
        return f"{emoji('nex_success')} done"
    parts = [f"{emoji(PRIO_DOT[item['priority']])} {item['priority']}"]
    if item["due"]:
        parts.append(_due_label(item["due"], today))
    return " · ".join(parts)


def _load_store() -> dict:
    if not os.path.exists(TODOS_FILE):
        return {"next_id": 1, "todos": []}
    with open(TODOS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_store(store: dict) -> None:
    os.makedirs("data", exist_ok=True)
    with open(TODOS_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2)


class TodoModal(discord.ui.Modal):
    """Add (item=None) or edit a task. Bad priority/due input degrades with a
    notice on the rebuilt list instead of erroring the whole submit."""

    def __init__(self, layout: "TodoListLayout", item: dict | None = None):
        super().__init__(title="Edit task" if item else "Add a task")
        self.layout = layout
        self.item = item
        self.task = discord.ui.TextInput(
            label="Task", max_length=MAX_TEXT, default=item["text"] if item else None,
        )
        self.priority = discord.ui.TextInput(
            label="Priority (high / medium / low)", required=False, max_length=10,
            default=item["priority"] if item else None, placeholder="medium",
        )
        self.due = discord.ui.TextInput(
            label="Due (today, fri, 3d, 2026-08-01)", required=False, max_length=20,
            default=item["due"] if item else None, placeholder="no due date",
        )
        self.add_item(self.task)
        self.add_item(self.priority)
        self.add_item(self.due)

    async def on_submit(self, interaction: discord.Interaction):
        layout, cog = self.layout, self.layout.cog
        notices = []

        prio_raw = self.priority.value.strip().lower()
        priority = {"h": "high", "m": "medium", "l": "low"}.get(prio_raw[:1]) if prio_raw else None
        if prio_raw and priority is None:
            notices.append(f"didn't recognise priority `{prio_raw}` — using medium")
        if priority is None:
            priority = self.item["priority"] if self.item else "medium"

        due_raw = self.due.value.strip()
        due = None
        if due_raw:
            parsed = parse_due(due_raw, datetime.now(timezone.utc).date())
            if parsed:
                due = parsed.isoformat()
            else:
                due = self.item["due"] if self.item else None
                notices.append(f"couldn't read due date `{due_raw}` — try today, fri, 3d, or 2026-08-01")

        if self.item:
            self.item.update(text=self.task.value.strip()[:MAX_TEXT], priority=priority, due=due)
            notices.insert(0, "Task updated.")
        else:
            item = cog.add_todo(layout.user_id, self.task.value.strip()[:MAX_TEXT], priority, due)
            if item is None:
                notices = [f"You already have {MAX_PER_USER} tasks — clear some first."]
            else:
                notices.insert(0, f"Added **{item['text']}**")
        _save_store(cog.store)

        layout.notice = " · ".join(notices)
        layout.rebuild()
        await interaction.response.edit_message(view=layout)


class TodoListLayout(discord.ui.LayoutView):
    """Ephemeral interactive checklist for one user's to-dos."""

    def __init__(self, cog: "Todos", user_id: int, notice: str | None = None):
        super().__init__(timeout=600)
        self.cog = cog
        self.user_id = user_id
        self.page = 0
        self.delete_mode = False
        self.notice = notice
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        today = datetime.now(timezone.utc).date()
        mine = self.cog.user_todos(self.user_id)
        done_count = sum(1 for i in mine if i["done"])

        pages = max(1, math.ceil(len(mine) / PAGE_SIZE))
        self.page = min(self.page, pages - 1)
        shown = mine[self.page * PAGE_SIZE:(self.page + 1) * PAGE_SIZE]

        container = discord.ui.Container(accent_color=NEXUS_RED)
        container.add_item(discord.ui.TextDisplay(
            f"## {emoji('nn_nexus')} Your to-dos\n"
            + (f"{_progress_bar(done_count, len(mine))} **{done_count}/{len(mine)}** done"
               if mine else "-# Nothing on your list — hit **➕ Add task** below.")
        ))
        if self.notice:
            container.add_item(discord.ui.TextDisplay(f"-# {emoji('nex_info')} {self.notice}"))
            self.notice = None
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        for item in shown:
            if self.delete_mode:
                btn = discord.ui.Button(label="Delete", style=discord.ButtonStyle.danger)
                btn.callback = self._make_delete(item["id"])
            elif item["done"]:
                btn = discord.ui.Button(label="Undo", style=discord.ButtonStyle.secondary)
                btn.callback = self._make_toggle(item["id"])
            else:
                btn = discord.ui.Button(label="Done", style=discord.ButtonStyle.success)
                btn.callback = self._make_toggle(item["id"])
            text = f"~~{item['text']}~~" if item["done"] else f"**{item['text']}**"
            container.add_item(discord.ui.Section(
                discord.ui.TextDisplay(f"{text}\n-# {_item_meta(item, today)}"),
                accessory=btn,
            ))

        if mine:
            container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        add_btn = discord.ui.Button(label="➕ Add task", style=discord.ButtonStyle.primary)
        add_btn.callback = self._add
        buttons = [add_btn]
        if mine:
            del_btn = discord.ui.Button(
                label="Exit delete mode" if self.delete_mode else "🗑 Delete tasks",
                style=discord.ButtonStyle.secondary if self.delete_mode else discord.ButtonStyle.danger,
            )
            del_btn.callback = self._toggle_delete_mode
            buttons.append(del_btn)
            if done_count:
                clear_btn = discord.ui.Button(label="🧹 Clear done", style=discord.ButtonStyle.secondary)
                clear_btn.callback = self._clear_done
                buttons.append(clear_btn)
            share_btn = discord.ui.Button(label="📮 Share", style=discord.ButtonStyle.secondary)
            share_btn.callback = self._share
            buttons.append(share_btn)
        container.add_item(discord.ui.ActionRow(*buttons))

        if mine:
            select = discord.ui.Select(placeholder="✏️ Edit a task…", options=[
                discord.SelectOption(
                    label=item["text"][:100], value=str(item["id"]),
                    description=_item_meta(item, today).replace("*", "")[:100],
                    emoji=None,
                )
                for item in mine[:25]
            ])
            # meta strings contain emoji mentions, which SelectOption descriptions
            # render as raw text — strip them down to the words
            for opt in select.options:
                opt.description = re.sub(r"<a?:\w+:\d+>\s*", "", opt.description or "")
            select.callback = self._edit_selected(select)
            container.add_item(discord.ui.ActionRow(select))

        if pages > 1:
            prev_btn = discord.ui.Button(label="◀", style=discord.ButtonStyle.secondary, disabled=self.page == 0)
            prev_btn.callback = self._make_page(-1)
            page_btn = discord.ui.Button(
                label=f"{self.page + 1}/{pages}", style=discord.ButtonStyle.secondary, disabled=True,
            )
            next_btn = discord.ui.Button(label="▶", style=discord.ButtonStyle.secondary, disabled=self.page >= pages - 1)
            next_btn.callback = self._make_page(1)
            container.add_item(discord.ui.ActionRow(prev_btn, page_btn, next_btn))

        self.add_item(container)

    # ── callbacks ────────────────────────────────────────────────────────────

    async def _refresh(self, interaction: discord.Interaction):
        self.rebuild()
        await interaction.response.edit_message(view=self)

    def _make_toggle(self, todo_id: int):
        async def toggle(interaction: discord.Interaction):
            item = self.cog.find_todo(todo_id)
            if item:
                item["done"] = not item["done"]
                _save_store(self.cog.store)
            await self._refresh(interaction)
        return toggle

    def _make_delete(self, todo_id: int):
        async def delete(interaction: discord.Interaction):
            self.cog.store["todos"] = [t for t in self.cog.store["todos"] if t["id"] != todo_id]
            _save_store(self.cog.store)
            if not self.cog.user_todos(self.user_id):
                self.delete_mode = False
            await self._refresh(interaction)
        return delete

    def _make_page(self, step: int):
        async def page(interaction: discord.Interaction):
            self.page += step
            await self._refresh(interaction)
        return page

    async def _add(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TodoModal(self))

    async def _toggle_delete_mode(self, interaction: discord.Interaction):
        self.delete_mode = not self.delete_mode
        await self._refresh(interaction)

    async def _clear_done(self, interaction: discord.Interaction):
        removed = self.cog.clear_done(self.user_id)
        self.notice = f"Cleared {removed} completed task{'s' if removed != 1 else ''}."
        await self._refresh(interaction)

    def _edit_selected(self, select: discord.ui.Select):
        async def edit(interaction: discord.Interaction):
            item = self.cog.find_todo(int(select.values[0]))
            if item is None:
                await self._refresh(interaction)
                return
            await interaction.response.send_modal(TodoModal(self, item))
        return edit

    async def _share(self, interaction: discord.Interaction):
        mine = self.cog.user_todos(self.user_id)
        try:
            await interaction.channel.send(view=TodoSnapshotLayout(interaction.user, mine))
            self.notice = "Posted a snapshot of your list to this channel."
        except (discord.HTTPException, AttributeError):
            self.notice = "Couldn't post here — check the bot's permissions in this channel."
        await self._refresh(interaction)


class TodoSnapshotLayout(discord.ui.LayoutView):
    """Read-only public snapshot posted by the Share button."""

    def __init__(self, user: discord.abc.User, items: list[dict]):
        super().__init__(timeout=None)
        today = datetime.now(timezone.utc).date()
        done_count = sum(1 for i in items if i["done"])

        container = discord.ui.Container(accent_color=NEXUS_RED)
        container.add_item(discord.ui.TextDisplay(
            f"## {emoji('nn_nexus')} {user.display_name}'s to-do list\n"
            f"{_progress_bar(done_count, len(items))} **{done_count}/{len(items)}** done"
        ))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
        lines = []
        for item in items[:SNAPSHOT_SHOWN]:
            if item["done"]:
                lines.append(f"{emoji('nex_success')} ~~{item['text']}~~")
            else:
                due = f" — {_due_label(item['due'], today)}" if item["due"] else ""
                lines.append(f"{emoji(PRIO_DOT[item['priority']])} {item['text']}{due}")
        if len(items) > SNAPSHOT_SHOWN:
            lines.append(f"-# …and {len(items) - SNAPSHOT_SHOWN} more")
        container.add_item(discord.ui.TextDisplay("\n".join(lines)))
        self.add_item(container)


class Todos(commands.Cog):
    """Personal to-do lists — /nextodo."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = _load_store()

    # ── store helpers ────────────────────────────────────────────────────────

    def user_todos(self, user_id: int) -> list[dict]:
        mine = [t for t in self.store["todos"] if t["user_id"] == user_id]
        mine.sort(key=lambda t: (
            t["done"], PRIO_ORDER[t["priority"]], t["due"] or "9999-12-31", t["id"],
        ))
        return mine

    def find_todo(self, todo_id: int) -> dict | None:
        return next((t for t in self.store["todos"] if t["id"] == todo_id), None)

    def add_todo(self, user_id: int, text: str, priority: str, due: str | None) -> dict | None:
        if sum(1 for t in self.store["todos"] if t["user_id"] == user_id) >= MAX_PER_USER:
            return None
        item = {
            "id": self.store["next_id"],
            "user_id": user_id,
            "text": text,
            "priority": priority,
            "due": due,
            "done": False,
            "created": datetime.now(timezone.utc).isoformat(),
        }
        self.store["next_id"] += 1
        self.store["todos"].append(item)
        _save_store(self.store)
        return item

    def clear_done(self, user_id: int) -> int:
        before = len(self.store["todos"])
        self.store["todos"] = [
            t for t in self.store["todos"] if not (t["user_id"] == user_id and t["done"])
        ]
        _save_store(self.store)
        return before - len(self.store["todos"])

    # ── /nextodo ─────────────────────────────────────────────────────────────

    @app_commands.command(name="nextodo", description="Your to-do list — open it, or quick-add a task")
    @app_commands.describe(
        task="Quick-add this task (leave empty to just open your list)",
        priority="Priority for the quick-added task (default: medium)",
        due="Due date: today, tomorrow, fri, 3d, 2026-08-01",
    )
    @app_commands.choices(priority=[
        app_commands.Choice(name="High", value="high"),
        app_commands.Choice(name="Medium", value="medium"),
        app_commands.Choice(name="Low", value="low"),
    ])
    async def nextodo(
        self,
        interaction: discord.Interaction,
        task: str | None = None,
        priority: str = "medium",
        due: str | None = None,
    ):
        notice = None
        task = task.strip() if task else None
        if task:
            due_iso = None
            if due:
                parsed = parse_due(due, datetime.now(timezone.utc).date())
                if parsed is None:
                    await interaction.response.send_message(
                        f"I couldn't read the due date `{due}` — try `today`, `fri`, `3d`, or `2026-08-01`.",
                        ephemeral=True,
                    )
                    return
                due_iso = parsed.isoformat()
            item = self.add_todo(interaction.user.id, task.strip()[:MAX_TEXT], priority, due_iso)
            if item is None:
                await interaction.response.send_message(
                    f"You already have {MAX_PER_USER} tasks — clear some with `/nextodo` first.",
                    ephemeral=True,
                )
                return
            notice = f"Added **{item['text']}**"
        await interaction.response.send_message(
            view=TodoListLayout(self, interaction.user.id, notice=notice), ephemeral=True,
        )

    @nextodo.autocomplete("due")
    async def due_autocomplete(self, interaction: discord.Interaction, current: str):
        today = datetime.now(timezone.utc).date()
        if current.strip():
            parsed = parse_due(current, today)
            if parsed:
                label = f"✓ {current.strip()} → {parsed.strftime('%a, %b %d')}"
                return [app_commands.Choice(name=label[:100], value=current[:100])]
            return [app_commands.Choice(
                name=f"✗ '{current[:60]}' — try today, fri, 3d, 2026-08-01", value=current[:100],
            )]
        return [
            app_commands.Choice(name=ex, value=ex)
            for ex in ("today", "tomorrow", "fri", "3d", "2026-08-01")
        ]


async def setup(bot: commands.Bot):
    await bot.add_cog(Todos(bot))
