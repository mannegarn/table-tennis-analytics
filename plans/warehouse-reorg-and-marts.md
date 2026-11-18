# Warehouse Reorganization & Marts Plan (`level_source`)

**Status**: Planning only — **do not execute code changes until approved.**
**Design spec**: `docs/database-design.md` | **Visual diagram**: `docs/drawdb-warehouse-design.json`
**Last revised**: 2026-11-18 after a staff-level review of the plan against the live database.

## Is This a Total Rewrite?
**No.** ~90% of existing code (Python scrapers, HTTP clients, unit/integration tests, CI, SQL unpack logic, and dbt SQL queries) stays intact. This is a **structured reorganization into `level_source` schemas/folders** plus deterministic surrogate keys (`event_sk`, `match_sk`, `player_sk`) and a much more careful match-cleaning step, broken into 7 small, independently testable chunks.

## Decisions Made

| Decision | Outcome |
|---|---|
| Walkovers, injury, retirement, disqualification (**default results**) | **Kept** in `fct_singles`, never dropped. Tagged with `result_type` + `is_default_result` + `defaulting_side`/`defaulting_player_sk` so we can analyse who gets or causes the most. Excluded from ratings by flag (`is_rating_eligible`), not by filter. |
| `LIVE` matches (11 rows captured mid-match) | **Excluded** from `fct_singles` and `fct_match_games`. Kept in `dim_matches` (`is_live = true`) so commentary foreign keys still hold. A warn-level dbt test reports the count. |
| Age category | **Added** as `age_category` (`senior`, `U11`…`U21`) and `is_youth`, parsed from `document_code`. |

Full rules are in `docs/database-design.md` §10 (Match Data Quality Rules). That section is the single source of truth. This plan only schedules the work.

## Fixes Found in Review (apply while executing)

Grouped by when they bite. Evidence is from queries against the live DB on 2026-11-18.

**Blockers (would fail or silently corrupt)**
1. **Duplicate dbt model names break Chunk 6.** dbt requires unique model names project-wide. `marts_wtt/dim_events.sql` and `marts/core/dim_events.sql` cannot both exist. Name the source files `wtt__dim_events.sql` etc. and set `config(alias='dim_events')`. Make `marts/core` **views** until a second league exists (otherwise 6.7M commentary rows are stored twice; Postgres prunes `UNION ALL` branches on a constant `source_system` filter).
2. **The covering index is missing on the live `fct_match_commentary` table.** On rebuild, dbt renames the old table to `__dbt_backup` but its indexes keep their names, so `create index if not exists idx_fct_commentary_match_action` in the post-hook sees the name taken and skips, then the backup is dropped. Use an unnamed index plus `analyze`:
   ```sql
   post_hook=[
     "create index on {{ this }} (event_id, document_code) include (action_type)",
     "analyze {{ this }}",
   ]
   ```
   Drop the plain `(event_id, document_code)` btree from `indexes=[...]`; the covering index already serves it.
3. **Singles filter was wrong.** `p1_player_sk`/`p2_player_sk NOT NULL` is also true for doubles. Filter on `sub_event_type like '%Singles'` **and** `comp_1_player_2_id is null and comp_2_player_2_id is null`. `competitorType` is `H` on every row so it cannot separate singles from doubles.
4. **`best_of` is NULL on all 22,600 detail rows.** `stg_match_details` reads `matchConfig.bestOf`; the real key is `bestOfXGames` (also `maxPointsPerGame`, `advantagePerGame`, `suddenDeathPoint`).
5. **`fct_singles` must not inner-join match details.** Only 22,600 of 201,847 match-detail rows (and about 11% of singles entries, in every age group) contain scores. Use `stg_match_entries` as the grain and left-join details, with `has_result`.

**Key design**
6. **WTT `player_id` is the ITTF ID.** Hash it as `'ittf|' || player_id`, not `'wtt|'`, using a dedicated `player_sk()` macro. Otherwise future league players with an ITTF ID never land on the same key.
7. **`player_sk` changes when identity is resolved.** Facts store a stable `source_player_sk` (hash of `source_system|source_player_id`) and look up `player_sk` via `int_player_identity_map`. Ratings are always recomputed from scratch. `player_identity_overrides.csv` points at `target_ittf_id` or a target source key, never a pasted hash value.
8. **`surrogate_key` macro casts every part to `::text`** so `123::bigint` and `'123'` hash the same. A NULL part yields a NULL key, caught by `not_null`. Add `unique` tests on every `_sk`.
9. **Commentary covers doubles and team matches too.** `fct_match_commentary.match_sk` therefore references a new `marts_wtt.dim_matches` (all disciplines, one row per `match_sk`), not `fct_singles`.

