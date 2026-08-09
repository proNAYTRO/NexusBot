/usage# CoC API Field Reference (surveyed live 2026-07-13)

Every documented GET endpoint on `https://api.clashofclans.com/v1`, called with real family
subjects (NAYTRO `#8Q82LPJPR`, KreweOfHydra `#GGRY80R0`, PineappleSxpres `#2LQYLVQ82`), recording
what each actually returns — including fields and endpoints the portal docs don't spell out.
Consult this before guessing what the API can or can't do.

**General rules observed**
- Auth: `Authorization: Bearer <key>`; key is IP-whitelisted (local dev vs Wispbyte need separate keys). Wrong IP → `403 accessDenied`.
- Tags must be URL-encoded (`#` → `%23`).
- List endpoints return `{items: [...], paging: {cursors}}` and accept `?limit=` (exception below).
- Timestamps come as `20260712T040906.000Z` (normalise via `capture.py`'s `_parse_time()`).
- Errors: `400 badRequest` (params), `403 accessDenied`/`accessDenied.invalidScope`, `404 notFound`.

## players

| Endpoint | Returns |
|---|---|
| `/players/{tag}` | Full profile: TH/BH levels, trophies (+best), warStars, attack/defense wins, donations, capital contributions, `clan{}`, `leagueTier{}` (new ranked league, e.g. "Legend III"), `builderBaseLeague{}`, **`currentLeagueGroupTag` / `currentLeagueSeasonId` / `previousLeagueGroupTag` / `previousLeagueSeasonId`** (ranked group pointers), `warPreference`, heroes/troops/spells/heroEquipment (with levels), `achievements[]`, `labels[]`, `playerHouse` |
| `/players/{tag}/battlelog` | Last **50** battles, attacks **and defenses**: `battleType` ('ranked'/'homeVillage'), `attack` (bool), **`armyShareCode`** (attacker's army — on defenses that's the enemy's army; wraps into `link.clashofclans.com?action=CopyArmy&army=...`), `opponentPlayerTag`, `stars`, `destructionPercentage`, loot arrays. **No timestamps, no opponent names, no order field** (list order = newest first) |
| `/players/{tag}/leaguehistory` | One row per past **weekly** ranked season: `leagueSeasonId` (unix ts, step = exactly 604800), `leagueTrophies`, `leagueTierId`, **`placement`** (final place in the ~100-player group — the only place the API exposes a finished-season rank), attack/defense W/L, `defenseStars`, `maxBattles` (18). Quirks: `attackStars` always 0 (unpopulated), `defenseWins` always 0 while `defenseLosses` is real — treat both W-side defense fields as unreliable |
| `POST /players/{tag}/verifytoken` | Not exercised (write op). Verifies the in-game API token — the standard ownership-proof flow if account verification is ever wanted for `/nexlink` |

## clans

| Endpoint | Returns |
|---|---|
| `/clans/{tag}` | Profile incl. `warWins/Ties/Losses`, `warWinStreak`, `isWarLogPublic`, `warLeague{}`, `capitalLeague{}`, `memberList[]` (each with `leagueTier`, trophies, donations, `townHallLevel`), `clanCapital{}` districts, labels, chat language |
| `/clans/{tag}/members` | `memberList` as its own paged list |
| `/clans?name=...` | Clan search; needs at least one criterion (name min 3 chars); many filter params (minMembers, warFrequency, locationId, labels...) |
| `/clans/{tag}/warlog` | War **summaries** only: `result` ('win'/'lose'/'tie'), `endTime`, `teamSize`, `attacksPerMember`, `battleModifier`, per-side stars/destruction/XP. No per-attack data. 403 if `isWarLogPublic` false |
| `/clans/{tag}/currentwar` | Full live war: `state` ('notInWar'/'preparation'/'inWar'/'warEnded'), members with `attacks[]` (stars, %, order, duration). Member with no attacks yet has no `attacks` key. 403 if war log private |
| `/clans/{tag}/currentwar/leaguegroup` | CWL group + `rounds[].warTags`. **404 `notFound` outside the CWL window** (confirmed mid-month) — not an error, just no active group |
| `/clanwarleagues/wars/{warTag}` | Individual CWL war; war tags **persist permanently** (capture.py relies on this). Not re-tested in this survey (no live CWL; imported DB rows have numeric keys, not warTags) |
| `/clans/{tag}/capitalraidseasons` | Raid weekends: totals + `members[]` (`attacks`, `attackLimit`+`bonusAttackLimit`, `capitalResourcesLooted`) + `attackLog[]`/`defenseLog[]` with per-district destruction |

## leagues

| Endpoint | Returns |
|---|---|
| `/leagues` | 23 **legacy** trophy leagues (Bronze III → Legend) with icons — pre-rework system, still served |
| `/leaguetiers` | **37 new ranked tiers** (Unranked, Skeleton → Legend; e.g. 105000034 = Legend III) with icons; `/leaguetiers/{id}` for one |
| `/leaguegroup/{groupTag}/{seasonId}?playerTag=` | **The ranked weekly group** (group tag + season id from the player payload). `playerTag` is **required** (400 without). Returns all ~100 `members` (name, clan, `leagueTrophies`, attack/defense win+lose counts — **no rank field, list unordered**; compute place by sorting) + the queried player's `attackLogs[]`/`defenseLogs[]`. No other endpoint resolves a group tag (all guessed variants 404) |
| `/leagues/29000022/seasons` | **133 season ids**: monthly `2015-07`... then **`v2-2026-07-06T05:00:00Z`-style weekly ids** after the rework — the archive continued into ranked mode |
| `/leagues/{id}/seasons/{seasonId}` | Global season leaderboard. **`limit` must be 100–25000** (400 below 100). Works for old monthly and new `v2-` ids; some transition ids (e.g. `2025-09`) return empty items. Only meaningful for Legend (29000022) |
| `/warleagues`, `/builderbaseleagues`, `/capitalleagues` (+`/{id}`) | id + name only (war/capital have no icons) |

## locations

| Endpoint | Returns |
|---|---|
| `/locations` | Countries + regions; `/locations/{id}` needs a **numeric** id (global = `32000006` "International") |
| `/locations/global/rankings/...` | The literal token `global` **is** accepted in rankings paths (only there): `players` (top by `trophies`, incl. `leagueTier`, `rank`/`previousRank`), `clans`, `players-builder-base`, `clans-builder-base`, `capitals` |

## goldpass / labels / esports

- `/goldpass/seasons/current` → `startTime`/`endTime` only — no reward/points data.
- `/labels/players`, `/labels/clans` → label ids/names/icons.
- `/esports*` → **403 `accessDenied.invalidScope`** on a normal key — gated to special key scopes; not usable.

## What the official API does NOT give

- **No historical regular-war detail** — `warlog` is summaries only; a war's attacks are gone once `currentwar` rolls over (hence the 30-min capture loop + `raw_json` column). CWL wars are the exception (permanent war tags).
- **No per-player war attack history** — that's why `/nexstats` and `/nexroster` use ClashKing (`api.clashk.ing`, `/player/{tag}/warhits`, 100-record cap).
- **No timestamps or opponent names in battlelog**; only the last 50 battles.
- **No live rank inside a ranked group** — compute it from `/leaguegroup` members (finished seasons: `placement` in `leaguehistory`).
- **No army/base contents** beyond opaque `armyShareCode` strings (no unit-id mapping served); no base layouts.
- **No push/webhooks** — poll only.
- Legend-archive `attackStars`/`defenseWins` in `leaguehistory` are unpopulated; some transition season ids are empty.

## Undocumented-but-real extras (portal omits or under-describes)

- Player payload's `currentLeagueGroupTag`/`currentLeagueSeasonId` + `/leaguegroup/...` together enable live group standings (backs `/nexleague`).
- `battlelog`'s defense entries carry the **attacker's** army code (backs `/nexbattles`' steal-the-army button).
- `leaguehistory.placement` = final weekly placement — free historical ranked results, no polling needed.
- `v2-` weekly ids in the Legend season archive = global ranked leaderboards per week.
