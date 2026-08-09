# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

KreweNexus is a Discord bot for a multi-clan Clash of Clans (CoC) family. Its jobs are:
1. **Player/clan linking** — linking Discord users to CoC player tags and displaying player/clan info.
2. **War capture** — capturing war attack data for all 15 family clans into a local SQLite database (`cogs/cwl/capture.py`). Player-facing stats commands (`/nexcwlstats`) and the CWL dashboard (`/nexcwldash`, `/nexcwlleaders`) were removed and are being rebuilt from scratch — see "Planned / Not Yet Built".

The old roster-allocation system (ClashKing API, xlsx fixtures, allocator, scoring) has been scrapped and will be redesigned from scratch at a later point.

## Running the Bot

```
pip install -r requirements.txt
python main.py
```

Requires a `.env` file with:
- `BOT_TOKEN` — Discord bot token
- `COC_API_KEY` — CoC developer API key (host IP must be whitelisted on the CoC developer portal)

There is no automated test suite. To test war capture: run `!nexcapture` while a war is active, then inspect `data/war_data.db` directly or check `!nexstatus` for per-clan capture health.

## Architecture

**CoC API field reference:** `docs/coc_api_reference.md` — full live-surveyed map (2026-07-13) of
what every official endpoint returns and doesn't (undocumented fields, hard limits, gaps). Check
it before assuming the API can or can't provide something.

### Cog Loading

`main.py` auto-discovers every `.py` file in `cogs/` that isn't prefixed with `_` and loads it as a `discord.py` extension. Slash commands are synced on `on_ready`. Adding a new cog requires no registration — just drop the file in the right `cogs/` subdirectory.

### Command Types

Cogs use two patterns:
- `@commands.command` — prefix commands (e.g. `!nexcapture`), require `message_content` intent
- `@app_commands.command` — slash commands (e.g. `/nexclan`), use `discord.Interaction`

### Data Flow

```
cogs/cwl/capture.py  (tasks.loop every 30 min  +  !nexcapture manual trigger)
  → CoC API: /clans/{tag}/currentwar                     (regular wars)
  → CoC API: /clans/{tag}/currentwar/leaguegroup         (CWL group + rounds)
  → CoC API: /clanwarleagues/wars/{warTag}               (individual CWL wars)
  → utils/db.py: upsert_war() + upsert_attack()
      → data/war_data.db  (SQLite)

  End-of-war scheduling: after each 30-min cycle, active regular wars ending
  within the next 30 min get a one-off asyncio.Task that fires 2 min after
  war end to capture all final attacks in warEnded state.
```

Stats-facing commands (`/nexcwlstats` in the former `cogs/cwl/stats.py`, and the
`/nexcwldash` / `/nexcwlleaders` dashboard in the former `cogs/cwl/dashboard.py`)
were deleted along with their `utils/db.py` query functions — capture is now the
only piece of the CWL pipeline. See "Planned / Not Yet Built".

### Persistence

Four files in `data/`:
- `data/war_data.db` — SQLite; all captured war + attack records (created automatically on first run)
- `data/linked_accounts.json` — `{ discord_user_id: [coc_tag, ...] }`, managed by `cogs/clash/player.py`
- `data/clan_tags.json` — saved clan tags for `/nexclan` autocomplete, managed by `cogs/clash/clan.py`
- `data/capture_status.json` — per-clan capture health (last checked / last success / last error for regular + CWL), managed by `cogs/cwl/capture.py`, viewable via `!nexstatus`

**DB sync rule:** Never overwrite the remote `data/war_data.db` with a local copy after the initial setup — the remote bot writes live captures continuously. Only upload the DB for a full reset or first-time setup.

