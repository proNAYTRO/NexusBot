"""/nexroster — CWL roster allocation recommendation (admin, public).

Judges CWL signups on their Beta-smoothed 3-star rate vs TH18 over the prior
month (ClashKing-sourced — see utils/roster_scoring), then either allocates the
combined Pine+Krypto+Hydra signup pool across the auto-run mains (Krypto/Hydra)
+ droppable overflow (mode=top-pool), or flags Voodoo standouts for pull-up while
keeping everyone else in Voodoo (mode=voodoo). Pine is hand-picked (config
`manual`) and never auto-allocated — its signups are scored and, if not in the
uploaded assignment, fall through to the other clans as leftovers. Output is a
recommendation the leaders confirm and adjust — the bot never has the last word
on borderline/equal-tier picks.

Data comes exclusively from ClashKing; the local war_data.db is intentionally
out of this path (it was missing the CWL window the metric needs).

Rendered with Components V2 (`discord.ui.LayoutView` + `Container`/`TextDisplay`/
`Section`/`Separator`/`ActionRow`) instead of classic Embed+View — see
cogs/clash/bases.py for the pattern this follows. Each clan/list card is its
own `Container`; several containers ride in one LayoutView per message, chunked
across followups the same way the old code chunked embeds (Components V2 has
its own per-message component-count/character budget, not the 10-embeds limit,
but the chunking shape is the same). The "Mark these as placed" button used to
be a separate classic `discord.ui.View` riding alongside the embeds — since a
message's `IS_COMPONENTS_V2` flag is permanent, the button now lives inside its
own trailing `Container` on the final `LayoutView` instead.
"""

import io
import os
import json
import asyncio
import urllib.parse
from datetime import datetime, timezone
from typing import Literal, Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from cogs.cwl.capture import FAMILY_CLANS, ADMIN_ID
from cogs.clash.clan import WAR_LEAGUE_EMOJI
from cogs.cwl.verify import NEXUS_RED, _short_name
from utils.roster_parser import parse_signup_export, detect_clan_tag
from utils.roster_scoring import (
    load_config, score_player, suggest_running_clans, allocate_pool, voodoo_pullups,
)
from utils.clashking import fetch_player_warhits, warhits_to_attacks
from utils.emojis import emoji

COC_BASE = "https://api.clashofclans.com/v1"
STATE_FILE = "data/roster_state.json"
CK_CONCURRENCY = 8
TIMEOUT = aiohttp.ClientTimeout(connect=5, sock_read=20)
HEADER_THUMBNAIL_URL = "https://i.imgur.com/3C7gXAF.png"

# 3-letter "FROM" codes for the home-clan column (Krewe* names collide on a
# naive prefix, so map by tag). Non-family homes fall back to "•••".
CLAN_CODE = {
    "#2LQYLVQ82": "PIN", "#92QUCULV": "KRY", "#GGRY80R0": "HYD", "#2YRPL0PQ0": "VOO",
    "#9QY9QJJQ": "ROY", "#2L9VVY0RL": "TIT", "#8CUCUQ2L": "SIN", "#Y880CVR0": "CHA",
    "#22CJ9CRL0": "ACA", "#RJPVQL9L": "ASN", "#2LJVPQJRQ": "MIS", "#99CULVQP": "LOO",
    "#LP8RJGGC": "FAT", "#LPP0VQ0L": "ESP", "#2J822R8UP": "PRK",
}

# Friendly short display names for the header's running-clans line (distinct
# from the terse 3-letter CLAN_CODE used in the per-clan player tables).
CLAN_SHORT_NAME = {
    "#2LQYLVQ82": "Pine", "#92QUCULV": "Krypto", "#GGRY80R0": "Hydra",
    "#2YRPL0PQ0": "Voodoo", "#9QY9QJJQ": "Royals", "#2L9VVY0RL": "Titans",
    "#8CUCUQ2L": "Sinners", "#Y880CVR0": "Chaos", "#22CJ9CRL0": "Acadia",
    "#RJPVQL9L": "Assassin's", "#2LJVPQJRQ": "Misfits", "#99CULVQP": "Loot Krewe",
    "#LP8RJGGC": "Fatale", "#LPP0VQ0L": "Esports", "#2J822R8UP": "Parking",
}

