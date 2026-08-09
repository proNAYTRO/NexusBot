"""
Import historical war attack data from ClashPerk /export attack-log xlsx files.

Usage (run from project root):
    python -m utils.import_clashperk <regular.xlsx> <cwl.xlsx>

Both arguments are required. Pass the path to each exported file.
Clans not in FAMILY_CLANS are silently skipped.
"""

import sys
import re
import openpyxl
from datetime import timedelta

from utils.db import init_db, open_connection, upsert_war, bulk_upsert_attacks

FAMILY_CLANS: set[str] = {
    "#2LQYLVQ82",  # PineappleSxpres
    "#92QUCULV",   # KryptonityKrewe
    "#GGRY80R0",   # KreweOfHydra
    "#2YRPL0PQ0",  # KreweOfVoodoo
    "#9QY9QJJQ",   # KreweOfRoyals
    "#2L9VVY0RL",  # KreweOfTitans
    "#8CUCUQ2L",   # KreweOfSinners
    "#Y880CVR0",   # KreweOfChaos
    "#22CJ9CRL0",  # Krewe Of Acadia
    "#RJPVQL9L",   # Assassin'sGuild
    "#2LJVPQJRQ",  # Misfit Clashers
    "#99CULVQP",   # Loot Krewe
    "#LP8RJGGC",   # Krewe Fatale
    "#LPP0VQ0L",   # Krewe Esports
    "#2J822R8UP",  # Krewe Parking
}

_TAG_RE = re.compile(r"\(#([A-Z0-9]+)\)")


def _norm(tag) -> str:
    if not tag:
        return ""
    t = str(tag).strip().upper()
    return t if t.startswith("#") else "#" + t


def _sheet_clan_tag(sheet_name: str) -> str | None:
    """Extract '#TAG' from a sheet name like 'ClanName (#TAG)'."""
    m = _TAG_RE.search(sheet_name)
    return f"#{m.group(1)}" if m else None


def _load_file(path) -> tuple[dict, list]:
    """
    path may be a filesystem path (str) or an in-memory file-like object
    (e.g. io.BytesIO from a Discord attachment) — openpyxl accepts both.

    Returns:
      wars    : {war_id_str -> war_kwargs_dict}
      attacks : [attack_dict, ...]
    """
    wb = openpyxl.load_workbook(path, read_only=True)
    wars: dict[str, dict] = {}
    attacks: list[dict] = []
    skipped_sheets = []

    for sheet_name in wb.sheetnames:
        clan_tag = _sheet_clan_tag(sheet_name)
        if not clan_tag or clan_tag not in FAMILY_CLANS:
            skipped_sheets.append(sheet_name)
            continue

        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))
        if len(rows) < 2:
            continue

        data_rows = rows[1:]
        sheet_attacks = 0

        for row in data_rows:
            try:
                war_id_raw = row[4]
                if war_id_raw is None:
                    continue
                war_key = str(int(war_id_raw))

                start_dt = row[22]
                war_type_raw = str(row[24]).strip().lower() if row[24] else "normal"
                war_type = "cwl" if war_type_raw == "cwl" else "regular"

                if war_key not in wars:
                    end_dt = None
                    if start_dt:
                        delta = timedelta(hours=24) if war_type == "cwl" else timedelta(hours=48)
                        end_dt = start_dt + delta

                    clan_tag_row = _norm(row[16])
                    if clan_tag_row not in FAMILY_CLANS:
                        clan_tag_row = clan_tag  # fall back to sheet tag

                    wars[war_key] = dict(
                        war_key          = war_key,
                        family_clan_tag  = clan_tag_row,
                        family_clan_name = str(row[17]) if row[17] else "",
                        war_type         = war_type,
                        opponent_tag     = _norm(row[19]),
                        opponent_name    = str(row[20]) if row[20] else "",
                        team_size        = int(row[23]) if row[23] else 0,
                        attacks_per_member = 1 if war_type == "cwl" else 2,
                        start_time       = start_dt.isoformat() if start_dt else "",
                        end_time         = end_dt.isoformat() if end_dt else "",
                        state            = "warEnded",
                        cwl_season       = (
                            f"{start_dt.year:04d}-{start_dt.month:02d}"
                            if start_dt and war_type == "cwl" else None
                        ),
                        cwl_round        = None,
                    )

                attacker_tag = _norm(row[6] or row[0])
                defender_tag = _norm(row[7])
                if not attacker_tag or not defender_tag:
                    continue

                attacks.append(dict(
                    war_key      = war_key,
                    attacker_tag = attacker_tag,
                    attacker_name= str(row[1]) if row[1] else "",
                    attacker_th  = int(row[3]) if row[3] else 0,
                    defender_tag = defender_tag,
                    defender_th  = int(row[14]) if row[14] else 0,
                    stars        = int(row[8]) if row[8] is not None else 0,
                    destruction  = float(row[10]) if row[10] is not None else 0.0,
                    order_idx    = int(row[5]) if row[5] else 0,
                ))
                sheet_attacks += 1

            except Exception as e:
                print(f"  [warn] skipped row in {sheet_name}: {e}")
                continue

        print(f"  {sheet_name}: {sheet_attacks} attacks")

    if skipped_sheets:
        print(f"  Skipped {len(skipped_sheets)} non-family sheets")

    wb.close()
    return wars, attacks