**Remote access constraint:** Wispbyte (Pterodactyl) only exposes SFTP (file transfer) for this server — there is no shell/SSH command execution available, despite the account-level page being labeled "SSH Keys" (that's just public-key auth *for* the SFTP endpoint). `utils/import_clashperk.py` cannot be run remotely via SSH as a result; historical imports on the live bot must go through an in-Discord admin command instead (planned — see "Planned / Not Yet Built").

### Database Schema (`utils/db.py`)

**wars** — one row per unique war
- `war_key` — unique identifier: `{clan_tag}|{start_time}` for regular wars, `{warTag}` for CWL, numeric string for ClashPerk imports
- `family_clan_tag` — which of the 15 family clans this record was captured through
- `war_type` — `'regular'` or `'cwl'`
- `cwl_season` — e.g. `'2026-07'` (NULL for regular wars); `cwl_round` — 1–7 (NULL for regular wars)
- `season` — `'YYYY-MM'`, populated for **every** war (mirrors `cwl_season` for CWL, derived from `start_time` for regular wars) — one consistent column to filter/group by regardless of war type, indexed (`idx_war_season`)
- `iso_week` — e.g. `'2026-W27'`, ISO calendar week derived from `start_time` — for "this week"/"by week" queries without repeating date math per-query
- `raw_json` — the full CoC API war payload, verbatim, for wars captured live going forward (NULL for ClashPerk-imported rows, whose xlsx export doesn't carry it). Since the CoC API has no historical-war endpoint, this is the only way a field nobody extracted into a column yet (troop/spell composition, attack duration, war league id, ...) can ever be recovered later — by re-parsing already-stored history, not by re-fetching data that no longer exists.
- `season`/`iso_week` are derived **inside `upsert_war()`** from the `start_time` it's given — callers never compute or pass them, so they can't drift out of sync between `capture.py` and `import_clashperk.py`.
- Indexed on `start_time`, `season`, `family_clan_tag` (plus the existing `war_type`) — date-range and season/clan filters no longer do a full table scan.

**attacks** — one row per attack, both sides of the war stored
- Foreign key to `wars.id`
- Stores attacker TH, defender TH, stars, destruction %, attack order, and `is_fresh` (True if first attack on that defender base in the war)
- `UNIQUE(war_id, order_idx)` — `INSERT OR IGNORE` makes re-captures and re-imports safe

**Query layer philosophy:** a new command needing a new question answered should rarely need new schema. `fetch_attacks()`/`fetch_wars()` are general-purpose, all-filters-optional functions (clan, player, war type, season, date range, defender TH) — prefer composing a query from these and aggregating in Python over writing a new bespoke SQL function. Keep dedicated SQL functions only for genuinely hot aggregates (leaderboards: `query_cwl_top_attackers`, `query_cwl_clan_attackers`) where pulling every row into Python would be wasteful. `query_player_vs_th`, `get_player_info`, `war_count`, `get_latest_cwl_season`, `get_current_cwl_season`, `get_cwl_seasons`, `query_cwl_season_summary` were restored (they'd been deleted along with `/nexcwlstats`/the dashboard, then brought back since those commands are being rebuilt). `get_seasons()` (distinct `season` across all wars, not just CWL) was added new to back `/nexstats`'s season-since autocomplete.

### Capture Task (`cogs/cwl/capture.py`)

Polls all 15 family clans every 30 minutes automatically:
- **Regular wars**: fetches `/currentwar`, skips if `notInWar` or private war log (403)
- **CWL**: fetches league group → iterates rounds → fetches each `warTag` individually
- CWL war tags are deduplicated across clan iterations so the same war isn't stored twice
- Manual trigger: `!nexcapture` (admin only)
- **Health status**: every cycle writes per-clan `last_checked` / `last_success` / `regular_error` / `cwl_error` to `data/capture_status.json`; view with `!nexstatus` (admin only) to spot a silently broken capture loop (expired API key, IP rotation, rate limiting) without digging through logs.
- **End-of-war scheduling**: `_fetch_regular` returns `(log_message, end_time, state)`. After each cycle, any `inWar` war ending within `END_OF_WAR_WINDOW` (30 min) gets a scheduled `asyncio.Task` (`_end_of_war_capture`), deduped per clan+end-time so repeat cycles don't double-schedule. The task sleeps until `END_OF_WAR_DELAY` (2 min) after war end, then re-fetches `/currentwar` for a final capture in `warEnded` state. Tasks are cancelled on `cog_unload`. Prevents last-minute attack loss. CWL is exempt (war tags persist permanently in the API, so a later poll can never lose CWL data).

**Timestamp normalisation**: CoC API returns `20260627T043543.000Z` — `_parse_time()` normalises this to `2026-06-27T04:35:43` so all times in the DB are consistent ISO strings and season filters work correctly for both live-captured and imported data.

### Historical Import (`utils/import_clashperk.py`)

Bulk-loads ClashPerk xlsx attack-log exports into the DB. Run from project root:

```
python -m utils.import_clashperk <regular.xlsx> <cwl.xlsx>
```

- Both files required (both args are read the same way — see note below on ClashPerk's export). Clans not in `FAMILY_CLANS` are silently skipped.
- `INSERT OR IGNORE` — safe to re-run without creating duplicates.
- War keys for imported rows are numeric strings (ClashPerk's internal WarID), no collision with live capture keys.
- **Bulk-write path**: `main()` opens one connection (`open_connection()`) and shares it across every `upsert_war()` call, then writes all attacks in a single `bulk_upsert_attacks()` `executemany()` call, instead of one connection per row. `bulk_upsert_attacks()` returns the actual number of *newly* inserted rows (via `conn.total_changes` delta) since `INSERT OR IGNORE` makes a raw row-count otherwise overstate what happened on a re-run.
- `_load_file()` accepts a path or an in-memory file-like object (e.g. `io.BytesIO`), so it also works from a Discord attachment, not just a filesystem path — this backs the planned in-Discord import command (see "Planned / Not Yet Built"), since Wispbyte has no shell access to run the CLI form remotely.
- No cross-source dedup: a single ClashPerk export uses one consistent war-numbering scheme for itself, and `upsert_war`'s `ON CONFLICT(war_key)` already makes re-running the same export safe. (An earlier `find_similar_war` helper existed for merging ClashPerk data into a *live, unwiped* DB under a different `war_key` scheme — removed once the plan became "wipe and reimport fresh" instead.)
- **ClashPerk's "Regular" and "CWL" attack-log exports are the same combined dataset** — both contain every war type; `War Type` (column 24, `'normal'`/`'cwl'`) is what actually distinguishes them, per-row, not the file/button used to export. Confirmed 2026-07-08: two separately-downloaded exports had different file hashes but identical row counts, date ranges, and War Type distribution per sheet. Passing both to `main()` is harmless (`INSERT OR IGNORE` absorbs the full duplicate second pass) but processes ~2x the rows for no benefit — fine at this project's scale (112k rows re-processes in well under a second), but worth knowing if it's ever slow.
- **2026-07-08 fresh full import**: both local and remote `data/war_data.db` were wiped and rebuilt from a ClashPerk export spanning 2024-02-03 through 2026-07-07 (all 15 clans) — **745 wars, 56,308 attacks**. This also happens to cover the 2026-07-01–08 capture gap from the bot's earlier downtime. The remote bot is capturing new data on top of this going forward.
- **Known gap: `family_stars`/`opp_stars`/`family_destruction` are always NULL on ClashPerk-imported wars.** ClashPerk's attack-log export is per-*attack*, not per-*war-result* — there's no "final war score" column in it. Only live capture (which reads the CoC API's war summary directly) populates those three columns. Anything wanting a win/loss/tie result for an imported war currently has none to read (confirmed via `/nexstats` — see below). Fixable later by deriving it from the attack rows themselves (best attack per defender base, summed per side), but not done as part of this import.

### Ranked League Standing (`cogs/clash/league.py`)

`/nexleague` (open to all users, **public** response) shows a player's place in their Ranked
(weekly league tournament) group — group rank, league trophies, attack/defense W/L, clan.
Defaults to the caller's linked accounts, with the same in-place account-switcher dropdown
pattern as `/nexprofile` (reuses `player.py`'s `_account_select_options`, TH emoji per option;
group payloads are cached per view so switching back is free). An optional `tag` parameter looks
up any player tag instead (single card, no dropdown), bypassing linked accounts entirely.

- **Endpoint**: `GET /leaguegroup/{groupTag}/{seasonId}?playerTag={tag}` — group tag and season
  id come from the player payload's `currentLeagueGroupTag` / `currentLeagueSeasonId` (added with
  the Oct 2025 ranked-mode update). `playerTag` is **required** (400 without it); the response is
  the full ~100-member weekly group (`members`) plus the queried player's `attackLogs`/
  `defenseLogs` (not yet displayed). Discovered 2026-07-13 — no other path resolves the group
  tag (`/leaguegroups/...` and every other guessed variant 404s; only `/leaguetiers` exists
  besides this).
- **Rank is computed here** (`1 + count of members with strictly more leagueTrophies`) — the API
  returns members unordered. Ties share a rank, so on season reset day everyone is `#1 of 100`.
- `currentLeagueSeasonId` is a unix timestamp of the season start; rendered as `<t:...:D>` in the
  footer (guarded by a plausibility check in case Supercell ever changes it to an opaque id).
- Accounts not signed up for Ranked (no `currentLeagueGroupTag`) get a per-account "isn't placed
  in a Ranked group this season" body instead of an error — the switcher keeps working.
- **Legend I has no weekly group**: the API marks it with sentinel group tag **`"#0"`**
  (querying `/leaguegroup/#0/...` 404s) — those players compete in one global pool. The command
  detects the sentinel and instead looks the player up in
  `/locations/global/rankings/players?limit=200`, showing "Global Rank #N" (or "outside top
  200"), trophies from the player payload, and no W/L (that data only exists in group payloads).

### Battle Log (`cogs/clash/battles.py`)

`/nexbattles` (open to all users, **public** response) is a paginated browser over a player's 50
most recent battles — attacks **and defenses**, ranked and casual — with a **Copy Army** link
button per battle (`link.clashofclans.com?action=CopyArmy&army=<armyShareCode>`; on defense
entries the code is the *attacker's* army, so it's a "steal the army that beat you" button —
same never-expiring link-button mechanism as `/nexbasepost`'s Copy Layout). Subject resolution
and account-switcher dropdown are identical to `/nexleague` (defaults to the caller's linked
accounts; optional `tag` param looks up anyone). `type` (All/Ranked/Casual) and `side`
(All/Attacks/Defenses) filters are applied in-view; the footer echoes page count, battle count,
and active filters.

- **Endpoint**: `GET /players/{tag}/battlelog` (undocumented, discovered 2026-07-13) — returns
  `{items: [50]}` with `battleType` ('ranked'/'homeVillage'), `attack` (bool), `armyShareCode`,
  `opponentPlayerTag`, `stars`, `destructionPercentage`, and loot arrays. **No timestamps and no
  opponent names** — entries are rendered newest-first by list order with opponent tags only
  (names would cost 50 extra player fetches).
- 5 battles per page, each a `Section` with the link button as accessory; battlelog responses
  are cached per tag in the view (same pattern as `/nexleague`'s group cache).
- `armyShareCode` comes in both the new format (with heroes/pets/equipment, `h...p...e...`) and
  the old units-only format (`u34x55...`) — the deep link wraps either verbatim.

### Data Verification (`cogs/cwl/verify.py` + `utils/clashking.py`)

`/nexstats` (open to all users, slash command, modeled on ClashPerk's own `/stats attacks`; the
cog file is still `verify.py` and the command was renamed from `/nexverify` on 2026-07-11) —
takes no tag input; it auto-resolves its subject plus a wide set of optional filters, and
always renders a ClashPerk-style ranked attack success-rate table (`#`/`TH`/`RATE%`/`HITS`/`NAME`)
— one row per linked account (or per clan member, if `clan` is given). Response is **public**
(visible to the whole channel, not ephemeral) and the table columns are kept narrow
(`_short_name()` truncates long in-game names to 12 chars) so it fits a mobile screen's
embed width without forcing horizontal scroll.

**Sourced entirely from ClashKing's global war history, not the local capture DB** (changed
2026-07-11 — this cog used to read `utils/db.py`'s `fetch_attacks()`; a twin admin-only command,
`/nexverifyman`, was built alongside it to prove ClashKing's numbers were trustworthy before
switching over, then the two were merged back into this one command once confirmed. `/nexverifyman`
no longer exists). Local capture was found to be missing CWL windows the roster metric depends on
(see "Roster Allocation" below), so there's no local fallback here either — same one-source
decision the roster command already made.

- **Subject resolution** — no `tag` parameter; three ways to pick a subject, checked in this order:
  1. **`clan`** (autocompletes over the 15 `FAMILY_CLANS`, not the separate
     `data/clan_tags.json` saved-clans list from `/nexclan`) — fans out to the clan's *current*
     members (live CoC API roster fetch) and keeps only attacks made on that family clan.
  2. **`user`** (a Discord member picker) — shows *that member's* linked accounts,
     looked up the same way as below but keyed on their Discord ID instead of the caller's.
  3. Default (no `clan` or `user` given) — looks up the caller's own tags from
     `data/linked_accounts.json` (via `cogs/clash/player.py`'s `load_data()`) and pulls
     ClashKing war history for every account they've linked with `/nexlink`; if the resolved
     target (self or `user`) has no linked accounts, it says so instead of erroring.
- **Scope filters**: `war_type` (regular/CWL/both), `season` (autocompletes from
  `get_seasons()` — still local-DB-sourced, since it's just a season-name list, not attack data
  — as "Since Jul 2026"-style suggestions; picking one filters to wars *since* the start of that
  month), `days` (last N days), `wars` (last N wars — wins over `season`/`days` if given).
- **Attack filters**: `defender_th`, `compare` (attacker-vs-defender TH matchup: all/equal/+1/+2),
  `attempt` (fresh first-hit vs cleanup repeat, via the `is_fresh` column),
  `min_stars` (the star threshold that counts as a "hit" in the rate — default 3, matching
  ClashPerk), `filter_farm_hits` (excludes 1★/<50% destruction attacks), `family_only`
  (player/`user` mode only, default **on**: restricts to wars played in a family clan; turn off
  to see an account's full ClashKing history — e.g. a fresh transfer's play at their old clan).
  **`filter_loot_hits` (excludes attacks on a base a teammate already 3-starred) no longer
  exists** — it needs the full war's cross-teammate attack order, which ClashKing's per-player
  feed doesn't expose, so the filter was dropped entirely rather than kept as an always-erroring
  option when the local-DB version (which could compute it) was retired.
- Embed footer mirrors ClashPerk's own: `War Type: <label> (<scope label>, <N> wars)`,
  where `<N>` is the count of distinct wars actually contributing to the shown rows.
- **`utils/clashking.py`** — thin async client, public API (`https://api.clashk.ing`, no auth).
  `fetch_player_warhits()` hits `GET /player/{tag}/warhits`; `warhits_to_attacks()` maps each
  record to the same attack-dict shape `fetch_attacks()` used to return, so the leaderboard
  renderer and filter helpers didn't need to change shape when the source switched. `utils/`
  stays cogs-free (tag/timestamp helpers duplicated there on purpose; the family-clan set is
  *injected* via `family_clans=` rather than imported from `cogs/`).
  **Side attribution (fixed):** ClashKing orients each war's `clan`/`opponent` by the war's own
  CoC ordering, **not** by the queried player, and exposes no member lists — so the queried
  player's own clan can sit on *either* side. `warhits_to_attacks()` sets `family_clan_tag` to
  whichever side is a family clan (via the injected `family_clans` set), not blindly to
  `war_data.clan.tag` — getting this wrong (an earlier bug) mislabeled every war where the
  player's clan happened to land on `opponent` as "outside the family". `war_id` is likewise
  made side-order-independent (CWL `warTag`, else sorted clan-pair + start time) so one war
  dedupes across accounts/sides. Attacker fields come from `member_data` — an `attacks[]` record
  carries a nested `defender` but no nested `attacker`.
- **Known caps/gaps:** (1) the `warhits` endpoint **hard-caps at 100 records** (most-recent
  first) regardless of `limit` — so all-time / old-season windows under-count for a very active
  player; recent windows (last N wars/days, recent season) aren't affected in practice. (2)
  ClashKing dates regular wars ~1 prep-day earlier than the old ClashPerk-imported rows used to
  (a legacy note from the local-DB era — doesn't affect ClashKing-vs-ClashKing consistency now).
- The leaderboard renderer is the **module-level** `build_leaderboard_layout()` in `verify.py`
  (not an instance method; returns a Components V2 `LayoutView`, converted from a classic
  `Embed` on 2026-07-12) — kept module-level from when a second cog needed to import it
  byte-identically; no longer load-bearing now that there's only one caller, but harmless to
  leave as-is.

### Roster Allocation (`cogs/cwl/roster.py` + `utils/roster_scoring.py` + `utils/roster_parser.py`)

`/nexroster` (**admin-only**, **public** response) recommends a CWL roster allocation from uploaded
ClashPerk signup exports. It automates the performance-judging + tiered bucketing leaders do by hand;
output is a **recommendation they confirm/adjust**, never a final auto-roster.

- **Data source: ClashKing ONLY — the local `war_data.db` is deliberately NOT used here.** Verified
  2026-07-09 that the captured DB was missing the June/July CWL wars the metric depends on (Peak read
  2/2 locally vs 33/33 in ClashPerk; ClashKing had the full window). So scoring reads exclusively from
  `utils/clashking.py`'s `/player/{tag}/warhits`. `utils/roster_scoring.py` stays sync/cog-free — the
  cog fetches warhits (async), maps them with `warhits_to_attacks` (prior-month window), and passes the
  rows into `score_player`.
  - **Scoring counts ALL of a player's wars, not just family-clan ones** (changed 2026-07-09). ClashPerk's
    `TH Any vs 18` judges a player across *every* clan they warred in, so family-only scoring badly
    under-counted anyone who did their prior-month wars in an outside/feeder clan — or any transfer only
    now joining the family (the whole future-transfer case the roster exists for). Diagnosed against the
    friend's May-2026 ClashPerk screenshots: family-only left Ninja Night at 7/7 and Johnny at 0/0 (→
    review) because their regular wars were in non-family clans; all-wars gives 19/19 and 11/11, matching
    ClashPerk's 17/20 and 6/7. Players who war inside the family are unchanged. `warhits_to_attacks` is
    still passed `family_clans` for correct side attribution / war-id dedup — it just no longer filters
    the set (the `[a for a … if family_clan_tag in fam]` line in `_score_signups` was removed). Residual
    small gaps (e.g. EnnoX 18/19 vs ClashPerk 21/23) are ClashKing genuinely having a few fewer wars than
    ClashPerk — not fixable without ClashPerk itself, and they don't move the ranking. The local
    `war_data.db` was also checked as a source and rejected again: its 2026-07-08 ClashPerk import holds
    only ~9 CWL wars for all of May (CWL never imported cleanly) and is family-only capture, so it can't
    see transfers' outside-clan play either.
- **Metric** = Beta-smoothed 3★ rate vs TH18 (`score = round(100*(triples+2)/(attempts+4))`), the exact
  `TH Any vs 18` number ClashPerk shows. Smoothing was calibrated against real July rosters: 1–2 attack
  "100%" players correctly sink instead of topping the list; `<4` vs-TH18 attempts → **⚠ review** (never
  auto-placed). "20–21 CWL stars in one season" → **★ move-up flag** (best of the most-recent-7 CWL
  attacks, capped 21) — surfaced for leaders to act on, not a hidden score bump.
- **Allocation** = rank-and-fill-to-capacity. `mode=top-pool` merges the Pine+Krypto+Hydra signup files
  into one pool, ranks by score, and fills the flagship down through droppable overflow (Titans/Chaos/
  Acadia) by capacity; the bot **suggests** which clans run + 15/30 sizes from headcount (override via
  the `size` param). The **`clans` param** (comma-separated, autocompletes over the family clans by short
  name/3-letter code/tag — mains and manual clans excluded from the picker) makes the running set
  explicit: the **auto-run mains (Krypto/Hydra) always run**, and picking clans **replaces the
  headcount-driven overflow auto-pick** with exactly the chosen clans (in listed order → fill priority).
  Omitting it keeps the auto behavior. Unrecognised tokens are warned about and ignored (all-invalid → a
  hard error listing valid clans); the header's running-clans line echoes the final set as confirmation.
  Implemented in `suggest_running_clans(..., include=...)`; `include=None` = auto, `include=[tags]` =
  explicit (empty list = mains only). **Pine is never auto-allocated** — it's in the config's new `manual`
  list (see below), so no path (auto overflow *or* an explicit `clans` pick) can fill it; its roster is
  always hand-uploaded via `pine_assignment`. `mode=voodoo` keeps everyone in Voodoo (a home clan, never emptied) and only flags
  standouts (`score ≥ voodoo_pullup_min`) as pull-up candidates. Leaders finalize the borderline picks
  and equal-tier Krypto↔Hydra body-balancing (which no data can decide).
- **Live league** is read per running clan from the CoC API (`warLeague`, reusing `clan.py`'s
  `WAR_LEAGUE_EMOJI`) for display; fill order drives allocation and the fetch is best-effort (falls back
  to config order, so a missing/unwhitelisted CoC key still works).
- **Config** `data/roster_config.json` — `top_pool`/`manual`/`overflow`/`voodoo` tags, league→rank ladder
  (rank only orders clans; there are **no hard score floors** — they underfilled the flagship), scoring
  constants. `manual` (default `[Pine]`) lists hand-picked clans that are **never auto-allocated**: Pine
  stays in `top_pool` for family-set membership + signup parsing, but `suggest_running_clans` folds
  `manual` into its exclusion so no path can fill it, and reads it via `config.get("manual", DEFAULT…)`
  so a pre-existing on-host config missing the key still drops Pine without regeneration. Set
  `manual: []` to opt a clan back into auto-allocation. Since `data/` is excluded from the SFTP sync, this file won't be on a fresh host —
  `load_config()` falls back to `DEFAULT_CONFIG` (in `utils/roster_scoring.py`) and **writes the file
  on first run**, so it self-heals and stays tunable. Edit the built-in default too when changing
  clans, or the remote will regenerate the old values if the file is ever deleted. **`roster_parser.py`** reads the export's stats sheet (selected by title, tolerant of the
  colour emoji) for the signup list + identity (name/TH/`Role`=`Co` co-leader/`Group`=War-Rotation
  rotator — data for the still-**deferred** max-2-rotator / min-2-coleader constraints), with a
  tag-regex fallback for non-xlsx. **`data/roster_state.json`** tracks players marked placed (the
  "📌 Mark these as placed" button) so a follow-up run can exclude them.
- **Manual-first clans (e.g. Pine):** Pine is hand-picked, **never auto-allocated** — it's in the config
  `manual` list, so `suggest_running_clans` drops it from the running set unconditionally (not just when
  the assignment is uploaded; a forgotten upload can no longer auto-fill Pine with the wrong players).
  Upload its finished *assignment* export via `pine_assignment` — the bot excludes those players from the
  pool (so they're not re-allocated elsewhere) and reads the clan tag via `detect_clan_tag`, so it
  allocates only the **leftovers** (Pine signups who didn't make the cut) across the remaining clans.
  Assignment exports parse identically to signups; excluded players are also persisted to
  `roster_state.json` so a later run drops them without re-uploading. This is the seam that lets the
  manual Pine step feed the automated rest.

### Base Layout Posts (`cogs/clash/bases.py` + `utils/base_pdf.py`)

`/nexbasepost` (**admin-only**, ephemeral review → **public** posts) turns a base-seller PDF into
ClashPerk-style layout posts. The family buys base packs from various builders (Atrasis, PINNACLE,
RHBB, …); each PDF has one base per page — a **screenshot**, a small caption (**name / builder /
recommended CC troops**), and the real **`action=OpenLayout` deep-link** embedded as a clickable
annotation. The command extracts all of that, lets the admin review/fix it privately, then posts
each approved base as an image + a `Name | Author | CC` title + a **Copy Layout** link button
(opens the base in-game). It automates the manual copy-paste-per-base the leaders did by hand.

- **Feasibility crux — the layout link must be a real embedded hyperlink, not painted into the
  screenshot.** A layout link can't be reconstructed from pixels. Verified across three real
  sellers (2026-07): all embed the links as plaintext URI annotations (not in compressed object
  streams), so `get_links()` recovers them verbatim. If a PDF has only base *images* and no
  `OpenLayout` links, that seller isn't supportable — the command says so instead of posting.
- **`utils/base_pdf.py`** *(cog-free, sync — mirrors `utils/roster_parser.py`)* — all PyMuPDF
  (`fitz`) extraction. `extract_bases(pdf_bytes) -> list[BaseCandidate]`. Per page: keep
  `get_links()` whose uri contains `openlayout`, **dedupe by the `id=` value** (sellers attach the
  same link to both the image and a button → ~2× annotations), pick the base **image** as the
  largest drawn image after excluding full-page backgrounds (PINNACLE lays bases over one) and
  logos/separators, and parse the caption. **Caption parsing is label-first** (`👷Builder:` /
  `💎Base Style:` / `🔰Clan Castle:` for PINNACLE & RHBB; `Base:N` + `Cc:`/`Recommended CC` for
  Atrasis) with a `Base N` top-title fallback for the name and CC-continuation-line merging (troop
  lists wrap). Standalone anchor words (`Link`, `Copy Base`) and social/URL lines are skipped.
  Offline probe: `python -m utils.base_pdf <file.pdf> [--dump=DIR]`. The link used for the button
  is always the annotation uri (complete), never the wrapped on-page URL text.
- **`cogs/clash/bases.py`** — Discord UI only. Built on **Components V2** (`discord.ui.LayoutView` +
  `Container`/`TextDisplay`/`MediaGallery`/`Separator`/`ActionRow`, requires discord.py ≥2.6 — see
  `requirements.txt`), converted from classic Embed+View on 2026-07-11 as this codebase's first
  Components V2 command. `BaseReviewLayout` is an **ephemeral paginated review** (◀▶ navigate, ✏️
  Edit via `BaseEditModal`, Skip/Include toggle, 📮 Post) — every base defaults to approved; the
  review is the safety net that makes the rules-based parser safe (fix a misparse before it's
  public). Posting sends each approved base to `interaction.channel` via `PostedBaseLayout`
  (`discord.File` image + `NEXUS_RED`-accented `Container` + a `ButtonStyle.link` **Copy Layout**
  button). **New patterns for this codebase**: `discord.ui.Modal`, sending image files
  (`discord.File` / `attachment://` — unchanged mechanism under Components V2, still referenced by
  filename, now via `MediaGallery.add_item(media=...)` instead of `Embed.set_image`), and
  Components V2 itself. Link buttons carry no `custom_id`, so they never expire or need
  persistence — the posted Copy Layout buttons survive bot restarts.
  **Components V2 constraint that shaped the design:** a message's `IS_COMPONENTS_V2` flag is set
  automatically the first time it's sent with a `LayoutView`, and is permanent — every later edit to
  that same message must also pass a `LayoutView` (never plain `content=`/`embed=`). That's why the
  paginated review (`BaseReviewLayout.refresh()`), each posted base (`PostedBaseLayout`), and the
  final "Posted N bases" status (`SummaryLayout`, replacing the review message once posting
  finishes) are three separate `LayoutView` subclasses rather than one embed mutated in place — the
  old code's plain-`content=` summary edit is impossible once the message already carries the flag.
  discord.py doesn't locally enforce the content/embed-vs-v2 exclusion (it's a Discord API-level
  rule), so this must be kept correct by convention in any future edit to this file.

### Server Management (`cogs/admin/roles.py`)

`/nexcopyperms` (**admin-only**, ephemeral) — the codebase's first Discord-server-management command
(nothing else touches roles or channel overwrites); lives in the new `cogs/admin/` package. Takes a
`channel` (any guild channel or category via the native picker), a `source` role, and a `target` role,
reads the source role's permission overwrite in that channel (`channel.overwrites_for`), and applies an
**identical clone** to the target via `channel.set_permissions(target, overwrite=...)` — any existing
target overwrite in that channel is replaced wholesale (the confirmation notes when that happened).
One channel per invocation; re-run for others. Guards: source == target, source has no overwrite there
(no-op, does NOT clear the target), bot missing **Manage Roles** in the channel (checked up front, and
`discord.Forbidden` from the edit is also caught — role hierarchy can still refuse). The edit carries an
audit-log `reason`. Requires the bot's server role to have **Manage Roles** — a guild permission, not an
intent, so `main.py` needed no changes.

### Custom Messages & Button Panels (`cogs/admin/messages.py`)

`/nexmsg` (group: `create`/`import`/`list`/`preview`/`delete`) + `/nexpanel` (all **admin-only**) —
the in-bot replacement for Discohook: saved custom messages posted as panels with **working buttons**,
where each button shows another saved message (ephemeral to the clicker). Built 2026-07-16 because
Discohook's equivalent (two webhooks, saved as backups, one linked into a button) was too clunky.

- **Saved messages** (`data/custom_messages.json`, name-keyed, `MAX_MESSAGES` 100): three kinds —
  `form` (authored via modal: title/body/image/color, defaults NEXUS_RED), `json` (classic
  content+embeds JSON), and `v2` (Discohook's **Components V2** export — `flags: 32768` +
  Container/TextDisplay/Separator/MediaGallery/Section/link-button dicts — rebuilt into a
  `discord.ui.LayoutView` at send time by `build_layout()`; imported non-link buttons are rejected
  with a pointer to the `buttons` param, since their custom_ids would be dead). Import accepts a
  raw message object or the full Discohook backup format, pasted in a modal or attached as `.json`;
  the kind is auto-detected. Reusing a name overwrites (form modal prefills for editing).
  `send_kwargs()` hides the classic-vs-V2 difference so channel sends, webhook sends, and ephemeral
  button replies share one path.
- **Button persistence** — the key design: buttons carry `custom_id="nexmsg:<saved name>"` and a
  single `on_interaction` listener resolves the name at click time. No view registration, no panel
  tracking — buttons survive restarts and deleting a message just makes its buttons say so.
- **Custom identity**: `/nexpanel as_name:`/`as_avatar:` posts through a bot-created webhook
  (`WEBHOOK_NAME` in the channel, created on demand — needs **Manage Webhooks**). Application-owned
  webhooks are the only kind Discord allows components on, which is exactly why Discohook can't do
  buttons without their bot. Default (no identity args) posts as the bot directly.
- Buttons param is a compact spec: `War Rules=war-rules, FAQ=faq` (max 5 = one action row,
  `ButtonStyle.danger` red to match the theme); parsed by `parse_buttons()` with validation against
  saved names.
- This cog introduced `app_commands.Group` to the codebase (the `/nexmsg` subcommands).

### Family Clans

Defined in `cogs/cwl/capture.py` as `FAMILY_CLANS` (tag → name) and mirrored as a set in `utils/import_clashperk.py`. Both must be kept in sync when adding clans.

| Tag | Name |
|---|---|
| `#2LQYLVQ82` | PineappleSxpres (main) |
| `#92QUCULV` | KryptoniteKrewe |
| `#GGRY80R0` | KreweOfHydra |
| `#2YRPL0PQ0` | KreweOfVoodoo |
| `#9QY9QJJQ` | KreweOfRoyals |
| `#2L9VVY0RL` | KreweOfTitans |
| `#8CUCUQ2L` | KreweOfSinners |
| `#Y880CVR0` | KreweOfChaos |
| `#22CJ9CRL0` | Krewe Of Acadia |
| `#RJPVQL9L` | Assassin'sGuild |
| `#2LJVPQJRQ` | Misfit Clashers |
| `#99CULVQP` | Loot Krewe |
| `#LP8RJGGC` | Krewe Fatale |
| `#LPP0VQ0L` | Krewe Esports |
| `#2J822R8UP` | Krewe Parking |

## Visual Theme

All embeds use a **black / red / white** color scheme:
- Embed color: `NEXUS_RED = 0xE8173A` (deep red — import or copy this constant into any new cog)
- Branding: every layout's `##` header line starts with the `nn_nexus` logo emoji — `f"## {emoji('nn_nexus')} {title}"` — one emoji per header (it replaces, not joins, any decorative leading emoji)
- Progress bars: `█` filled, `░` empty, wrapped in a code block
- No branding footer: the old `-# KREWE NEXUS` footer line was removed from every layout on 2026-07-12 — don't add it to new commands. Footers exist only where they carry real info (page counts in `/nexemoji` and the `/nexbasepost` review, war-type/timestamp in `/nexstats`)
- Avoid table-style layouts; use Discord embed fields with inline stats instead

## Key Constraints

- `!nexcapture`, `!nexstatus`, `/nexaddclan`, `/nexroster`, `/nexbasepost`, `/nexemoji`, `/nexcopyperms`, `/nexmsg`, and `/nexpanel` are restricted to `ADMIN_ID = 1048729773926522981` (hardcoded). `!nayteal` (reply to a sticker to clone it into the server — `cogs/admin/stickers.py`; needs the bot to have **Manage Expressions**) is open to all users. `/nexstats` is open to all users — see "Data Verification".
- All API calls are async (`aiohttp`). Keep any new data-fetching code async.
- The CoC API requires the caller's IP to be whitelisted — local dev and any hosted environment need separate API keys.
- SQLite only — no external DB, no extra hosting cost. File lives at `data/war_data.db`.
- `openpyxl` is in `requirements.txt` for the ClashPerk xlsx import script (`utils/import_clashperk.py`).
- `PyMuPDF` (`import fitz`) is in `requirements.txt` for base-pack PDF extraction (`utils/base_pdf.py`, backing `/nexbasepost`). Ships prebuilt wheels (no compiler needed) for both local and the Wispbyte host.

## Hosting / Deployment (Wispbyte)

- Sync code files via SFTP — exclude `data/`, `.env`, `__pycache__/`
- Remote bot needs its own `COC_API_KEY` with the remote server's IP whitelisted on the CoC developer portal
- `pip install -r requirements.txt` must run on the host after adding `PyMuPDF` (for `/nexbasepost`) — it's a prebuilt-wheel install, no compiler needed, but the cog won't import (`fitz`) until it's present
- Slash commands re-sync automatically on bot restart (`on_ready`)
- The remote DB (`data/war_data.db`) is self-maintaining — the bot captures every 30 min on its own. Do not overwrite it with a local copy after initial setup.
- No shell access on the remote host — see "Remote access constraint" under Persistence. Historical imports must go through an in-Discord command, not SSH.

## Planned / Not Yet Built

- **In-Discord import command** — `utils/import_clashperk.py`'s bulk-write path is done (see "Historical Import"), but there's still no way to run it against the *remote* DB, since Wispbyte has no shell access (SFTP only). Needs a `!neximport`-style admin command that accepts the xlsx files as Discord attachments and calls the same `_load_file()`/`upsert_war()`/`bulk_upsert_attacks()` path in-process on the running bot. A first draft of this existed earlier in the DB-redesign work and was intentionally stripped back out of `capture.py` pending this rebuild — re-add once the January-onward xlsx export is in hand.
- **Player/CWL stats commands** — `/nexcwlstats`, `/nexcwldash`, and `/nexcwlleaders` were deleted as part of a cleanup; their `utils/db.py` query functions were restored (schema unchanged, now with `season`/`iso_week`/`raw_json` added) and the commands themselves are pending a rebuild on top of them.
- **Roster allocation** — **built** as `/nexroster` (see "Roster Allocation"). Remaining: the deferred
  max-2-rotator / min-2-coleader constraint enforcement (data is already parsed — `Role`=`Co`,
  `Group`~`War Rotation` — via the `apply_roster_constraints` stub in `utils/roster_scoring.py`), and an
  optional interactive size/clan confirmation View (currently confirmed via the `size` param + re-run).
