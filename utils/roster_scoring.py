"""Roster scoring + tiered allocation (pure/sync, cog-free).

Reproduces how the family judges CWL signups: the primary signal is the
Beta-smoothed 3-star rate vs TH18 over the prior month (both war types) — the
exact metric behind ClashPerk's `TH Any vs 18` screenshots — and "20-21 CWL
stars last season" is a separate promotion nudge, not folded into the score.

Calibration against real July-2026 rosters validated the smoothing: "100%"
players on 1-2 attacks (Omega 2/2, Parth 2/2) were placed in overflow, NOT
promoted; `(triples+2)/(attempts+4)` reproduces that. Sub-`min_attempts`
players are always flagged for review.

DATA SOURCE — ClashKing ONLY (no local DB). The local `war_data.db` capture
was found to be missing the June/July CWL wars the metric depends on (verified
2026-07-09: Peak read 2/2 locally vs 33/33 in ClashPerk; ClashKing had the full
window). So the roster path reads exclusively from ClashKing's global war
history. The cog fetches each signup's warhits (async), maps them with
`utils.clashking.warhits_to_attacks`, filters to family play + the prior-month
window, and passes the resulting attack rows into `score_player`; this module
stays sync/cog-free and never touches the DB. A tag ClashKing has no data for
goes to review (manual check), never a guessed placement.
"""

import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "roster_config.json")

# Built-in defaults so the bot works even when data/roster_config.json wasn't
# deployed (data/ is excluded from the SFTP sync to protect the live DB).
# load_config() writes this out on first run, so it also becomes a tunable file.
DEFAULT_CONFIG = {
    "top_pool": ["#2LQYLVQ82", "#92QUCULV", "#GGRY80R0"],
    # Hand-picked clans that are NEVER auto-allocated (their roster is uploaded
    # via the assignment file). Pine is always manual; it stays in top_pool for
    # family-set membership + signup parsing, but suggest_running_clans drops it
    # from the running set so it can never be auto-filled. The auto-run mains are
    # therefore top_pool minus manual (= Krypto/Hydra).
    "manual": ["#2LQYLVQ82"],
    "overflow": ["#2L9VVY0RL", "#Y880CVR0", "#22CJ9CRL0"],
    "voodoo": "#2YRPL0PQ0",
    "scoring": {
        "alpha": 2, "beta": 2, "min_attempts": 4, "max_defender_th": 18,
        "cwl_star_promote": 20, "promote_nudge": 3, "default_size": 30,
        "voodoo_pullup_min": 78,
    },
    "constraints": {"max_rotators": 2, "min_coleaders": 2},
    "leagues": {
        "48000022": {"name": "Legend League", "rank": 22},
        "48000021": {"name": "Titan League I", "rank": 21},
        "48000020": {"name": "Titan League II", "rank": 20},
        "48000019": {"name": "Titan League III", "rank": 19},
        "48000018": {"name": "Champion League I", "rank": 18},
        "48000017": {"name": "Champion League II", "rank": 17},
        "48000016": {"name": "Champion League III", "rank": 16},
        "48000015": {"name": "Master League I", "rank": 15},
        "48000014": {"name": "Master League II", "rank": 14},
        "48000013": {"name": "Master League III", "rank": 13},
        "48000012": {"name": "Crystal League I", "rank": 12},
        "48000011": {"name": "Crystal League II", "rank": 11},
        "48000010": {"name": "Crystal League III", "rank": 10},
        "48000009": {"name": "Gold League I", "rank": 9},
        "48000008": {"name": "Gold League II", "rank": 8},
        "48000007": {"name": "Gold League III", "rank": 7},
        "48000006": {"name": "Silver League I", "rank": 6},
        "48000005": {"name": "Silver League II", "rank": 5},
        "48000004": {"name": "Silver League III", "rank": 4},
        "48000003": {"name": "Bronze League I", "rank": 3},
        "48000002": {"name": "Bronze League II", "rank": 2},
        "48000001": {"name": "Bronze League III", "rank": 1},
        "48000000": {"name": "Unranked", "rank": 0},
    },
}


