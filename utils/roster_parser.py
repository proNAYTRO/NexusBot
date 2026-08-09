"""Parser for ClashPerk CWL roster / signup xlsx exports.

A ClashPerk export has two sheets:
  * Sheet 1 — title like '🟡 CWL Signup (Clan)' / '… CWL Assignment …'. One row
    per signup WITH ClashPerk-computed stats (Player Tag, Town Hall, Total
    Attacks, 3 Stars, Avg. Destruction, current clan, the War-Rotation Group…).
    This is the primary parse target and the signup list.
  * Sheet 2 — title like 'Clan (#TAG)'. The clan roster with Role
    (Mem/Eld/Co) and 'Signed up?'. We read it only to enrich each signup with
    its Role (the 'Co' = co-leader flag the deferred constraint will use).

utils/ stays cog-free: the family-clan set is injected via `family_clans=`
rather than imported from cogs/, and the tag helper is duplicated here on
purpose (same convention as utils/clashking.py).
"""

import io
import re

import openpyxl

# CoC player/clan tags: '#' + base-20 charset (no O/I). Used for the non-xlsx
# fallback and to sanity-check a cell actually holds a tag.
_TAG_RE = re.compile(r"#[0289PYLQGRJCUV]{4,12}")


def _norm(tag) -> str | None:
    if tag is None:
        return None
    t = str(tag).strip().upper()
    if not t:
        return None
    return t if t.startswith("#") else "#" + t


def _to_int(value):
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value):
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _header_index(header_row: list) -> dict[str, int]:
    """Map a lowercased/stripped header name → column index, so we look columns
    up by name and survive ClashPerk reordering or adding columns."""
    idx = {}
    for i, cell in enumerate(header_row):
        if cell is None:
            continue
        idx[str(cell).strip().lower()] = i
    return idx


def _pick_stats_sheet(wb) -> str:
    """The stats sheet is the one whose title mentions 'CWL Signup'/'CWL
    Assignment' (tolerating the leading colour emoji); fall back to sheet 1."""
    for name in wb.sheetnames:
        low = name.lower()
        if "cwl signup" in low or "cwl assignment" in low or "signup" in low or "assignment" in low:
            return name
    return wb.sheetnames[0]


def _roster_roles(wb, stats_sheet: str) -> dict[str, str]:
    """Read Role (Mem/Eld/Co) keyed by normalized tag from the *other* sheet
    (the clan-roster sheet), so signups can be enriched with co-leader status."""
    roles: dict[str, str] = {}
    for name in wb.sheetnames:
        if name == stats_sheet:
            continue
        ws = wb[name]
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            continue
        idx = _header_index(list(header))
        tag_i = idx.get("tag")
        role_i = idx.get("role")
        if tag_i is None or role_i is None:
            continue
        for r in rows:
            tag = _norm(r[tag_i]) if tag_i < len(r) else None
            if tag:
                roles[tag] = (r[role_i] if role_i < len(r) else None) or ""
    return roles


def parse_signup_export(file_like, *, family_clans=None) -> list[dict]:
    """Parse a ClashPerk roster/signup xlsx (path or file-like, e.g. an
    io.BytesIO from a Discord attachment) into a list of signup dicts:

        {tag, name, current_clan, current_clan_tag, th, role, group,
         total_attacks, stars, three_stars, avg_dest}

    `family_clans` is accepted for symmetry/injection (utils stays cogs-free);
    it isn't used to filter here — every signup row is returned and the caller
    decides scope. Falls back to a tag-regex scan for non-xlsx input.
    """
    try:
        wb = openpyxl.load_workbook(file_like, read_only=True, data_only=True)
    except Exception:
        return _fallback_tags(file_like)

    stats_sheet = _pick_stats_sheet(wb)
    roles = _roster_roles(wb, stats_sheet)

    ws = wb[stats_sheet]
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    if not header:
        wb.close()
        return []
    idx = _header_index(list(header))

    def col(row, *names):
        for n in names:
            i = idx.get(n)
            if i is not None and i < len(row):
                return row[i]
        return None

    out: list[dict] = []
    for row in rows:
        tag = _norm(col(row, "player tag", "tag"))
        if not tag or not _TAG_RE.fullmatch(tag):
            continue
        out.append({
            "tag": tag,
            "name": col(row, "player name", "name") or "",
            "current_clan": col(row, "current clan", "clan") or "",
            "current_clan_tag": _norm(col(row, "current clantag", "clan tag")),
            "th": _to_int(col(row, "town hall")),
            "role": roles.get(tag, ""),
            "group": (col(row, "group") or ""),
            "total_attacks": _to_int(col(row, "total attacks")),
            "stars": _to_int(col(row, "stars")),
            "three_stars": _to_int(col(row, "3 stars")),
            "avg_dest": _to_float(col(row, "avg. destruction", "avg destruction")),
        })
    wb.close()
    return out


def detect_clan_tag(file_like, *, family_clans=None) -> str | None:
    """Which clan an export belongs to, read from its sheet titles (e.g.
    'PineappleSxpres (#2LQYLVQ82)'). Used to drop a clan that was already
    assigned manually (its assignment file is uploaded) from the target list."""
    try:
        wb = openpyxl.load_workbook(file_like, read_only=True, data_only=True)
    except Exception:
        return None
    fam = set(family_clans or ())
    found = None
    for name in wb.sheetnames:
        for m in _TAG_RE.findall(str(name).upper()):
            tag = _norm(m)
            if tag and (not fam or tag in fam):
                found = tag
                break
        if found:
            break
    wb.close()
    return found


def _fallback_tags(file_like) -> list[dict]:
    """Best-effort: pull bare player tags out of a non-xlsx blob (csv/txt/paste)."""
    try:
        if hasattr(file_like, "read"):
            if hasattr(file_like, "seek"):
                file_like.seek(0)
            data = file_like.read()
        elif isinstance(file_like, (bytes, bytearray)):
            data = file_like
        else:
            with open(file_like, "rb") as f:
                data = f.read()
        text = data.decode("utf-8", "ignore") if isinstance(data, (bytes, bytearray)) else str(data)
    except Exception:
        return []

    seen = set()
    out = []
    for m in _TAG_RE.findall(text.upper()):
        tag = _norm(m)
        if tag and tag not in seen:
            seen.add(tag)
            out.append({
                "tag": tag, "name": "", "current_clan": "", "current_clan_tag": None,
                "th": None, "role": "", "group": "",
                "total_attacks": None, "stars": None, "three_stars": None, "avg_dest": None,
            })
    return out
