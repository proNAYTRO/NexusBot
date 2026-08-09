import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "war_data.db")

_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS wars (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    war_key             TEXT    NOT NULL UNIQUE,
    family_clan_tag     TEXT    NOT NULL,
    family_clan_name    TEXT,
    war_type            TEXT    NOT NULL,
    cwl_season          TEXT,
    cwl_round           INTEGER,
    opponent_tag        TEXT    NOT NULL,
    opponent_name       TEXT,
    team_size           INTEGER,
    attacks_per_member  INTEGER NOT NULL DEFAULT 2,
    start_time          TEXT    NOT NULL,
    end_time            TEXT    NOT NULL,
    state               TEXT,
    family_stars        INTEGER,
    opp_stars           INTEGER,
    family_destruction  REAL,
    season              TEXT,
    iso_week            TEXT,
    raw_json            TEXT
);

CREATE TABLE IF NOT EXISTS attacks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    war_id              INTEGER NOT NULL REFERENCES wars(id) ON DELETE CASCADE,
    attacker_tag        TEXT    NOT NULL,
    attacker_name       TEXT,
    attacker_th         INTEGER,
    defender_tag        TEXT    NOT NULL,
    defender_th         INTEGER,
    stars               INTEGER NOT NULL,
    destruction         REAL    NOT NULL,
    order_idx           INTEGER NOT NULL,
    is_fresh            INTEGER NOT NULL DEFAULT 1,
    is_family_attacker  INTEGER NOT NULL DEFAULT 1,
    UNIQUE(war_id, order_idx)
);