**dbt / Postgres**
10. **Heavy staging models stay `table`** (`stg_events`, `stg_event_sponsors`, `stg_match_entries`, `stg_match_details`, `stg_match_commentary`, `stg_wtt_players`). Their in-file `config()` overrides folder defaults. Do not "tidy" them into views: tests and downstream builds would re-expand the JSONB every time.
11. **sqlfluff's jinja templater cannot see custom macros.** Add to `.sqlfluff` before the first macro lands:
    ```
    [sqlfluff:templater:jinja]
    load_macros_from_path = dbt/macros
    ```
12. **dbt never drops old relations.** After Chunk 1, drop the stale `analytics.*` tables manually, or Polars may keep reading old copies.
13. **Rename dbt sources in Chunk 1, not Chunk 7.** Use `source('wtt', …)` and `source('ittf', …)`, both still `schema: ingest`. Chunk 7 then becomes a two-line YAML edit.
14. **Prefer `dbt build`** (or `seed` + `run` + `test`) so a failing test stops downstream models.
15. **`summary.py:139`** filters `nspname IN ('ingest','analytics')` and will miss new schemas.
16. **ADR 0007** says the dbt profile schema "must stay `analytics`". Write **ADR 0012** to supersede it (schemas become `staging_*`, `marts_*`, `intermediate`, `marts`, `seeds`).

---

## Chunk 1 — dbt Folder & Schema Reorganization (`level_source`)
**Goal**: Align dbt folders and Postgres schemas to `level_source` **without touching Python ingestion** (dbt still reads from existing `ingest.*` tables).

Files:
- `git mv` models (not copy):
  - → `dbt/models/staging/wtt/`: `stg_events`, `stg_event_sponsors`, `stg_match_entries`, `stg_match_details`, `stg_match_commentary`, `stg_wtt_players`
  - → `dbt/models/staging/ittf/`: `stg_ittf_players`
  - → `dbt/models/intermediate/`: `int_event_equipment_sponsors`
  - → `dbt/models/marts/wtt/`: `fct_match_commentary`
- Split the three `_*_staging.yml` files into `staging/wtt/_wtt__models.yml` and `staging/ittf/_ittf__models.yml`; move `_events_intermediate.yml` and `_matches_marts.yml` beside their models. Delete the old YAMLs. A model described in two YAML files fails to parse.
- Replace `dbt/models/sources.yml` with `_wtt__sources.yml` + `_ittf__sources.yml`; update all 7 `source('ingest', …)` calls.
- `dbt/dbt_project.yml`: per-folder `+schema` (`staging_wtt`, `staging_ittf`, `intermediate`, `marts_wtt`, `marts_ittf`, `marts`), `+materialized` (views for `staging/ittf`, `marts/ittf`, `marts/core`), `seeds: +schema: seeds`. Rewrite the stale header comment.
- Add `dbt/macros/generate_schema_name.sql` (return `target.schema` when no custom schema, else `custom_schema_name | trim`).
- Apply the post-hook fix (review item 2) to `fct_match_commentary.sql`.
- Remove `.gitkeep` files and empty domain folders.
- `src/tt_stats/summary.py:139`: include the new schemas.
- Makefile help text: "in the analytics schema" is no longer true.
- Write ADR 0012.
- One-off: drop stale `analytics.*` relations.
- **No change needed**: `dbt/tests/*.sql` (they use `ref()` by name) and `tests/integration/test_database_migrations.py` (checks migration-created schemas only).

**Checkpoint**: `make dbt-run`, `make dbt-test`, `make check` green; `\dn` shows the new schemas; `analytics` holds nothing.

