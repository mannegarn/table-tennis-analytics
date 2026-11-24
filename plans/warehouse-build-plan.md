# Warehouse Build Plan (`level_source`)

**Status**: Planning only — **do not execute code changes until approved.**
**Design spec**: `docs/database-design.md` | **Diagram**: `docs/drawdb-warehouse-design.json`
**Last revised**: 2026-11-18

The warehouse is built to the target design in one clean pass: the database is reset and everything is rescraped, so there is no migration path and no interim state. Python scrapers, HTTP clients, CI and the SQL unpack logic keep their behaviour; the work is the schema layout, the run-history and ledger changes, and the dbt layers on top.

## Decisions

| Decision | Outcome |
|---|---|
| Raw schemas | `ingest_wtt`, `ingest_ittf`. No bare `ingest` schema. |
| Run history | `ingest_wtt.ingest_runs`, local to the source. The `source` column becomes `endpoint` (API endpoint, e.g. `wtt_eventcalendar`). ITTF is a CSV seed and has no run history. A future league adds its own `ingest_<league>.ingest_runs`. |
| Migration ledger | `public.schema_version`, one per database. Migrations create `ingest_wtt` / `ingest_ittf` themselves (the test database never runs the bootstrap script). |
| dbt schemas | `staging_wtt`, `staging_ittf`, `intermediate`, `marts_wtt`, `marts_ittf`, `marts`, `seeds`. The profile's default schema `dbt_default` must stay empty. |
| Default results (walkover, injury, retirement, disqualification) | **Kept** in `fct_singles`, flagged by `result_type`, `is_default_result`, `defaulting_side`, `defaulting_player_sk`, `is_rating_eligible`. |
| `LIVE` matches | **Excluded** from `fct_singles` and `fct_match_games`; kept in `dim_matches` (`is_live = true`) so commentary keys resolve. |
| Age category | `age_category` (`senior`, `U11`…`U21`) and `is_youth`, parsed from `document_code`. |

Full match rules: `docs/database-design.md` §10 (single source of truth).

## Implementation Rules

**Names and keys**
1. dbt model names are unique project-wide, so each mart with a unified twin is `<source>__<name>.sql` (`wtt__dim_events.sql`, `core__dim_events.sql`) with `config(alias='<name>')`. Unified `marts/core` models are **views** until a second league exists.
2. The `surrogate_key` macro casts every part to `::text`; a NULL part yields a NULL key, caught by `not_null`. Every `_sk` has `unique` and `not_null` tests.
3. WTT `player_id` is the ITTF ID: hash it as `'ittf|' || player_id` via a dedicated `player_sk()` macro.
4. `player_sk` changes when identity is resolved, so facts carry a permanent `source_player_sk` plus the resolved `player_sk`. Ratings are always recomputed from scratch. `player_identity_overrides.csv` points at `target_ittf_id` or a target source key, never a pasted hash.
5. Commentary covers doubles and team matches, so `fct_match_commentary.match_sk` references `dim_matches` (all disciplines), not `fct_singles`.

**Data rules**
6. `fct_singles` filters on `sub_event_type like '%Singles'` **and** both second-player IDs NULL. `NOT NULL` on player 1 and 2 also passes doubles, and `competitorType` is `H` on every row.
7. `fct_singles` takes match entries as the grain and **left-joins** match details. Only about 11% of entries have a score payload (179,247 of 201,847 detail rows are HTTP 404 with `{}`).
8. `best_of` is read from `matchConfig.bestOfXGames`, not `bestOf`.
9. Use `results_game_scores` as the only source of game scores. `comp_1_scores`/`comp_2_scores` disagree with it on defaults.
10. Competitor 1 wins about 62% of matches, so `p1`/`p2` are ordered, not exchangeable.

**dbt / Postgres**
11. Heavy WTT staging models stay `table`; as views they would re-expand the JSONB on every test and build.
12. `fct_match_commentary` post-hook uses an **unnamed** covering index plus `analyze`. A named `create index if not exists` silently skips after dbt's backup-table rename.
13. sqlfluff needs `load_macros_from_path = dbt/macros` in `.sqlfluff` before the first macro.
14. Name dbt sources after the source system (`source('wtt', …)`, `source('ittf', …)`).
15. Write ADR 0012 superseding ADR 0007, and ADR 0013 for the match-result rules.

---