# Custom per-clan Discord emoji for the header's running-clans line. Clans
# with no dedicated emoji (overflow, Voodoo, ...) fall back to nex_clash.
CLAN_EMOJI = {
    "#2LQYLVQ82": "<:_KOC_1Pineapples:1203678403543310418>",  # Pine
    "#92QUCULV": "<:_KOC_2Kryptonite:1203678406852743239>",   # Krypto
    "#GGRY80R0": "<:_KOC_4Hydra:1203678409474056242>",        # Hydra
}


def _clan_emoji(tag: str) -> str:
    return CLAN_EMOJI.get(tag, emoji("nex_clash"))


# Reverse lookups for resolving the `clans:` option's tokens back to a tag.
_NAME_TO_TAG = {v.lower(): k for k, v in CLAN_SHORT_NAME.items()}
_CODE_TO_TAG = {v.lower(): k for k, v in CLAN_CODE.items()}


def _resolve_clan_token(token: str) -> Optional[str]:
    """Resolve one `clans:` token — a tag (`#…`), 3-letter code (`TIT`), or short
    name (`Titans`, or a prefix like `tit`) — to a family clan tag, case-
    insensitively. Returns None if nothing matches."""
    t = (token or "").strip()
    if not t:
        return None
    up = t.upper()
    tag = up if up.startswith("#") else "#" + up
    if tag in FAMILY_CLANS:
        return tag
    low = t.lower()
    if low in _CODE_TO_TAG:
        return _CODE_TO_TAG[low]
    if low in _NAME_TO_TAG:
        return _NAME_TO_TAG[low]
    for name, tg in _NAME_TO_TAG.items():  # short-name prefix, e.g. "tit" -> Titans
        if name.startswith(low):
            return tg
    return None


def _parse_clans(raw: str) -> tuple[list[str], list[str]]:
    """Parse the comma-separated `clans:` string into (resolved tags in order,
    unresolved tokens). Deduped, order preserved. Split on commas only so multi-
    word names like `Loot Krewe` survive."""
    tags, bad, seen = [], [], set()
    for tok in (raw or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        tag = _resolve_clan_token(tok)
        if tag is None:
            bad.append(tok)
        elif tag not in seen:
            seen.add(tag)
            tags.append(tag)
    return tags, bad


# ── small helpers ────────────────────────────────────────────────────────────

def _prev_month() -> str:
    now = datetime.now(timezone.utc)
    y, m = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    return f"{y:04d}-{m:02d}"


def _this_month() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def _family_set(config: dict) -> set:
    return set(config["top_pool"]) | set(config["overflow"]) | {config["voodoo"]}


def _load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


async def _fetch_league(session: aiohttp.ClientSession, tag: str, api_key: str):
    """Best-effort live clan info (league + badge) for a clan. Returns None on any
    failure — the caller falls back to the configured fill order for ordering,
    shows '—' for the league, and skips the author badge, so no CoC key locally
    still works. One API call covers both league and badge."""
    if not api_key:
        return None
    url = f"{COC_BASE}/clans/{urllib.parse.quote(tag, safe='')}"
    try:
        async with session.get(url, headers={"Authorization": f"Bearer {api_key}"},
                               timeout=TIMEOUT) as r:
            if r.status != 200:
                return None
            data = await r.json()
    except Exception:
        return None
    wl = data.get("warLeague", {}) or {}
    return {
        "league_id": wl.get("id"),
        "league_name": wl.get("name", "Unranked"),
        "badge_url": (data.get("badgeUrls") or {}).get("medium"),
    }


# ── layout builders ─────────────────────────────────────────────────────────

def _home_code(p: dict) -> str:
    return CLAN_CODE.get(p.get("home_clan_tag"), "•••")


def _table(players: list[dict], show_home: bool = True) -> str:
    """ClashPerk-style stacked rows (fits mobile): #  TH  SCR  ★  FROM  NAME.
    SCR is the smoothed 3★-vs-TH18 rate (0-100); ★ = 20-21 CWL stars
    (move-up), ⚠ = <4 wars. Each row is its own single-backtick inline-code
    span (not one shared ``` block) — Discord renders that as an individual
    pill-shaped row with its own background, matching ClashPerk's list look
    instead of one big enclosing rectangle. Fixed-width FROM (home clan) sits
    before NAME so CJK/emoji names only ragged the trailing column."""
    head = f"{'#':<4}{'TH':<4}{'SCR':<5}{'':<3}{'FRM':<5}NAME" if show_home \
        else f"{'#':<4}{'TH':<4}{'SCR':<5}{'':<3}NAME"
    lines = [f"`{head}`"]
    for i, p in enumerate(players[:60], 1):
        th = p["th"] or "?"
        sc = p["score"] if p["score"] is not None else "-"
        flag = "⚠" if p["low_sample"] else ("★" if p["promotion_eligible"] else " ")
        # Strip stray backticks so a name can't prematurely close the inline
        # code span and break that row's formatting.
        name = _short_name((p["name"] or "?").replace("`", "'"), 14)
        frm = f"{_home_code(p):<5}" if show_home else ""
        row = f"{i:<4}{str(th):<4}{str(sc):<5}{flag:<3}{frm}{name}"
        lines.append(f"`{row}`")
    if len(players) > 60:
        lines.append(f"`… +{len(players) - 60} more`")
    return "\n".join(lines)


def _clan_container(clan: dict, players: list[dict]) -> discord.ui.Container:
    league_emoji = WAR_LEAGUE_EMOJI.get(clan.get("league_id"), emoji("nex_clash"))
    subtitle = f"{league_emoji} {clan.get('league_name') or 'Unranked'} — {len(players)}/{clan['size']}"
    header = discord.ui.TextDisplay(f"### {clan['name']}\n{subtitle}")

    container = discord.ui.Container(accent_color=NEXUS_RED)
    if clan.get("badge_url"):
        container.add_item(discord.ui.Section(header, accessory=discord.ui.Thumbnail(clan["badge_url"])))
    else:
        container.add_item(header)
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(_table(players, show_home=True) if players else "*(no players)*"))
    return container