## Chunk 2 — Surrogate Key Macros & `marts_wtt.dim_events`
**Goal**: First dimension mart with deterministic keys.
- `dbt/macros/surrogate_key.sql` — `('x' || left(md5(<source_system>::text || '|' || <part>::text …), 15))::bit(60)::bigint` (review item 8) — plus `player_sk()` (review item 6).
- `.sqlfluff` macro path (review item 11).
- `dbt/models/marts/wtt/wtt__dim_events.sql` (`alias='dim_events'`):
  - Deduplicate `stg_events` on `event_id` (latest `source_year`). Events **2226 and 2227** appear in both 2020 and 2021.
  - Normalize whitespace in `event_name` / `city` / `country`, drop `event_code`, guard `duration_days` when `start_date > end_date`.
  - `event_sk`, `source_system = 'wtt'`, `ball_sponsor` / `table_sponsor` from `int_event_equipment_sponsors`, flags `has_matches`, `has_match_details`, `has_sponsors`.
  - Events span 1926-12-06 to 2027-12-07. Future events legitimately have no matches (`has_matches = false`).
- `_wtt__marts.yml`: `unique`/`not_null` on `event_sk`; uniqueness on `(source_system, source_event_id)`.

**Checkpoint**: `make dbt-run SELECT=wtt__dim_events`, `make dbt-test`, `make check`.

## Chunk 3 — `marts_ittf.dim_player_profiles` & `marts_wtt.dim_players`
**Goal**: Master WTT player dimension enriched with ITTF profiles, ready for league players without an `ittf_id`.
- `marts/ittf/dim_player_profiles.sql` (view).
- `marts/wtt/wtt__dim_players.sql` (`alias='dim_players'`, table): `player_sk` (PK) + nullable `ittf_id`; `COALESCE` `hand`/`grip`/`style` from WTT then ITTF.
- 3,426 WTT players have no ITTF profile, so `hand`/`grip`/`style` will be NULL for them. That is expected.
- 5 exact (name, country) pairs map to different IDs (e.g. `LEE Yubin`, `LEE Daeun`, `LEE Junho` KOR; `Juan GONZALEZ` ECU). The identity map must **never** merge on name alone.
- Tests: `unique`/`not_null` on `player_sk`, `accepted_values` on `hand`/`grip`/`style`.

**Checkpoint**: `make dbt-run`, `make dbt-test`, `make check`.

## Chunk 4 — Match Staging Fixes (the hard cleaning)
**Goal**: Make match staging trustworthy before any fact table depends on it. Rules and rationale are in `docs/database-design.md` §10; this chunk implements them.

In `stg_match_details`:
- Fix `best_of` → read `matchConfig.bestOfXGames`; add `max_points_per_game`, `advantage_per_game`, `sudden_death_point` from `matchConfig`.
- Parse the overall-score suffix (`WO`, `INJ`, `RET`, `DSQ`) into `result_suffix` (text) and split the numeric part into `overall_games_1` / `overall_games_2`.
- Keep `result_status` (`OFFICIAL` / `LIVE`).
- Do **not** use `comp_1_scores` / `comp_2_scores` downstream. They disagree with `results_game_scores` on defaults (e.g. `0-3 INJ`: comp scores `5,0,0` vs game scores `5-11,7-11,0-11`). `results_game_scores` is the single source of truth.

In `stg_match_entries`:
- Parse from the fixed-width `document_code`: `age_category` (regex `^TTE[MWX](SINGLES|DOUBLES|TEAM)-+U([0-9]{2})`, group 2 → `U15` etc.; no match → `senior`), and `is_youth`. Note the trailing `8` in codes like `U158FNL` belongs to the round code, not the age. Verified: 146,632 of 178,555 singles entries are age-coded.
- Add `discipline` (`singles`/`doubles`/`mixed_doubles`/`team`) from `sub_event_type`. The 1,414 "Team" entries are whole ties with no details; they never enter `fct_singles`.

New tests (singular, in `dbt/tests/`):
- Derived games-won from `results_game_scores` equals the numeric `overall_scores` for `result_type = 'played'`.
- Winner reaches `ceil(best_of / 2)` games.
- Per-game score validity for `result_type = 'played'`: the game winner has at least `max_points_per_game` points and leads by at least 2 (the lead rule is `advantage_per_game`). Report violations at `warn` severity first; sudden-death sets and pre-2001 21-point games will show up here and need triage before this becomes a hard failure.
- `age_category` is never NULL.
- Warn (not fail): count of `LIVE` rows.

**Checkpoint**: `make dbt-run SELECT=stg_match_entries stg_match_details+`, `make dbt-test`, `make check`. Review failures by hand: expect genuine oddities, not just bugs.

