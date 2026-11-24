# Database Data Summary

- **Generated at**: 2026-11-23 23:09:19 UTC
- **Database**: `tt_stats` (5196 MB)
- **Applied migrations**: 17

## Executive Data Overview

| Entity | Actual Count | Usable Data Snapshot |
| --- | --- | --- |
| Tournaments | 2,970 | 536 with matches; 509 with sponsors |
| Matches | 202,184 | 51,790 with non-empty details (51,789 with players); 68,281 with commentary (6,760,339 rows) |
| Players | 6,812 | 6,462 with WTT profile; 2,931 with ITTF profile (2,931 total ITTF rows) |

## High-Level Data Coverage (Has Data vs. Does Not Have Data)

| Entity / Check | Total | Has Data | No Data | Notes |
| --- | --- | --- | --- | --- |
| Calendar years fetched -> have events | 102 | 93 (91.2%) | 9 (8.8%) | range 1926-2027, 2 pending |
| Tournaments -> match list fetched | 2,970 | 2,844 (95.8%) | 126 (4.2%) | 2 pending |
| Tournaments -> have matches (>0) | 2,970 | 536 (18.0%) | 2,434 (82.0%) | remaining fetched events returned [] or are pending |
| Tournaments -> sponsors fetched | 2,970 | 2,844 (95.8%) | 126 (4.2%) | 2 pending |
| Tournaments -> have sponsors (>0) | 2,970 | 509 (17.1%) | 2,461 (82.9%) | remaining fetched events had 0 sponsors or are pending |
| Matches -> match details fetched | 202,184 | 202,175 (100.0%) | 9 (0.0%) | 198 pending |
| Matches -> have non-empty details | 202,184 | 51,790 (25.6%) | 150,394 (74.4%) | 150,385 fetched rows returned empty {} |
| Matches -> have unpacked players | 202,184 | 51,789 (25.6%) | 150,395 (74.4%) | from match_details_players_unpacked |
| Matches -> commentary fetched | 202,184 | 202,184 (100.0%) | 0 (0.0%) | 189 pending |
| Matches -> have commentary (>0 rows) | 202,184 | 68,281 (33.8%) | 133,903 (66.2%) | 6,760,339 total rows |
| Seen players -> WTT details fetched | 6,812 | 6,812 (100.0%) | 0 (0.0%) | 0 pending |
| Seen players -> WTT profile unpacked | 6,812 | 6,462 (94.9%) | 350 (5.1%) | non-empty GetPlayers response |
| Seen players -> ITTF profile | 6,812 | 2,931 (43.0%) | 3,881 (57.0%) | 3,881 pending; 2,931 total ITTF rows |

## Tournaments & Calendar

- **Calendar years fetched**: 102 (1926-2027), with **93** having events and **9** empty.
- **Calendar event rows**: 2,972 unpacked from 2,991 raw items.
- **Distinct tournaments (`event_id`)**: 2,970 (date span: 1926-12-06 to 2027-12-12).

## Players & Profile Enrichment

- **Distinct players seen in matches**: 6,812
- **WTT `player_details_raw` rows**: 6,812 (unpacked: 6,462)
- **ITTF `ittf_player_profiles` rows**: 2,931 (2,931 matched to seen players, 0 not yet seen in WTT matches)

### ITTF Player Profiles Completeness (`ingest_ittf.ittf_player_profiles`)

| Field | Total Rows | Has Data | Missing / Empty |
| --- | --- | --- | --- |
| style | 2,931 | 2,846 (97.1%) | 85 (2.9%) |
| grip | 2,931 | 2,846 (97.1%) | 85 (2.9%) |
| hand | 2,931 | 2,846 (97.1%) | 85 (2.9%) |

### ITTF Profiles by Source

| Source | Rows | With Style | With Grip | With Hand |
| --- | --- | --- | --- | --- |
| 20260615_master_players.csv | 2,931 | 2,846 | 2,846 | 2,846 |

## Ingestion Run History (`ingest_wtt.ingest_runs`)

| Endpoint | Total Runs | Succeeded | Failed | Last Run |
| --- | --- | --- | --- | --- |
| wtt_matchcentre | 204,680 | 204,572 (99.9%) | 108 (0.1%) | 2026-11-23 19:23 UTC |
| wtt_match_commentary | 204,531 | 204,531 (100.0%) | 0 (0.0%) | 2026-11-23 19:23 UTC |
| wtt_player_details | 6,812 | 6,812 (100.0%) | 0 (0.0%) | 2026-11-23 23:05 UTC |
| wtt_geteventequipmentwithlogo | 2,866 | 2,866 (100.0%) | 0 (0.0%) | 2026-11-23 19:23 UTC |
| wtt_getofficialresult | 2,866 | 2,866 (100.0%) | 0 (0.0%) | 2026-11-23 19:23 UTC |
| wtt_eventcalendar | 124 | 124 (100.0%) | 0 (0.0%) | 2026-11-23 19:23 UTC |

## Table Inventory (Row Counts & Disk Size)

| Table | Rows | Total Size |
| --- | --- | --- |
| `ingest_ittf.ittf_player_profiles` | 2,931 | 1752 kB |
| `ingest_wtt.match_details_raw` | 202,175 | 972 MB |
| `ingest_wtt.match_commentary_raw` | 202,184 | 632 MB |
| `ingest_wtt.ingest_runs` | 421,879 | 166 MB |
| `ingest_wtt.match_entries_unpacked` | 202,184 | 45 MB |
| `ingest_wtt.match_details_players_unpacked` | 114,102 | 30 MB |
| `ingest_wtt.player_details_raw` | 6,812 | 8200 kB |
| `ingest_wtt.match_entries_payloads_raw` | 2,844 | 3744 kB |
| `ingest_wtt.event_sponsors_raw` | 2,844 | 904 kB |
| `ingest_wtt.events_unpacked` | 2,972 | 840 kB |
| `ingest_wtt.events_by_year_raw` | 102 | 640 kB |
| `intermediate.int_event_equipment_sponsors` | 420 | 40 kB |
| `marts_wtt.fct_match_commentary` | 6,748,788 | 1979 MB |
| `marts_wtt.dim_players` | 6,462 | 1064 kB |
| `marts_wtt.dim_events` | 2,970 | 520 kB |
| `seeds.event_sponsors_manual` | 243 | 56 kB |
| `seeds.action_types` | 23 | 16 kB |
| `seeds.match_format_overrides` | 12 | 16 kB |
| `seeds.result_types` | 8 | 16 kB |
| `seeds.round_codes` | 34 | 16 kB |
| `seeds.sponsor_domains` | 17 | 16 kB |
| `seeds.player_identity_overrides` | 0 | 0 bytes |
| `staging_wtt.stg_match_commentary` | 6,748,788 | 1294 MB |
| `staging_wtt.stg_match_entries` | 202,184 | 25 MB |
| `staging_wtt.stg_match_details` | 51,790 | 24 MB |
| `staging_wtt.stg_team_match_rubbers` | 3,212 | 1784 kB |
| `staging_wtt.stg_wtt_players` | 6,462 | 952 kB |
| `staging_wtt.stg_events` | 2,972 | 552 kB |
| `staging_wtt.stg_event_sponsors` | 1,640 | 312 kB |