def _list_container(title: str, players: list[dict], note: str) -> discord.ui.Container:
    container = discord.ui.Container(accent_color=NEXUS_RED)
    container.add_item(discord.ui.TextDisplay(f"### {title}"))
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(_table(players, show_home=True) if players else "*(none)*"))
    container.add_item(discord.ui.TextDisplay(f"-# {note}"))
    return container


def _container_weight(container: discord.ui.Container) -> int:
    """Rough per-container cost used to chunk containers across messages,
    mirroring the old embeds' `len(description) + len(title) + 120` estimate."""
    total = 120
    for item in container.children:
        text = getattr(item, "content", "") or ""
        if hasattr(item, "children"):  # Section — sum its TextDisplay children
            text = "".join(getattr(c, "content", "") or "" for c in item.children)
        total += len(text)
    return total


class RosterLayout(discord.ui.LayoutView):
    """One message's worth of roster cards. Only the final message of a chunked
    run carries `mark_placed` (season/allocation/clans), which adds a trailing
    'Mark these as placed' button container."""

    def __init__(self, containers: list[discord.ui.Container], mark_placed: Optional[dict] = None):
        super().__init__(timeout=900 if mark_placed else None)
        for c in containers:
            self.add_item(c)

        self.mark_placed = mark_placed
        self.mark_btn: Optional[discord.ui.Button] = None
        if mark_placed:
            self.mark_btn = discord.ui.Button(
                label="Mark these as placed", style=discord.ButtonStyle.danger, emoji="📌"
            )
            self.mark_btn.callback = self._mark
            btn_container = discord.ui.Container(accent_color=NEXUS_RED)
            btn_container.add_item(discord.ui.ActionRow(self.mark_btn))
            self.add_item(btn_container)

    async def _mark(self, interaction: discord.Interaction):
        if interaction.user.id != ADMIN_ID:
            await interaction.response.send_message(f"{emoji('nex_error')} Admin only.", ephemeral=True)
            return

        season = self.mark_placed["season"]
        allocation = self.mark_placed["allocation"]
        clans = {c["clan_tag"]: c for c in self.mark_placed["clans"]}

        state = _load_state()
        placed = state.setdefault(season, {}).setdefault("placed", {})
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        n = 0
        for clan_tag, players in allocation["clans"].items():
            clan = clans.get(clan_tag, {})
            for p in players:
                placed[p["tag"]] = {
                    "clan_tag": clan_tag, "clan_name": clan.get("name", ""),
                    "league": clan.get("league_name", ""), "score": p["score"],
                    "home_clan": p.get("home_clan", ""), "placed_at": now,
                }
                n += 1
        _save_state(state)

        self.mark_btn.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(f"📌 Marked **{n}** players placed for {season}.", ephemeral=True)

    async def on_timeout(self):
        if self.mark_btn:
            self.mark_btn.disabled = True