## Phase 1 — Raw Layer
**Goal**: `ingest_wtt`, `ingest_ittf`, `public.schema_version`, local `ingest_runs`; no `ingest` or `analytics` schema.
- Bootstrap keeps only `public` and `pg_trgm`; migrations create the source schemas.
- Migrations 001–016 are rewritten in place to the target state (the database is reset, so checksums do not matter). `ingest_runs` lives in `ingest_wtt` with an `endpoint` column.
- `core/migrations.py` uses `public.schema_version`. `core/ingest_runs.py` writes to `ingest_wtt.ingest_runs` directly (only WTT records run history); a future league adds its own table and a schema parameter then.
- All table references in `src/`, `tests/`, `Makefile`, `README.md` use the new schemas. A grep proves no unqualified `ingest.` remains.
- Reset the database, migrate, then `make collect-all` (it seeds ITTF profiles and unpacks the narrow queue keys at land time).
- `make dbt-run` becomes `dbt build` (seeds, models and tests in dependency order).

**Checkpoint**: `make check`, `make test` green; `\dn` shows `ingest_wtt`, `ingest_ittf`, `public` only; row counts ≥ the old warehouse.

## Phase 2 — dbt Skeleton
- Models live in `models/staging/<source>/`, `models/intermediate/`, `models/marts/<source>/`, `models/marts/core/`.
- `generate_schema_name` macro, per-folder `+schema` and `+materialized`, `seeds: +schema: seeds`, sources `wtt` and `ittf`.
- Existing models keep their SQL; `fct_match_commentary` gets the corrected post-hook.

**Checkpoint**: `dbt parse`, `make dbt-run`, `make dbt-test`, `make check`; no model in `dbt_default`.

## Phase 3 — Surrogate Keys & `dim_events`
- `surrogate_key` and `player_sk` macros.
- `wtt__dim_events`: dedupe on `event_id` (events 2226/2227 appear in both 2020 and 2021), whitespace-normalised text, guarded `duration_days`, `event_sk`, sponsors from `int_event_equipment_sponsors`, availability flags.

## Phase 4 — Players
- `ittf__dim_player_profiles` (view), `wtt__dim_players` (`player_sk`, nullable `ittf_id`, `coalesce` of `hand`/`grip`/`style`).
- `player_identity_overrides.csv` seed. A warn-level test lists `(name, country)` pairs with more than one `player_sk` (5 exist today, e.g. `LEE Yubin` KOR). Never merge on name alone.

## Phase 5 — Match Staging (Implemented)
- `stg_match_details`: `best_of` (`bestOfXGames`/`bestOf`), `max_points_per_game`, `advantage_per_game`, `sudden_death_point`, overall score split (`overall_games_p1`, `overall_games_p2`, `result_suffix`), and session-TZ-invariant timestamp macros.
- `stg_match_entries` & `stg_team_match_rubbers`: `discipline`, `gender_category`, `age_category`, `is_youth`, `para_class`, `is_para`, `raw_round_token`, `group_number`, `match_number_in_round`.
- `match_format_overrides.csv` (`is_team_rubber`-scoped) & `match_score_overrides.csv` seeds, plus `has_wtt_profile` player stubs in `wtt__dim_players`.

## Phase 6 — Intermediate & Match Marts (Implemented, ADR 0013)
- `int_wtt_senior_singles_games` & `int_wtt_senior_singles` (canonical `overall_scores` + `results_game_scores`, format overrides, score overrides, anomaly/eligibility flags, `BYE`/Event `2615` R4/R5 exclusions).
- `wtt__dim_matches` (all entries + unpacked team rubbers, reconciled team rounds, `is_live`, `has_match_details`, `has_commentary`), `wtt__fct_senior_singles_matches` (represented association `p1/p2_country_code`, draws with `NULL` winner/loser, defaults with `NULL` game/point totals), `wtt__fct_senior_singles_games` (unsigned `point_margin`), and `match_sk` + `(match_sk, sequence_number)` index on `wtt__fct_match_commentary`.
- Full `error` invariant tests + `warn` upstream data monitors in `dbt/tests/`.

## Phase 7 — Unified `marts` & Docs (Implemented)
- Thin views in `models/marts/core/` (`dim_events`, `dim_players`, `dim_matches`, `fct_senior_singles_matches`, `fct_senior_singles_games`, `fct_match_commentary`) over `marts_wtt.*`. Each future league adds `ingest_<league>` → `staging_<league>` → `marts_<league>` and one `UNION ALL` branch.
- Regenerated `docs/data-summary.md` and added ADR 0013.

## Ingestion Follow-ups (outside dbt, ADR 0004)
- `LIVE` payloads should be re-fetched so they flip to `OFFICIAL`. Queues derive from raw only, so this is a pending view over `payload ->> 'resultStatus' = 'LIVE'`, not a dbt model.
- The ~89% of empty match-detail payloads are HTTP 404. A spot re-request before the rescrape confirms whether this is an upstream gap or a transient failure.