def load_config(path: str = CONFIG_PATH) -> dict:
    """Load the roster config, falling back to (and writing out) DEFAULT_CONFIG
    when the file is missing — so a fresh deploy where data/ wasn't synced still
    works, and leaves a tunable file behind."""
    if not os.path.exists(path):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
        except OSError:
            pass
        return DEFAULT_CONFIG
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ── Scoring ──────────────────────────────────────────────────────────────────

def _smoothed(triples: int, attempts: int, alpha: float, beta: float) -> float:
    """Beta-smoothed hit rate as a 0-100 score. With no attempts this returns
    the prior mean (alpha/(alpha+beta)); callers gate that case to review."""
    return 100.0 * (triples + alpha) / (attempts + alpha + beta)


def score_player(tag: str, *, config: dict, ck_attacks: list,
                 export_row: dict | None = None) -> dict:
    """Score one player from ClashKing attack rows only.

    `ck_attacks` are the player's attacks already mapped by
    `utils.clashking.warhits_to_attacks` and pre-filtered by the cog to family
    play + the prior-month window (see module docstring). We compute the
    vs-TH18 3★ rate over them, smooth it, and read CWL stars for the promotion
    nudge. `export_row` (from the uploaded ClashPerk file) supplies identity
    fallbacks + home clan/role/group only — never the score. A player with no
    ClashKing attacks goes to review, never a guess.
    """
    sc = config["scoring"]
    alpha, beta = sc["alpha"], sc["beta"]
    min_attempts = sc["min_attempts"]
    target_th = sc["max_defender_th"]
    ck_attacks = ck_attacks or []

    vs = [a for a in ck_attacks if a["defender_th"] == target_th]
    attempts = len(vs)
    triples = sum(1 for a in vs if a["stars"] == 3)
    wars = len({a["war_id"] for a in ck_attacks})

    # CWL stars for the "20-21 stars -> move up" nudge must be ~ONE CWL season
    # (7 rounds, max 21). ClashKing's CWL `season` strings are inconsistent
    # (e.g. '2026-06' and '2026-06-16' both land in June), so instead of trusting
    # them we take the most recent 7 CWL attacks by time and cap at 21 — the last
    # CWL the player played, robust to the season-string mess and the window.
    cwl = sorted((a for a in ck_attacks if a.get("war_type") == "cwl"),
                 key=lambda a: a.get("start_time", ""), reverse=True)
    cwl_stars = min(sum(a["stars"] for a in cwl[:7]), 21)

    # Identity: ClashKing (name/TH from the feed) -> uploaded export -> tag.
    ck_name = next((a.get("attacker_name") for a in ck_attacks if a.get("attacker_name")), None)
    ck_th = next((a.get("attacker_th") for a in ck_attacks if a.get("attacker_th")), None)
    name = ck_name or (export_row or {}).get("name") or tag
    th = ck_th or (export_row or {}).get("th")

    has_data = attempts > 0
    low_sample = attempts < min_attempts
    score = round(_smoothed(triples, attempts, alpha, beta)) if has_data else None
    raw_rate = round(100.0 * triples / attempts, 1) if attempts else None

    return {
        "tag": tag,
        "name": name,
        "th": th,
        "triples": triples,
        "attempts": attempts,
        "wars": wars,
        "cwl_stars": cwl_stars,
        "score": score,
        "raw_rate": raw_rate,
        "source": "clashking" if has_data else None,
        "low_sample": low_sample,
        "promotion_eligible": cwl_stars >= sc["cwl_star_promote"],
        "review": (not has_data) or low_sample,
        "home_clan": (export_row or {}).get("current_clan", ""),
        "home_clan_tag": (export_row or {}).get("current_clan_tag"),
        "role": (export_row or {}).get("role", ""),
        "group": (export_row or {}).get("group", ""),
    }


# ── Tier + allocation ────────────────────────────────────────────────────────

def league_meta(league_id, config: dict) -> dict:
    """Look up a CoC warLeague id in config -> {name, rank}. rank only orders the
    clans (top league filled first); it never gates who's eligible — tiering comes
    from rank-and-fill-to-capacity, so the flagship gets the best available rather
    than underfilling behind a hard score floor. Unknown ids -> Unranked (rank 0)."""
    leagues = config["leagues"]
    return leagues.get(str(league_id), leagues.get("48000000", {"name": "Unranked", "rank": 0}))