CREATE INDEX IF NOT EXISTS idx_atk_tag  ON attacks(attacker_tag);
CREATE INDEX IF NOT EXISTS idx_atk_dth  ON attacks(defender_th);
CREATE INDEX IF NOT EXISTS idx_war_type ON wars(war_type);
"""


def _migrate():
    """Add columns/indexes introduced after the initial schema — safe to re-run.
    Indexes on columns added here must stay in this function (not _SCHEMA) since
    on an existing DB the ALTER TABLE has to run before the column can be indexed."""
    migrations = [
        "ALTER TABLE wars    ADD COLUMN family_stars       INTEGER",
        "ALTER TABLE wars    ADD COLUMN opp_stars          INTEGER",
        "ALTER TABLE wars    ADD COLUMN family_destruction REAL",
        "ALTER TABLE attacks ADD COLUMN is_family_attacker INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE wars    ADD COLUMN season             TEXT",
        "ALTER TABLE wars    ADD COLUMN iso_week           TEXT",
        "ALTER TABLE wars    ADD COLUMN raw_json           TEXT",
    ]
    with _get_conn() as conn:
        for stmt in migrations:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:
                pass  # column already exists

        for stmt in [
            "CREATE INDEX IF NOT EXISTS idx_war_start_time  ON wars(start_time)",
            "CREATE INDEX IF NOT EXISTS idx_war_season      ON wars(season)",
            "CREATE INDEX IF NOT EXISTS idx_war_family_clan ON wars(family_clan_tag)",
        ]:
            conn.execute(stmt)

        # Self-healing: some historical rows ended up with a full-date season
        # ('2026-07-02') instead of the intended 'YYYY-MM' bucket. Truncate on
        # every startup so it never needs a manual/remote DB fix.
        conn.execute(
            "UPDATE wars SET season = substr(season, 1, 7) "
            "WHERE season IS NOT NULL AND length(season) > 7"
        )


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(_SCHEMA)
    _migrate()


def _get_conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def open_connection() -> sqlite3.Connection:
    """Open a connection for callers that need to share one across many writes
    (e.g. bulk imports) instead of every upsert_war()/upsert_attack() call
    opening and closing its own. Caller owns commit()/close()."""
    return _get_conn()


def _derive_season_week(start_time: str, cwl_season: str | None) -> tuple[str | None, str | None]:
    """season: 'YYYY-MM', authoritative cwl_season if given, else derived from
    start_time so regular wars get the same kind of bucket CWL wars already have.
    iso_week: 'YYYY-Www' via ISO calendar week, computed once here so every
    query can filter/group on a plain column instead of redoing date math."""
    iso_week = None
    dt = None
    if start_time:
        try:
            dt = datetime.fromisoformat(start_time)
        except ValueError:
            dt = None
    if dt is not None:
        iso_year, iso_wk, _ = dt.isocalendar()
        iso_week = f"{iso_year:04d}-W{iso_wk:02d}"

    if cwl_season:
        season = cwl_season
    elif dt is not None:
        season = f"{dt.year:04d}-{dt.month:02d}"
    else:
        season = None

    return season, iso_week


def upsert_war(*, conn: sqlite3.Connection = None, war_key: str, family_clan_tag: str,
               family_clan_name: str, war_type: str, opponent_tag: str, opponent_name: str,
               team_size: int, attacks_per_member: int,
               start_time: str, end_time: str, state: str,
               cwl_season: str = None, cwl_round: int = None,
               family_stars: int = None, opp_stars: int = None,
               family_destruction: float = None, raw_json: str = None) -> int:
    """Pass conn= to share one connection/transaction across many calls (bulk
    imports) — the caller then owns commit()/close(). Otherwise a connection
    is opened and committed/closed for this single call, as before."""
    season, iso_week = _derive_season_week(start_time, cwl_season)
    owns_conn = conn is None
    c = conn if conn is not None else _get_conn()
    try:
        c.execute("""
            INSERT INTO wars (war_key, family_clan_tag, family_clan_name, war_type,
                cwl_season, cwl_round, opponent_tag, opponent_name, team_size,
                attacks_per_member, start_time, end_time, state,
                family_stars, opp_stars, family_destruction,
                season, iso_week, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(war_key) DO UPDATE SET
                state              = excluded.state,
                opponent_name      = excluded.opponent_name,
                family_clan_name   = excluded.family_clan_name,
                family_stars       = COALESCE(excluded.family_stars,       family_stars),
                opp_stars          = COALESCE(excluded.opp_stars,          opp_stars),
                family_destruction = COALESCE(excluded.family_destruction,  family_destruction),
                season             = COALESCE(excluded.season,             season),
                iso_week           = COALESCE(excluded.iso_week,           iso_week),
                raw_json           = COALESCE(excluded.raw_json,           raw_json)
        """, (war_key, family_clan_tag, family_clan_name, war_type,
              cwl_season, cwl_round, opponent_tag, opponent_name, team_size,
              attacks_per_member, start_time, end_time, state,
              family_stars, opp_stars, family_destruction,
              season, iso_week, raw_json))
        row = c.execute("SELECT id FROM wars WHERE war_key = ?", (war_key,)).fetchone()
        if owns_conn:
            c.commit()
        return row["id"]
    finally:
        if owns_conn:
            c.close()


def upsert_attack(*, conn: sqlite3.Connection = None, war_id: int, attacker_tag: str,
                  attacker_name: str, attacker_th: int, defender_tag: str, defender_th: int,
                  stars: int, destruction: float, order_idx: int,
                  is_fresh: bool, is_family_attacker: bool = True):
    """Pass conn= to share one connection/transaction across many calls (bulk
    imports) instead of opening/closing a connection per attack."""
    owns_conn = conn is None
    c = conn if conn is not None else _get_conn()
    try:
        c.execute("""
            INSERT OR IGNORE INTO attacks
            (war_id, attacker_tag, attacker_name, attacker_th,
             defender_tag, defender_th, stars, destruction, order_idx,
             is_fresh, is_family_attacker)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (war_id, attacker_tag, attacker_name, attacker_th,
              defender_tag, defender_th, stars, destruction, order_idx,
              int(is_fresh), int(is_family_attacker)))
        if owns_conn:
            c.commit()
    finally:
        if owns_conn:
            c.close()


