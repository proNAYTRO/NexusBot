import os
import json
import asyncio
import aiohttp
from discord.ext import commands, tasks
from datetime import datetime, timezone, timedelta

from utils.db import init_db, upsert_war, upsert_attack
from utils.emojis import emoji

ADMIN_ID = 1048729773926522981

COC_BASE   = "https://api.clashofclans.com/v1"
TIMEOUT    = aiohttp.ClientTimeout(connect=5, sock_read=20)
STATUS_FILE = "data/capture_status.json"

FAMILY_CLANS: dict[str, str] = {
    "#2LQYLVQ82": "PineappleSxpres",
    "#92QUCULV":  "KryptoniteKrewe",
    "#GGRY80R0":  "KreweOfHydra",
    "#2YRPL0PQ0": "KreweOfVoodoo",
    "#9QY9QJJQ":  "KreweOfRoyals",
    "#2L9VVY0RL": "KreweOfTitans",
    "#8CUCUQ2L":  "KreweOfSinners",
    "#Y880CVR0":  "KreweOfChaos",
    "#22CJ9CRL0": "Krewe Of Acadia",
    "#RJPVQL9L":  "Assassin'sGuild",
    "#2LJVPQJRQ": "Misfit Clashers",
    "#99CULVQP":  "Loot Krewe",
    "#LP8RJGGC":  "Krewe Fatale",
    "#LPP0VQ0L":  "Krewe Esports",
    "#2J822R8UP": "Krewe Parking",
}