## Chunk 5 — `dim_matches`, `fct_singles`, `fct_match_games` & `match_sk` on Commentary
**Goal**: Strictly 1v1 singles facts (doubles deferred to `fct_doubles`) and a commentary join on a single `bigint`.
- `marts/wtt/wtt__dim_matches.sql` (`alias='dim_matches'`): one row per `match_sk` for **all** disciplines from `stg_match_entries`; `event_sk`, `discipline`, `age_category`, `is_live`.
- `marts/wtt/wtt__fct_singles.sql` (`alias='fct_singles'`):
  - Grain: `stg_match_entries` (singles only, review item 3) left-joined to `stg_match_details` and commentary counts. `LIVE` matches excluded.
  - Columns beyond the base set: `age_category`, `is_youth`, `result_type`, `is_default_result`, `defaulting_side`, `defaulting_player_sk`, `is_rating_eligible`, `has_result`, `overall_score_raw`, `best_of`, `match_format`, `scoring_rule`, `games_won_p1/p2`, `points_won_p1/p2`, `winner_side`, `p1_source_player_sk`/`p2_source_player_sk`, `p1_player_sk`/`p2_player_sk`.
  - For defaults, `games_won_*`/`points_won_*` are NULL (the upstream game scores are fabricated); `overall_score_raw` keeps the original text.
- `marts/wtt/wtt__fct_match_games.sql` (`alias='fct_match_games'`): one row per `(match_sk, game_number)` from `results_game_scores`; drops `0-0` padding games; only `result_type = 'played'` (INJ/RET partial games deferred, only 9 matches).
- Add `match_sk` to `fct_match_commentary`; add an index on `(match_sk) include (action_type)`. Relationship test to `dim_matches`.
- **Competitor 1 wins 62% of matches** (13,926 vs 8,651 in the current data), so side order is not random. Downstream models must treat `p1`/`p2` as ordered, not exchangeable.
- Possible future gain: commentary exists for 67,953 matches vs 22,600 with scores. Results could be derived from point-won (`PT`) actions for matches that have commentary but no details. Out of scope here; revisit before Glicko-2 if sample size is a problem.

**Checkpoint**: full `make dbt-run`, `make dbt-test`, `make check`.

## Chunk 6 — Unified `marts.*` ("Marts of Marts") Layer
**Goal**: Thin unified views `marts.dim_events`, `marts.dim_players`, `marts.dim_matches`, `marts.fct_singles`, `marts.fct_match_games`, `marts.fct_match_commentary` in `dbt/models/marts/core/`.
- Files are named `core__<name>.sql` with `alias='<name>'` (review item 1). Materialized as **views**.
- Initially select directly from `marts_wtt.*` so Polars/Streamlit/FastAPI point at `marts.*` from day one.
- Each future league (`pro_a`, `ttbl`, `tleague`, `superliga`, `mltt`) adds `ingest_<league>` → `staging_<league>` → `marts_<league>` and one more `UNION ALL` branch, plus `int_player_identity_map` rows.

## Chunk 7 — (Optional, On Next Clean DB Reset) Split `ingest` → `ingest_wtt` + `ingest_ittf`
**Goal**: Only if you want raw schemas split too (DB resets take ~2 hours to re-collect).
- Update `db/migrations/` and `src/tt_stats/` SQL from `ingest.` to `ingest_wtt.` / `ingest_ittf.`; change `schema:` in the two source YAMLs (already named `wtt` / `ittf` after Chunk 1, so no model edits).
- Update `db/init/001_bootstrap.sql`, `tests/integration/test_database_migrations.py` (expects exactly `analytics` and `ingest`), and `summary.py`.
- Chunks 1–6 do **not** depend on Chunk 7.

## Follow-ups Outside This Plan (ingestion side, ADR 0004)
- **Re-fetch `LIVE` match details.** The 11 `LIVE` rows in `ingest.match_details_raw` were captured mid-match and will flip to `OFFICIAL` on re-fetch. Ingestion queues derive from raw only, so this needs a queue view over `payload ->> 'resultStatus' = 'LIVE'`, not a dbt model.
- **Check why ~89% of match-detail payloads are `{}`.** If the upstream truly has no data for most youth matches, nothing to do; if it is a fetch problem, ratings coverage improves a lot.