def bulk_upsert_attacks(conn: sqlite3.Connection, attacks: list[dict]) -> int:
    """Efficient bulk insert for large imports — one executemany() on a
    shared connection instead of one upsert_attack() call (and connection)
    per row. Each dict needs the same fields as upsert_attack's kwargs, plus
    'war_id'. Caller owns commit()/close(). Returns the number of rows
    actually newly inserted (via conn.total_changes — INSERT OR IGNORE means
    len(attacks) alone would overcount whatever was already there)."""
    rows = [
        (
            a["war_id"], a["attacker_tag"], a["attacker_name"], a["attacker_th"],
            a["defender_tag"], a["defender_th"], a["stars"], a["destruction"],
            a["order_idx"], int(a["is_fresh"]), int(a.get("is_family_attacker", True)),
        )
        for a in attacks
    ]
    before = conn.total_changes
    conn.executemany("""
        INSERT OR IGNORE INTO attacks
        (war_id, attacker_tag, attacker_name, attacker_th,
         defender_tag, defender_th, stars, destruction, order_idx,
         is_fresh, is_family_attacker)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    return conn.total_changes - before


# ── General-purpose query functions ─────────────────────────────
# Prefer these for new commands over writing a new bespoke SQL query — filter
# here, aggregate in Python. Keep dedicated SQL functions only for genuinely
# hot/heavy aggregates (leaderboards etc.), same as the ones below this section.

def fetch_attacks(*, clan_tag: str = None, player_tag: str = None, war_type: str = None,
                   season: str = None, since: str = None, until: str = None,
                   defender_th: int = None, is_family_attacker: bool = None) -> list:
    clauses, params = [], []
    if clan_tag:
        clauses.append("w.family_clan_tag = ?"); params.append(clan_tag)
    if player_tag:
        clauses.append("a.attacker_tag = ?"); params.append(player_tag)
    if war_type:
        clauses.append("w.war_type = ?"); params.append(war_type)
    if season:
        clauses.append("w.season = ?"); params.append(season)
    if since:
        clauses.append("w.start_time >= ?"); params.append(since)
    if until:
        clauses.append("w.start_time < ?"); params.append(until)
    if defender_th is not None:
        clauses.append("a.defender_th = ?"); params.append(defender_th)
    if is_family_attacker is not None:
        clauses.append("a.is_family_attacker = ?"); params.append(int(is_family_attacker))

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _get_conn() as conn:
        return conn.execute(f"""
            SELECT a.*, w.war_type, w.season, w.iso_week, w.start_time,
                   w.family_clan_tag, w.family_clan_name, w.opponent_tag, w.opponent_name
            FROM attacks a JOIN wars w ON a.war_id = w.id
            {where}
            ORDER BY w.start_time
        """, params).fetchall()


def fetch_wars(*, clan_tag: str = None, war_type: str = None, season: str = None,
               since: str = None, until: str = None, opponent_tag: str = None) -> list:
    clauses, params = [], []
    if clan_tag:
        clauses.append("family_clan_tag = ?"); params.append(clan_tag)
    if war_type:
        clauses.append("war_type = ?"); params.append(war_type)
    if season:
        clauses.append("season = ?"); params.append(season)
    if since:
        clauses.append("start_time >= ?"); params.append(since)
    if until:
        clauses.append("start_time < ?"); params.append(until)
    if opponent_tag:
        clauses.append("opponent_tag = ?"); params.append(opponent_tag)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _get_conn() as conn:
        return conn.execute(f"""
            SELECT * FROM wars
            {where}
            ORDER BY start_time
        """, params).fetchall()


# ── Restored query functions (previously used by /nexcwlstats) ──

def query_player_vs_th(player_tag: str, defender_th: int,
                       war_type: str = "both", since: str = None) -> list:
    with _get_conn() as conn:
        since_clause = "AND w.start_time >= ?" if since else ""
        if war_type == "both":
            params = [player_tag, defender_th] + ([since] if since else [])
            return conn.execute(f"""
                SELECT a.stars, a.destruction, a.is_fresh, a.attacker_th,
                       w.war_type, w.start_time
                FROM attacks a JOIN wars w ON a.war_id = w.id
                WHERE a.attacker_tag = ? AND a.defender_th = ? {since_clause}
                ORDER BY w.start_time
            """, params).fetchall()
        params = [player_tag, defender_th, war_type] + ([since] if since else [])
        return conn.execute(f"""
            SELECT a.stars, a.destruction, a.is_fresh, a.attacker_th,
                   w.war_type, w.start_time
            FROM attacks a JOIN wars w ON a.war_id = w.id
            WHERE a.attacker_tag = ? AND a.defender_th = ? AND w.war_type = ? {since_clause}
            ORDER BY w.start_time
        """, params).fetchall()


def get_player_info(player_tag: str) -> tuple[str, int] | None:
    """Name and TH from the player's most recent war attack."""
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT a.attacker_name, a.attacker_th
            FROM attacks a JOIN wars w ON a.war_id = w.id
            WHERE a.attacker_tag = ?
            ORDER BY w.start_time DESC LIMIT 1
        """, (player_tag,)).fetchone()
        return (row["attacker_name"], row["attacker_th"]) if row else None


def war_count(player_tag: str, war_type: str = "both", since: str = None) -> int:
    with _get_conn() as conn:
        since_clause = "AND w.start_time >= ?" if since else ""
        if war_type == "both":
            params = [player_tag] + ([since] if since else [])
            row = conn.execute(f"""
                SELECT COUNT(DISTINCT a.war_id)
                FROM attacks a JOIN wars w ON a.war_id = w.id
                WHERE a.attacker_tag = ? {since_clause}
            """, params).fetchone()
        else:
            params = [player_tag, war_type] + ([since] if since else [])
            row = conn.execute(f"""
                SELECT COUNT(DISTINCT a.war_id)
                FROM attacks a JOIN wars w ON a.war_id = w.id
                WHERE a.attacker_tag = ? AND w.war_type = ? {since_clause}
            """, params).fetchone()
        return row[0]


# ── CWL Dashboard queries ────────────────────────────────────────

def get_latest_cwl_season() -> str | None:
    """Returns the most recent CWL season key in the DB (e.g. '2026-06')."""
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT cwl_season FROM wars
            WHERE war_type = 'cwl' AND cwl_season IS NOT NULL
            ORDER BY cwl_season DESC LIMIT 1
        """).fetchone()
        return row["cwl_season"] if row else None


