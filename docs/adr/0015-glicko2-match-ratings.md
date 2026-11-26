# 0015. Glicko-2 match ratings in a decoupled `analytics` schema

## Status

Accepted (November 2026).

## Context

With clean senior singles marts in place (`marts.fct_senior_singles_matches`, see [ADR 0013](0013-match-cleaning-and-senior-singles-marts.md)), the platform needs historical and current Glicko-2 skill ratings for players. Three architectural questions shaped the design:

1. **Where should ratings live?** `marts.fct_senior_singles_matches` is already 60 columns wide and built by dbt. Computing Glicko-2 requires sequential chronological replay in Python, so writing rating columns back into `marts.fct_senior_singles_matches` would introduce a circular dependency (`dbt` -> Python -> `dbt`) and bloat the match mart with 14+ rating columns.
2. **Should inactivity RD decay be enabled?** The warehouse currently covers WTT senior singles, while players also compete in national/regional leagues (e.g., Pro A, TTBL, T.League, MLTT) that are not yet ingested. Penalizing or inflating a player's Rating Deviation (RD) for gaps in WTT appearances would misrepresent active players as inactive.
3. **Should ratings be updated incrementally or recomputed from scratch? Should a FastAPI backend be introduced now?**

## Decision

1. **Decoupled `analytics` schema (`analytics.fct_glicko_match_ratings` + `analytics.glicko_run_metadata`):**
   - Defined in `db/migrations/019_analytics_glicko2.sql` and populated downstream of dbt by `src/tt_stats/analytics/glicko2.py` (`uv run tt-glicko-build` / `make glicko-run`, which runs `tt-db-migrate` first).
   - Stores 1 row per rating-eligible `match_sk` with `p1`/`p2` pre- and post-match `rating`, `rd`, `vol`, `p1_expected_win_prob`, `p1_actual_score`, `gender_category`, and `rating_date`.
   - Player-level metrics (current rating, peak rating, average rating, rating history, and matchup differences) are derived dynamically from this single table by joining on indexed `match_sk` / `player_sk` rather than duplicating wide rating tables.
2. **Zero inactivity time decay by default (`glicko_rd_decay_days = None`):**
   - Players are never penalized for gaps between WTT matches because they play unscraped matches elsewhere. Decay remains configurable via `GLICKO_RD_DECAY_DAYS` (or `--rd-decay-days`) once multi-league coverage is complete.
   - All core parameters (`initial_rating=1500.0`, `initial_rd=350.0`, `initial_vol=0.06`, `tau=0.5`, `update_mode="match"`, `separate_gender_pools=true`) are configurable in `Settings` (`.env`) and via CLI flags.
3. **Full chronological recompute on every run:**
   - Replaying ~33.8k matches and bulk-loading via PostgreSQL `COPY` takes ~1 second. Recomputing from scratch guarantees exact consistency whenever backdated league matches arrive, player identities are merged in `player_identity_overrides.csv`, or model parameters change.
4. **Defer FastAPI and hosted PostgreSQL:**
   - The Streamlit app continues to read Zstandard-compressed Parquet snapshots ([ADR 0014](0014-parquet-snapshots-for-streamlit-deployment.md)) rather than introducing a FastAPI service or hosted database before point-level commentary drilldowns or external API consumers require one.

## Consequences

**Positive**

- Strictly one-way data flow (`ingest_*` -> `dbt` -> `analytics.*` -> `tt-app-export` -> Streamlit) with no circular dependencies and no bloat on `marts.fct_senior_singles_matches`.
- Sub-second to ~1-second full rebuilds make parameter tuning and multi-league backfills trivial.
- Zero time decay avoids artificially inflating RD for active league players not yet in the WTT feed.

**Negative**

- Adding ratings to the hosted Streamlit app requires exporting `analytics.fct_glicko_match_ratings` in `tt-app-export` (`make app-refresh`), intentionally deferred to the frontend phase.
- Player-specific split ratings (e.g., vs. left-handers or vs. defenders) will require either dynamic expected-vs-actual aggregation in Polars or dedicated split rating runs later.