def suggest_running_clans(pool_size: int, config: dict, exclude=None,
                          include=None) -> list[dict]:
    """Suggest which clans to run and each one's size from headcount. Top-pool
    (main) clans always run; sizes grow 15 -> 30 as needed. `exclude` drops
    clans already filled manually (e.g. Pine, whose assignment file was
    uploaded) so the bot neither re-fills them nor counts their slots.

    `include` (the tags an admin picked via the `clans:` option) makes the
    running set explicit: mains + exactly those clans, in the given order, with
    the headcount-driven overflow auto-pick skipped entirely. When `include`
    is None, overflow clans are auto-added lowest-priority-last until the pool
    fits. Returned as an editable list the cog confirms before allocation.

    `config['manual']` clans (e.g. Pine — hand-picked, uploaded separately) are
    NEVER in the running set: they're folded into the exclusion so no path (auto
    overflow OR an explicit `include`) can auto-fill them. Falls back to the
    built-in default when an older config file predates the `manual` key, so the
    live host drops Pine without regenerating its config."""
    default_size = config["scoring"].get("default_size", 30)
    manual = set(config.get("manual", DEFAULT_CONFIG["manual"]))
    ex = set(exclude or ()) | manual
    top = [t for t in config["top_pool"] if t not in ex]

    running = [{"clan_tag": t, "size": 15} for t in top]
    seen = {c["clan_tag"] for c in running}
    if include is not None:
        # Explicit pick: mains + exactly these clans (deduped, excluded dropped).
        for t in include:
            if t in ex or t in seen:
                continue
            running.append({"clan_tag": t, "size": 15})
            seen.add(t)
    else:
        overflow = [t for t in config["overflow"] if t not in ex]
        capacity = 15 * len(running)
        for t in overflow:
            if capacity >= pool_size:
                break
            running.append({"clan_tag": t, "size": 15})
            capacity += 15

    # Grow sizes 15 -> 30 (in running order) until every signup fits.
    capacity = sum(c["size"] for c in running)
    for clan in running:
        if capacity >= pool_size:
            break
        if clan["size"] < default_size:
            capacity += default_size - clan["size"]
            clan["size"] = default_size
    return running


def allocate_pool(scored: list[dict], running_clans: list[dict], config: dict) -> dict:
    """Rank-and-fill-to-capacity. `running_clans` items: {clan_tag, name,
    league_id, league_name, rank, size}. Rank the whole (non-review) pool by
    score, then walk clans top league -> lowest, handing each the next `size`
    players off the top. Leftovers past total capacity -> bench; low-sample /
    no-data -> review for manual slotting. `promotion_eligible` (best CWL
    >= 20-21) is surfaced as a ★ flag rather than a hidden score bump — a
    move-up candidate the leaders act on, keeping the ranking monotonic and
    the table honest. Everything is a recommendation the leaders finalize
    (esp. equal-tier Krypto<->Hydra body-balancing, which no data can decide)."""
    review = [p for p in scored if p["review"]]
    pool = sorted((p for p in scored if not p["review"] and p["score"] is not None),
                  key=lambda p: (-p["score"], -p["wars"]))

    result = {c["clan_tag"]: [] for c in running_clans}
    i = 0
    for clan in sorted(running_clans, key=lambda c: -c["rank"]):
        result[clan["clan_tag"]] = pool[i:i + clan["size"]]
        i += len(result[clan["clan_tag"]])

    return {"clans": result, "review": review, "bench": pool[i:]}


def voodoo_pullups(scored_voodoo: list[dict], min_score: float) -> list[dict]:
    """Voodoo players strong enough to be pulled up into a top-pool clan if it
    still needs bodies (score >= scoring.voodoo_pullup_min). Advisory — leaders
    confirm; everyone else stays in Voodoo, a home clan that's never emptied."""
    cands = [p for p in scored_voodoo
             if p["score"] is not None and not p["review"] and p["score"] >= min_score]
    cands.sort(key=lambda p: -p["score"])
    return cands


def apply_roster_constraints(placed: list[dict], config: dict) -> list[dict]:
    """DEFERRED (rotator max / co-leader min). Data is already carried on each
    player: role == 'Co' (co-leader) from the roster sheet, and group containing
    'War Rotation' (rotator) from the signup sheet. Hook only for now."""
    # TODO: enforce config['constraints'] max_rotators / min_coleaders here.
    return placed