def _parse_dt(coc_str: str) -> datetime | None:
    """Parse a CoC API timestamp ('20260627T043543.000Z') into an aware UTC datetime."""
    try:
        return datetime.strptime(coc_str, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _parse_time(coc_str: str) -> str:
    """Normalize CoC API timestamps to ISO format for consistent DB sorting.
    Input:  '20260627T043543.000Z'
    Output: '2026-06-27T04:35:43'
    """
    dt = _parse_dt(coc_str)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else (coc_str or "")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _load_status() -> dict:
    """Load per-clan capture health status from disk (survives bot restarts)."""
    if os.path.exists(STATUS_FILE):
        try:
            with open(STATUS_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_run_at": None, "clans": {}}


def _save_status(status: dict) -> None:
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)


def _enc(tag: str) -> str:
    return tag.strip().upper().replace("#", "%23")

def _norm(tag: str) -> str:
    t = tag.strip().upper()
    return t if t.startswith("#") else "#" + t

def _headers() -> dict:
    return {"Authorization": f"Bearer {os.getenv('COC_API_KEY', '')}"}


def _th_map(side: dict) -> dict[str, int]:
    return {
        _norm(m["tag"]): m.get("townhallLevel", m.get("townHallLevel", 0))
        for m in side.get("members", [])
    }

def _name_map(side: dict) -> dict[str, str]:
    return {_norm(m["tag"]): m.get("name", "") for m in side.get("members", [])}


def _fresh_map(all_attacks: list[dict]) -> dict[int, bool]:
    """First attack on each defender base in attack order = fresh."""
    seen: set[str] = set()
    result: dict[int, bool] = {}
    for atk in sorted(all_attacks, key=lambda a: a.get("order", 0)):
        dtag = _norm(atk.get("defenderTag", ""))
        result[atk.get("order", 0)] = dtag not in seen
        seen.add(dtag)
    return result


def _store_war(war: dict, family_tag: str, war_type: str,
               cwl_season: str | None, cwl_round: int | None, war_key: str):
    state = war.get("state", "")
    if state in ("notInWar", "notStarted"):
        return

    clan_data = war.get("clan", {})
    opp_data  = war.get("opponent", {})
    clan_tag  = _norm(clan_data.get("tag", ""))

    our   = clan_data if clan_tag == family_tag else opp_data
    their = opp_data  if clan_tag == family_tag else clan_data

    war_id = upsert_war(
        war_key            = war_key,
        family_clan_tag    = family_tag,
        family_clan_name   = FAMILY_CLANS.get(family_tag, our.get("name", "")),
        war_type           = war_type,
        opponent_tag       = _norm(their.get("tag", "")),
        opponent_name      = their.get("name", ""),
        team_size          = war.get("teamSize", 0),
        attacks_per_member = war.get("attacksPerMember", 1 if war_type == "cwl" else 2),
        start_time         = _parse_time(war.get("startTime", "")),
        end_time           = _parse_time(war.get("endTime", "")),
        state              = state,
        cwl_season         = cwl_season,
        cwl_round          = cwl_round,
        family_stars       = our.get("stars"),
        opp_stars          = their.get("stars"),
        family_destruction = our.get("destructionPercentage"),
        raw_json           = json.dumps(war),
    )

    all_attacks: list[dict] = []
    for side in (clan_data, opp_data):
        for member in side.get("members", []):
            all_attacks.extend(member.get("attacks", []))

    if not all_attacks:
        return

    ths                = {**_th_map(clan_data),  **_th_map(opp_data)}
    names              = {**_name_map(clan_data), **_name_map(opp_data)}
    fresh              = _fresh_map(all_attacks)
    family_member_tags = {_norm(m["tag"]) for m in our.get("members", [])}

    for atk in all_attacks:
        atag  = _norm(atk.get("attackerTag", ""))
        dtag  = _norm(atk.get("defenderTag", ""))
        order = atk.get("order", 0)
        upsert_attack(
            war_id             = war_id,
            attacker_tag       = atag,
            attacker_name      = names.get(atag, ""),
            attacker_th        = ths.get(atag, 0),
            defender_tag       = dtag,
            defender_th        = ths.get(dtag, 0),
            stars              = atk.get("stars", 0),
            destruction        = atk.get("destructionPercentage", 0.0),
            order_idx          = order,
            is_fresh           = fresh.get(order, True),
            is_family_attacker = atag in family_member_tags,
        )


async def _fetch_regular(
    session: aiohttp.ClientSession, clan_tag: str
) -> tuple[str, datetime | None, str | None]:
    """Returns (log_message, war_end_time, war_state). end_time/state are None
    when there's no active war worth scheduling an end-of-war capture around."""
    url = f"{COC_BASE}/clans/{_enc(clan_tag)}/currentwar"
    async with session.get(url, headers=_headers(), timeout=TIMEOUT) as r:
        if r.status == 403:
            return f"{clan_tag}: war log private (403)", None, None
        if r.status != 200:
            return f"{clan_tag}: HTTP {r.status}", None, None
        war = await r.json()

    state = war.get("state", "")
    if state == "notInWar":
        return f"{clan_tag}: not in regular war", None, None

    war_key = f"{clan_tag}|{war.get('startTime', '')}"
    _store_war(war, clan_tag, "regular", None, None, war_key)
    end_dt = _parse_dt(war.get("endTime", ""))
    return f"{FAMILY_CLANS.get(clan_tag, clan_tag)}: regular {state} captured", end_dt, state


async def _fetch_cwl(session: aiohttp.ClientSession, clan_tag: str,
                     seen: set[str]) -> list[str]:
    logs = []
    url  = f"{COC_BASE}/clans/{_enc(clan_tag)}/currentwar/leaguegroup"
    async with session.get(url, headers=_headers(), timeout=TIMEOUT) as r:
        if r.status in (403, 404):
            return logs
        if r.status != 200:
            return [f"{clan_tag}: CWL group HTTP {r.status}"]
        group = await r.json()

    if group.get("state") == "notInWar":
        return logs

    season = group.get("season", "")

    for round_idx, rnd in enumerate(group.get("rounds", []), start=1):
        for war_tag in rnd.get("warTags", []):
            if war_tag == "#0" or war_tag in seen:
                continue
            seen.add(war_tag)

            wurl = f"{COC_BASE}/clanwarleagues/wars/{_enc(war_tag)}"
            async with session.get(wurl, headers=_headers(), timeout=TIMEOUT) as wr:
                if wr.status != 200:
                    continue
                war = await wr.json()

            if war.get("state") == "notStarted":
                continue

            c_tag = _norm(war.get("clan", {}).get("tag", ""))
            o_tag = _norm(war.get("opponent", {}).get("tag", ""))
            family = c_tag if c_tag in FAMILY_CLANS else (o_tag if o_tag in FAMILY_CLANS else None)
            if not family:
                continue

            _store_war(war, family, "cwl", season, round_idx, war_tag)
            logs.append(f"CWL {war_tag} R{round_idx} ({season}): captured")

    return logs


END_OF_WAR_WINDOW = timedelta(minutes=30)  # schedule if war ends within this long
END_OF_WAR_DELAY  = timedelta(minutes=2)   # capture this long after war end


class WarCapture(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        init_db()
        self._eow_tasks: dict[str, asyncio.Task] = {}
        self.capture_loop.start()

    def cog_unload(self):
        self.capture_loop.cancel()
        for task in self._eow_tasks.values():
            task.cancel()

    @tasks.loop(minutes=30)
    async def capture_loop(self):
        await self._run_capture()

    @capture_loop.before_loop
    async def _before_capture(self):
        await self.bot.wait_until_ready()

    async def _run_capture(self) -> list[str]:
        logs = []
        seen_cwl: set[str] = set()
        status = _load_status()
        status.setdefault("clans", {})

        async with aiohttp.ClientSession() as session:
            for tag in FAMILY_CLANS:
                clan_status = status["clans"].setdefault(tag, {})
                clan_status["last_checked"] = _now_iso()

                try:
                    result, end_dt, war_state = await _fetch_regular(session, tag)
                    clan_status["regular"] = result
                    clan_status.pop("regular_error", None)
                    if "captured" in result:
                        logs.append(result)
                        clan_status["last_success"] = _now_iso()
                    self._maybe_schedule_end_of_war(tag, end_dt, war_state)
                except Exception as e:
                    clan_status["regular_error"] = f"{type(e).__name__}: {e}"
                    logs.append(f"{tag} regular: {e}")

                try:
                    cwl_logs = await _fetch_cwl(session, tag, seen_cwl)
                    clan_status.pop("cwl_error", None)
                    if cwl_logs:
                        logs.extend(cwl_logs)
                        clan_status["last_success"] = _now_iso()
                except Exception as e:
                    clan_status["cwl_error"] = f"{type(e).__name__}: {e}"
                    logs.append(f"{tag} CWL: {e}")

                await asyncio.sleep(0.3)

        status["last_run_at"] = _now_iso()
        _save_status(status)

        ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
        print(f"[capture] {ts} — {len(logs)} update(s)")
        return logs

    def _maybe_schedule_end_of_war(self, clan_tag: str, end_dt: datetime | None, state: str | None):
        """Schedule a one-off final capture shortly after a regular war ends, so
        last-minute attacks aren't lost if the next 30-min poll lands too late
        (or the war has already rolled to notInWar by then). CWL is exempt —
        war tags persist permanently in the API so a later poll can't lose data."""
        if end_dt is None or state != "inWar":
            return

        eow_key = f"{clan_tag}|{end_dt.isoformat()}"
        if eow_key in self._eow_tasks:
            return

        remaining = end_dt - datetime.now(timezone.utc)
        if remaining > END_OF_WAR_WINDOW:
            return

        task = asyncio.create_task(self._end_of_war_capture(clan_tag, end_dt, eow_key))
        self._eow_tasks[eow_key] = task

    async def _end_of_war_capture(self, clan_tag: str, end_dt: datetime, eow_key: str):
        try:
            delay = (end_dt + END_OF_WAR_DELAY - datetime.now(timezone.utc)).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)

            async with aiohttp.ClientSession() as session:
                result, _, _ = await _fetch_regular(session, clan_tag)

            ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
            print(f"[capture] {ts} — end-of-war capture: {result}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[capture] end-of-war capture failed for {clan_tag}: {e}")
        finally:
            self._eow_tasks.pop(eow_key, None)

    @commands.command(name="nexcapture") #test
    async def nexcapture(self, ctx: commands.Context):
        if ctx.author.id != ADMIN_ID:
            return
        msg  = await ctx.send(f"{emoji('nex_loading')} Running war data capture...")
        logs = await self._run_capture()
        body = "\n".join(logs) if logs else "Nothing new captured."
        await msg.edit(content=f"```\n{body[:1900]}\n```")

    @commands.command(name="nexstatus")
    async def nexstatus(self, ctx: commands.Context):
        """Show last capture time and any errors per family clan."""
        if ctx.author.id != ADMIN_ID:
            return

        status   = _load_status()
        last_run = status.get("last_run_at") or "never"
        clans    = status.get("clans", {})

        lines = [f"Last capture cycle: {last_run}", ""]
        for tag, name in FAMILY_CLANS.items():
            cs      = clans.get(tag, {})
            checked = cs.get("last_checked", "never")
            success = cs.get("last_success", "never")
            line    = f"{name}: last checked {checked} | last success {success}"
            if cs.get("regular_error"):
                line += f"\n    ⚠ regular: {cs['regular_error']}"
            if cs.get("cwl_error"):
                line += f"\n    ⚠ CWL: {cs['cwl_error']}"
            lines.append(line)

        body = "\n".join(lines)
        await ctx.send(f"```\n{body[:1900]}\n```")


async def setup(bot: commands.Bot):
    await bot.add_cog(WarCapture(bot))