def _compute_fresh(attacks: list[dict]) -> None:
    """Add 'is_fresh' in-place, grouped by war_key and ordered by order_idx."""
    by_war: dict[str, list] = {}
    for atk in attacks:
        by_war.setdefault(atk["war_key"], []).append(atk)

    for war_atks in by_war.values():
        seen: set[str] = set()
        for atk in sorted(war_atks, key=lambda a: a["order_idx"]):
            dtag = atk["defender_tag"]
            atk["is_fresh"] = dtag not in seen
            seen.add(dtag)


def main():
    if len(sys.argv) < 3:
        print("Usage: python -m utils.import_clashperk <regular.xlsx> <cwl.xlsx>")
        sys.exit(1)

    regular_path, cwl_path = sys.argv[1], sys.argv[2]
    init_db()

    all_wars: dict[str, dict] = {}
    all_attacks: list[dict] = []

    for label, path in [("Regular", regular_path), ("CWL", cwl_path)]:
        print(f"\nLoading {label}: {path}")
        wars, attacks = _load_file(path)
        for k, v in wars.items():
            all_wars.setdefault(k, v)  # first-seen wins for war metadata
        all_attacks.extend(attacks)
        print(f"  -> {len(wars)} wars, {len(attacks)} attacks")

    print(f"\nTotal: {len(all_wars)} unique wars, {len(all_attacks)} attacks")
    print("Computing fresh-hit flags...")
    _compute_fresh(all_attacks)

    # One shared connection/transaction for the whole import instead of every
    # war/attack opening and closing its own — the difference between a few
    # seconds and several minutes at tens of thousands of rows.
    conn = open_connection()
    try:
        print("Writing wars to DB...")
        war_id_map: dict[str, int] = {}
        total_wars = len(all_wars)
        for i, (war_key, kwargs) in enumerate(all_wars.items(), start=1):
            war_id_map[war_key] = upsert_war(conn=conn, **kwargs)
            if i % 200 == 0 or i == total_wars:
                print(f"  ...{i}/{total_wars} wars written")

        print("Writing attacks to DB...")
        ready = [
            {**atk, "war_id": war_id_map[atk["war_key"]]}
            for atk in all_attacks
            if atk["war_key"] in war_id_map
        ]
        new_count = bulk_upsert_attacks(conn, ready)
        conn.commit()
    finally:
        conn.close()

    print(f"\nDone. {new_count} new attack row(s) written ({len(ready)} processed, "
          f"{len(ready) - new_count} already present) across {len(all_wars)} wars.")


if __name__ == "__main__":
    main()
