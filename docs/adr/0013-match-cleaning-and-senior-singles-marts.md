# 0013. Match cleaning, team-tie rubber unification, and senior singles marts

## Status

Accepted (November 2026).

## Context

WTT match records arrive with several upstream structural quirks that must be solved consistently before any rating (Glicko-2) or match/set analytics run:

1. **Split match sources:** Standalone matches live in `staging_wtt.stg_match_entries` + `stg_match_details`, whereas team-tie rubbers live inside `teamParentData.extended_info.matches` and are unpacked into `staging_wtt.stg_team_match_rubbers`.
2. **Multiple upstream score fields:** Payloads carry `overallScores`, `resultOverallScores`, `resultsGameScores`, and `comp_1_scores` / `comp_2_scores`. Live inspection of WTT's `PostMatchCenter` frontend confirmed WTT renders `overallScores` and `resultsGameScores` (stripping `0-0` padding).
3. **Represented association vs. birth country:** `marts_wtt.dim_players.country_code` records birth nationality, whereas match-time `playerOrgCode` / `organization_code` records the association the player represented.
4. **Format & score defects:** Tokyo 2020 (`2345`) omits `bestOfXGames` on both individual (`best_of_7`) and team-tie (`best_of_5`) matches; Paris 2024 (`2603`) misreports individual singles as `best_of_5`; Mixed Team World Cup (`2860`, `2979`) uses `fixed_3_games` rubbers that stop early once a team reaches 8 cumulative games; ~45 matches freeze `resultsGameScores` mid-game while `comp_1_scores` / `comp_2_scores` hold the finished score; and Event `2615` team rubbers contain 10 unlabelled `BYE` rows plus copy-pasted wrong-person player IDs on rubbers 4 and 5.
5. **Unprofiled players:** WTT's `GetPlayers` endpoint returns `{"Result": []}` for a small number of match players (e.g. `137357` `REYEMI Ahmed Salem`).

## Decision

1. **Canonical score & outcome model (`intermediate.int_wtt_senior_singles` & `int_wtt_senior_singles_games`):**
   - Use `overall_scores` and `results_game_scores` (with `0-0` padding stripped while preserving 1-based `game_number` slots) as the primary score source.
   - Apply two explicit, auditable CSV seeds in `seeds`:
     - `match_format_overrides.csv` (scoped by `is_team_rubber`, `discipline`, `round_code`, and `document_code`).
     - `match_score_overrides.csv` (45 verified per-match fixes for frozen/corrupted upstream scores).
   - Keep `games_won_*` and `points_won_*` populated for `played` and `draw` matches (`2-2` draws leave `winner_*` / `loser_*` `NULL` with `is_rating_eligible = true`) and `NULL` for default results (`WO`, `INJ`, `RET`, `DSQ`, `DNS`, with `is_rating_eligible = false`).
   - Exclude unplayed team dead rubbers (`overall_scores` NULL or `0-0` without a default suffix), `BYE` rubbers, and Event `2615` R4/R5 rubbers from senior singles facts.
2. **Mart split & naming (`marts_wtt` & `marts`):**
   - `dim_matches` (`205k+` rows) retains the full WTT catalog (including youth, Para, doubles, `LIVE`, and detail-less entries) with reconciled parent/child team rounds so 100% of `fct_match_commentary.match_sk` (`6.77M` rows) resolves with zero orphans.
   - `fct_senior_singles_matches` (1 row per match) and `fct_senior_singles_games` (1 row per played/drawn game with unsigned `point_margin`) scope strictly to `OFFICIAL` senior able-bodied `men`/`women` singles.
   - `p1_country_code` / `p2_country_code` use `coalesce(match_player_org, dim_players.organization_code)` (represented association, never birth nationality).
3. **Player FK integrity (`wtt__dim_players`):**
   - Add `has_wtt_profile` (`boolean`) and auto-generate minimal stub rows (`has_wtt_profile = false`) for any non-team match player missing from `stg_wtt_players`, plus numeric `comp_1_id` / `comp_2_id` fallback on standalone singles.

## Consequences

**Positive**

- Zero score anomalies (`has_score_anomaly = false`) and zero invalid game scores across all 34,140 senior singles matches and 133,151 games, backed by strict `error` tests and `warn`-level upstream drift monitors.
- 100% FK integrity from `fct_senior_singles_matches`, `fct_senior_singles_games`, and `fct_match_commentary` into `dim_matches`, `dim_events`, and `dim_players`.
- `marts.*` exposes stable unified views (`dim_events`, `dim_players`, `dim_matches`, `fct_senior_singles_matches`, `fct_senior_singles_games`, `fct_match_commentary`) ready for future league `UNION ALL` branches.

**Negative**

- New non-standard tournament formats (e.g. future Mixed Team World Cups `3263` / `3378`) or upstream scoreboard freezes require adding rows to `match_format_overrides.csv` or `match_score_overrides.csv`; `warn` tests surface these automatically without blocking `make dbt-run`.