def get_current_cwl_season() -> str | None:
    """Returns the latest CWL season only if it has active wars (inWar or preparation)."""
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT cwl_season FROM wars
            WHERE war_type = 'cwl'
              AND cwl_season IS NOT NULL
              AND state IN ('inWar', 'preparation')
            ORDER BY cwl_season DESC LIMIT 1
        """).fetchone()
        return row["cwl_season"] if row else None


def get_cwl_seasons() -> list[str]:
    """Distinct CWL seasons in the DB, newest first."""
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT cwl_season FROM wars
            WHERE war_type = 'cwl' AND cwl_season IS NOT NULL
            ORDER BY cwl_season DESC LIMIT 24
        """).fetchall()
        return [r["cwl_season"] for r in rows]


def get_seasons() -> list[str]:
    """Distinct `season` values across ALL wars (regular + CWL), newest first.
    Backs the `/nexstats` season-since autocomplete — unlike get_cwl_seasons()
    this isn't CWL-only, since regular wars get a season bucket too. The GLOB
    guards against any malformed non-'YYYY-MM' value (e.g. a stray full date)
    ever reaching the UI, on top of the startup migration that truncates them."""
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT season FROM wars
            WHERE season IS NOT NULL AND season GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
            ORDER BY season DESC LIMIT 24
        """).fetchall()
        return [r["season"] for r in rows]


def query_cwl_season_summary(season: str) -> list:
    """One row per CWL war: family stars, opp stars, attacks used, state."""
    with _get_conn() as conn:
        return conn.execute("""
            SELECT
                w.id, w.cwl_round, w.family_clan_tag, w.family_clan_name,
                w.opponent_name, w.team_size, w.attacks_per_member,
                w.state, w.end_time,
                w.family_stars, w.opp_stars, w.family_destruction,
                COUNT(CASE WHEN a.is_family_attacker = 1 THEN 1 END) AS family_atks
            FROM wars w
            LEFT JOIN attacks a ON a.war_id = w.id
            WHERE w.cwl_season = ? AND w.war_type = 'cwl'
            GROUP BY w.id
            ORDER BY w.cwl_round, w.family_clan_name
        """, (season,)).fetchall()


def query_cwl_clan_attackers(season: str, clan_name: str, round_num: int = None) -> list:
    """Top 5 attackers for a single family clan in a CWL season."""
    round_clause = "AND w.cwl_round = ?" if round_num else ""
    params = [season, clan_name] + ([round_num] if round_num else [])
    with _get_conn() as conn:
        return conn.execute(f"""
            SELECT
                a.attacker_tag, a.attacker_name,
                COUNT(*)                                      AS atks,
                SUM(a.stars)                                  AS stars,
                SUM(CASE WHEN a.stars=3 THEN 1 ELSE 0 END)   AS triples,
                AVG(a.destruction)                            AS avg_dest,
                MAX(a.attacker_th)                            AS th
            FROM attacks a
            JOIN wars w ON a.war_id = w.id
            WHERE w.cwl_season = ? AND w.war_type = 'cwl'
              AND w.family_clan_name = ? AND a.is_family_attacker = 1
              {round_clause}
            GROUP BY a.attacker_tag
            ORDER BY stars DESC, avg_dest DESC
            LIMIT 5
        """, params).fetchall()


def query_cwl_top_attackers(season: str, round_num: int = None) -> list:
    """Top attackers for a CWL season, ranked by stars then destruction."""
    round_clause = "AND w.cwl_round = ?" if round_num else ""
    params = [season] + ([round_num] if round_num else [])
    with _get_conn() as conn:
        return conn.execute(f"""
            SELECT
                a.attacker_tag,
                a.attacker_name,
                w.family_clan_name,
                COUNT(*)                                          AS atks,
                SUM(a.stars)                                      AS stars,
                SUM(CASE WHEN a.stars = 3 THEN 1 ELSE 0 END)     AS triples,
                AVG(a.destruction)                                AS avg_dest,
                MAX(a.attacker_th)                                AS th
            FROM attacks a
            JOIN wars w ON a.war_id = w.id
            WHERE w.cwl_season = ? AND w.war_type = 'cwl'
              AND a.is_family_attacker = 1
              {round_clause}
            GROUP BY a.attacker_tag
            ORDER BY stars DESC, avg_dest DESC
            LIMIT 20
        """, params).fetchall()
