"""Thin async client for the public ClashKing API (https://api.clashk.ing).

Backs /nexstats and /nexroster: a player's global war-attack history, mapped
into the same attack-dict shape utils/db.fetch_attacks() used to return, so
/nexstats's filters + leaderboard renderer can consume it unchanged.

ClashKing is public — no auth header, no strict rate limit. utils/ stays
self-contained (no imports from cogs/), so the tag/timestamp helpers are
duplicated here on purpose rather than pulled from cogs/cwl/capture.py.
"""

import aiohttp
from datetime import datetime, timezone

CK_BASE = "https://api.clashk.ing"
TIMEOUT = aiohttp.ClientTimeout(connect=5, sock_read=20)

# ClashKing 'war_data.type' → our war_type. 'friendly' wars are dropped entirely.
_WAR_TYPE_MAP = {"cwl": "cwl", "random": "regular", "classic": "regular"}


def _norm(tag: str) -> str:
    t = (tag or "").strip().upper()
    return t if t.startswith("#") else "#" + t


def _enc(tag: str) -> str:
    return _norm(tag).replace("#", "%23")


def _to_dt(value) -> datetime | None:
    """Best-effort parse of a ClashKing/CoC timestamp into an aware UTC datetime.
    Handles the CoC compact form ('20260627T043543.000Z'), plain ISO, and unix."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (ValueError, OSError):
            return None
    s = str(value)
    # CoC compact: 20260627T043543.000Z
    try:
        return datetime.strptime(s, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") if dt else ""


async def fetch_player_warhits(
    session: aiohttp.ClientSession,
    tag: str,
    *,
    since_unix: int = 0,
    until_unix: int | None = None,
    limit: int = 100,
) -> list | None:
    """Return the list of war records under 'items' for a player tag, or None on
    failure. Each record is {war_data, member_data, attacks[], defenses[]}.

    NOTE: the endpoint hard-caps at 100 records (most-recent first) regardless of
    `limit` — confirmed 2026-07-08. That's ~3-4 months for an active player, so
    'all-time' or old-season windows will under-count vs local capture. For a fair
    side-by-side, compare recent windows (last N wars/days, or a recent season).
    Reaching further back would need timestamp_end pagination — not done here."""
    url = f"{CK_BASE}/player/{_enc(tag)}/warhits"
    params = {"timestamp_start": int(since_unix), "limit": int(limit)}
    if until_unix is not None:
        params["timestamp_end"] = int(until_unix)
    try:
        async with session.get(url, params=params, timeout=TIMEOUT) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
    except Exception:
        return None
    if isinstance(data, dict):
        return data.get("items", [])
    if isinstance(data, list):
        return data
    return None


def warhits_to_attacks(
    items: list, *, war_type: str | None = None, since: str | None = None,
    family_clans=None,
) -> list[dict]:
    """Map ClashKing war records into local attack-dict rows (one per attack the
    player *made*). `war_type` ('regular'/'cwl') and `since` (ISO 'YYYY-MM-DDTHH:MM:SS')
    filter exactly like utils/db.fetch_attacks would, so results line up with
    /nexstats. `since` is compared as a string against the war's start_time — the
    same lexicographic comparison SQLite does on the stored ISO column.

    `family_clans` (a set/dict of family clan tags, injected so utils/ stays
    cogs-free) drives side attribution: ClashKing orients `clan`/`opponent` by the
    war's own CoC ordering, NOT by the queried player, and exposes no member lists —
    so the player's own clan can sit on *either* side. We attribute the war to the
    family side when exactly one side is a family clan (correct for family play);
    otherwise we default to `clan`.  `attacks[]` records carry a nested `defender`
    but no nested `attacker`, so attacker fields come from `member_data` (the player).
    """
    fam = set(family_clans or ())
    rows: list[dict] = []
    for rec in items or []:
        wd = rec.get("war_data", {}) or {}
        wtype = _WAR_TYPE_MAP.get((wd.get("type") or "").lower())
        if wtype is None:  # friendly / unknown → skip
            continue
        if war_type and wtype != war_type:
            continue

        start_dt = _to_dt(wd.get("startTime") or wd.get("warStartTime"))
        start_iso = _iso(start_dt)
        if since and start_iso and start_iso < since:
            continue

        # Local stores season as 'YYYY-MM'; ClashKing's CWL season is a date
        # like '2026-07-01' — trim to 'YYYY-MM' so both sources agree in format.
        season_raw = wd.get("season")
        season = (season_raw[:7] if season_raw else
                  (start_dt.strftime("%Y-%m") if start_dt else ""))

        ctag = _norm((wd.get("clan") or {}).get("tag", ""))
        otag = _norm((wd.get("opponent") or {}).get("tag", ""))
        # Player's own clan = the family side when exactly one side is a family clan.
        if otag in fam and ctag not in fam:
            player_clan = otag
        else:
            player_clan = ctag

        # war_id must be identical no matter which side the player is on, so the
        # same war dedupes across accounts/sides: prefer the CWL warTag, else a
        # side-order-independent key from the (sorted) clan pair + start time.
        war_tag = wd.get("tag")
        war_id = _norm(war_tag) if war_tag else f"{start_iso}|{'|'.join(sorted([ctag, otag]))}"

        member = rec.get("member_data", {}) or {}
        for atk in rec.get("attacks", []) or []:
            defender = atk.get("defender", {}) or {}
            rows.append({
                "war_id": war_id,
                "order_idx": atk.get("order", atk.get("attack_order", 0)),
                "stars": atk.get("stars", 0) or 0,
                "destruction": atk.get("destructionPercentage", 0) or 0,
                "attacker_tag": _norm(atk.get("attackerTag") or member.get("tag", "")),
                "defender_tag": _norm(atk.get("defenderTag") or defender.get("tag", "")),
                "attacker_th": member.get("townhallLevel") or member.get("townHallLevel") or 0,
                "defender_th": defender.get("townhallLevel") or defender.get("townHallLevel") or 0,
                "attacker_name": member.get("name") or "",
                "is_fresh": 1 if atk.get("fresh") else 0,
                "war_type": wtype,
                "season": season,
                "start_time": start_iso,
                "family_clan_tag": player_clan,
            })
    return rows