# ── cog ──────────────────────────────────────────────────────────────────────

class Roster(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.api_key = os.getenv("COC_API_KEY", "")

    async def _send_layouts(self, interaction, containers, mark_placed=None):
        """Send containers across multiple followups so we never hit Components
        V2's per-message component-count/character budget (which would throw
        and leave the interaction stuck 'thinking'). `mark_placed`, if given,
        rides only the final message."""
        chunks, cur, cur_weight = [], [], 0
        for c in containers:
            w = _container_weight(c)
            if cur and (len(cur) >= 4 or cur_weight + w > 5000):
                chunks.append(cur)
                cur, cur_weight = [], 0
            cur.append(c)
            cur_weight += w
        if cur:
            chunks.append(cur)
        for i, ch in enumerate(chunks):
            last = i == len(chunks) - 1
            view = RosterLayout(ch, mark_placed=(mark_placed if (last and mark_placed) else None))
            await interaction.followup.send(view=view)

    async def _score_signups(self, signups: list[dict], since: str,
                             fam: set, config: dict) -> list[dict]:
        """Fetch each signup's ClashKing warhits, map to since-windowed attacks,
        and score. Concurrency-limited; ClashKing is public but be polite.

        Scoring counts ALL of the player's wars, not just family-clan ones — this
        mirrors ClashPerk's "TH Any vs 18" (which judges a player across every
        clan they warred in) and is what the roster actually needs: a signup who
        did their prior-month wars in an outside/feeder clan, or a transfer only
        now joining the family, would otherwise be under-counted or wrongly dumped
        to review despite a full attack record. Verified 2026-07-09 against the
        friend's ClashPerk screenshots: family-only left Ninja Night 7/7 and Johnny
        0/0 (→review); all-wars gives 19/19 and 11/11, matching ClashPerk's 17/20
        and 6/7. `family_clans` is still passed to warhits_to_attacks for correct
        side attribution / war-id dedup — it just no longer filters the set."""
        sem = asyncio.Semaphore(CK_CONCURRENCY)

        async def one(session, row):
            attacks = []
            try:
                async with sem:
                    items = await fetch_player_warhits(session, row["tag"])
                attacks = warhits_to_attacks(items or [], since=since, family_clans=fam)
            except Exception as e:
                # One player's odd ClashKing payload must never sink the whole
                # run — fall back to no data (-> review), keep going.
                print(f"[nexroster] scoring failed for {row['tag']}: {type(e).__name__}: {e}")
            return score_player(row["tag"], config=config, ck_attacks=attacks, export_row=row)

        async with aiohttp.ClientSession() as session:
            return await asyncio.gather(*[one(session, r) for r in signups])

    async def clans_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Suggest additional clans to run, building up a comma-separated value.
        The mains (top-pool) always run and manual clans (Pine) are never auto-
        allocated, so both are left out of the picker; each suggestion appends
        the next clan to whatever's already been chosen."""
        config = load_config()
        mains = set(config["top_pool"]) | set(config.get("manual", []))
        head, sep, frag = current.rpartition(",")
        committed = _parse_clans(head)[0] if sep else []
        prefix = ", ".join(CLAN_SHORT_NAME.get(t, t) for t in committed)
        prefix = f"{prefix}, " if prefix else ""
        f = frag.strip().lower()
        chosen = set(committed)
        out = []
        for tag, short in CLAN_SHORT_NAME.items():
            if tag not in FAMILY_CLANS or tag in mains or tag in chosen:
                continue
            code = CLAN_CODE.get(tag, "").lower()
            if f and not (f in short.lower() or code.startswith(f)):
                continue
            value = f"{prefix}{short}"[:100]
            out.append(app_commands.Choice(name=value, value=value))
            if len(out) >= 25:
                break
        return out

    @app_commands.command(
        name="nexroster",
        description="[Admin] Recommend a CWL roster allocation from ClashPerk signup export(s)",
    )
    @app_commands.describe(
        pine_signup="Pine signup export (.xlsx)",
        krypto_signup="Krypto signup export to merge into the pool (optional)",
        hydra_signup="Hydra signup export to merge into the pool (optional)",
        pine_assignment="Finished Pine assignment export — its players are excluded and Pine dropped from the running clans",
        mode="top-pool = allocate Krypto/Hydra + overflow (Pine is manual, via pine_assignment); voodoo = keep in Voodoo, flag standouts",
        clans="Which clans to actually run this CWL (comma-separated). Mains (Krypto/Hydra) always run; picking clans replaces the auto overflow",
        since_month="Judge performance since this month (YYYY-MM); default previous month",
        size="Roster size per clan (auto = suggest from headcount)",
        exclude_placed="Drop players already marked placed this CWL season",
    )
    @app_commands.autocomplete(clans=clans_autocomplete)
    async def nexroster(
        self,
        interaction: discord.Interaction,
        pine_signup: discord.Attachment,
        krypto_signup: Optional[discord.Attachment] = None,
        hydra_signup: Optional[discord.Attachment] = None,
        pine_assignment: Optional[discord.Attachment] = None,
        mode: Literal["top-pool", "voodoo"] = "top-pool",
        clans: Optional[str] = None,
        since_month: Optional[str] = None,
        size: Literal["auto", "15", "30"] = "auto",
        exclude_placed: bool = True,
    ):
        if interaction.user.id != ADMIN_ID:
            await interaction.response.send_message(f"{emoji('nex_error')} Admin only.", ephemeral=True)
            return
        await interaction.response.defer()  # public
        try:
            await self._run(interaction, pine_signup, krypto_signup, hydra_signup,
                            pine_assignment, mode, clans, since_month, size, exclude_placed)
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                await interaction.followup.send(
                    f"{emoji('nex_error')} Roster run failed: `{type(e).__name__}: {e}`\n"
                    "(full traceback in the bot console)"
                )
            except Exception:
                pass

    async def _run(self, interaction: discord.Interaction,
                   pine_signup, krypto_signup, hydra_signup,
                   pine_assignment, mode, clans, since_month, size, exclude_placed):
        config = load_config()
        fam = _family_set(config)
        month = since_month or _prev_month()
        if len(month) != 7 or month[4] != "-":
            await interaction.followup.send(f"{emoji('nex_error')} `since_month` must be `YYYY-MM` (e.g. `2026-06`).")
            return

        # `clans:` — explicit running set (mains always run; these replace the
        # auto headcount overflow). None = unspecified → auto. All-invalid → error.
        include = None
        if clans:
            include, bad = _parse_clans(clans)
            if bad and not include:
                names = ", ".join(sorted(set(CLAN_SHORT_NAME[t] for t in config["overflow"])
                                         | {CLAN_SHORT_NAME.get(config["voodoo"], "Voodoo")}))
                await interaction.followup.send(
                    f"{emoji('nex_error')} Couldn't recognise any clan in `clans`: {', '.join(bad)}\n"
                    f"Pick from the autocomplete (e.g. {names})."
                )
                return
            if bad:
                await interaction.followup.send(
                    f"{emoji('nex_warning')} Ignoring unrecognised clan(s) in `clans`: {', '.join(bad)}"
                )
        since = f"{month}-01T00:00:00"
        cwl_season = _this_month()

        # ── parse + merge signup files (dedup by tag, first file wins) ──
        merged: dict[str, dict] = {}
        for att in (pine_signup, krypto_signup, hydra_signup):
            if att is None:
                continue
            try:
                data = await att.read()
                for row in parse_signup_export(io.BytesIO(data), family_clans=fam):
                    merged.setdefault(row["tag"], row)
            except Exception as e:
                await interaction.followup.send(f"{emoji('nex_error')} Couldn't parse `{att.filename}`: {e}")
                return
        if not merged:
            await interaction.followup.send(f"{emoji('nex_error')} No signups found in the uploaded file(s).")
            return

        # ── already-assigned exclusion: uploaded finished rosters (e.g. the
        # hand-picked Pine assignment). Their players are removed from the pool
        # and their clan is dropped from the targets, so the bot allocates only
        # the leftovers across the remaining clans. Also persisted so a later
        # run excludes them without re-uploading. ──
        assigned_tags: set = set()
        done_clans: set = set()
        if pine_assignment is not None:
            try:
                data = await pine_assignment.read()
                for row in parse_signup_export(io.BytesIO(data), family_clans=fam):
                    assigned_tags.add(row["tag"])
                ct = detect_clan_tag(io.BytesIO(data), family_clans=fam)
                if ct:
                    done_clans.add(ct)
            except Exception as e:
                await interaction.followup.send(f"{emoji('nex_error')} Couldn't parse assignment `{pine_assignment.filename}`: {e}")
                return

        state = _load_state()
        if assigned_tags:
            self._persist_assigned(state, cwl_season, assigned_tags, done_clans)
        placed = set(state.get(cwl_season, {}).get("placed", {}).keys()) if exclude_placed else set()
        drop = placed | assigned_tags
        signups = [r for r in merged.values() if r["tag"] not in drop]
        # Split the skip count by cause for the header: a tag can only land in
        # one bucket (assigned-this-run takes priority) so the two numbers
        # always sum to the total skipped, with no double-counting.
        merged_tags = set(merged.keys())
        skipped_assigned = len(merged_tags & assigned_tags)
        skipped_placed = len((merged_tags & placed) - assigned_tags)
        if not signups:
            await interaction.followup.send(f"{emoji('nex_error')} Every signup is already placed / assigned.")
            return

        print(f"[nexroster] {interaction.user} • mode={mode} • scoring {len(signups)} players "
              f"(since {since}) via ClashKing…")
        await interaction.followup.send(
            f"{emoji('nex_loading')} Scoring **{len(signups)}** players via ClashKing… (this can take up to a minute)"
        )
        try:
            scored = await asyncio.wait_for(
                self._score_signups(signups, since, fam, config), timeout=480
            )
        except asyncio.TimeoutError:
            await interaction.followup.send(
                f"{emoji('nex_error')} ClashKing scoring timed out (8 min). It may be slow or unreachable "
                "from the host — try again, or with fewer signups."
            )
            return
        print(f"[nexroster] scored {len(scored)}; rendering {mode}")

        if mode == "voodoo":
            await self._render_voodoo(interaction, scored, config, month,
                                      skipped_placed + skipped_assigned)
        else:
            await self._render_top_pool(interaction, scored, config, size, month,
                                        skipped_placed, skipped_assigned, cwl_season,
                                        done_clans, include)

    def _persist_assigned(self, state, season, tags, done_clans):
        """Record manually-assigned players as placed for the season so later
        runs exclude them even without re-uploading the assignment file."""
        placed = state.setdefault(season, {}).setdefault("placed", {})
        clan_tag = next(iter(done_clans), "")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        for tag in tags:
            placed.setdefault(tag, {
                "clan_tag": clan_tag, "clan_name": FAMILY_CLANS.get(clan_tag, ""),
                "source": "manual-assignment", "placed_at": now,
            })
        _save_state(state)

    async def _render_top_pool(self, interaction, scored, config, size, month,
                               skipped_placed, skipped_assigned, cwl_season,
                               done_clans=None, include=None):
        done_clans = done_clans or set()
        suggestion = suggest_running_clans(len(scored), config, exclude=done_clans,
                                           include=include)
        if not suggestion:
            await interaction.followup.send(f"{emoji('nex_error')} No clans left to fill (all excluded).")
            return
        if size != "auto":
            for item in suggestion:
                item["size"] = int(size)

        # Live league + badge (display + context), fill order drives allocation rank.
        async with aiohttp.ClientSession() as session:
            infos = await asyncio.gather(
                *[_fetch_league(session, item["clan_tag"], self.api_key) for item in suggestion]
            )
        clans = []
        for pos, (item, info) in enumerate(zip(suggestion, infos)):
            info = info or {}
            clans.append({
                "clan_tag": item["clan_tag"], "name": FAMILY_CLANS.get(item["clan_tag"], item["clan_tag"]),
                "league_id": info.get("league_id"), "league_name": info.get("league_name"),
                "badge_url": info.get("badge_url"),
                "rank": 100 - pos, "size": item["size"],
            })

        alloc = allocate_pool(scored, clans, config)

        containers = [_clan_container(c, alloc["clans"][c["clan_tag"]])
                     for c in sorted(clans, key=lambda c: -c["rank"])]
        if alloc["review"]:
            containers.append(_list_container(f"{emoji('nex_warning')} Review (low sample / no data)", alloc["review"],
                                              "manual check — not auto-placed"))
        if alloc["bench"]:
            containers.append(_list_container("Bench (beyond capacity)", alloc["bench"],
                                              "overflow — drop a clan or bump sizes"))

        running_line = "   ".join(
            f"{_clan_emoji(c['clan_tag'])} {CLAN_SHORT_NAME.get(c['clan_tag'], c['name'])} {c['size']}"
            for c in clans
        )

        skip_bits = []
        if skipped_placed:
            skip_bits.append(f"{skipped_placed} already placed")
        if skipped_assigned:
            skip_bits.append(f"{skipped_assigned} in assignment file")
        skip_line = (f"{emoji('nex_close')} {skipped_placed + skipped_assigned} skipped ({', '.join(skip_bits)})"
                    if skip_bits else None)

        month_label = datetime.strptime(month, "%Y-%m").strftime("%b %Y")
        now_unix = int(datetime.now(timezone.utc).timestamp())
        description = f"Top Pool — **{len(scored)}** signups scored, since {month_label}\n\n{running_line}"
        if skip_line:
            description += f"\n\n{skip_line}"
        description += f"\n★ move-up (20-21 CWL★)  {emoji('nex_warning')} review (<4 wars)"
        description += f"\n-# <t:{now_unix}:R>"

        header_text = discord.ui.TextDisplay(
            f"## {emoji('nn_nexus')} CWL Roster Recommendation\n{description}"
        )
        header = discord.ui.Container(accent_color=NEXUS_RED)
        header.add_item(discord.ui.Section(header_text, accessory=discord.ui.Thumbnail(HEADER_THUMBNAIL_URL)))

        mark_placed = {"season": cwl_season, "allocation": alloc, "clans": clans}
        await self._send_layouts(interaction, [header] + containers, mark_placed=mark_placed)

    async def _render_voodoo(self, interaction, scored, config, month, skipped):
        floor = config["scoring"].get("voodoo_pullup_min", 78)
        pullups = voodoo_pullups(scored, floor)
        pulled = {p["tag"] for p in pullups}
        stay = [p for p in scored if p["tag"] not in pulled]
        stay.sort(key=lambda p: (p["score"] is None, -(p["score"] or 0)))

        containers = []
        if pullups:
            containers.append(_list_container(f"{emoji('nex_up')} Pull-up candidates (strong enough for a CWL clan)",
                                              pullups, f"score ≥ {floor} — confirm before moving"))
        containers.append(_list_container(f"{emoji('nex_locked')} Staying in Voodoo", stay, "home clan — never emptied"))

        header_desc = (
            f"**{len(scored)}** Voodoo signups scored (since {month})"
            + (f" • {skipped} already placed skipped" if skipped else "")
            + f"\n{len(pullups)} pull-up candidate(s); the rest stay in Voodoo."
        )
        header = discord.ui.Container(accent_color=NEXUS_RED)
        header.add_item(discord.ui.TextDisplay(f"## {emoji('nn_nexus')} CWL Roster Recommendation — Voodoo\n{header_desc}"))

        await self._send_layouts(interaction, [header] + containers)


async def setup(bot: commands.Bot):
    await bot.add_cog(Roster(bot))
