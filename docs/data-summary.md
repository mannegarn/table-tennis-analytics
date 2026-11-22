# Database Data Summary

- **Generated at**: 2026-11-22 21:33:06 UTC
- **Database**: `tt_stats` (5173 MB)
- **Applied migrations**: 16

## Executive Data Overview

| Entity | Actual Count | Usable Data Snapshot |
| --- | --- | --- |
| Tournaments | 2,969 | 535 with matches; 508 with sponsors |
| Matches | 202,031 | 51,637 with non-empty details (51,636 with players); 68,128 with commentary (6,731,794 rows) |
| Players | 6,782 | 6,432 with WTT profile; 2,931 with ITTF profile (2,931 total ITTF rows) |

## High-Level Data Coverage (Has Data vs. Does Not Have Data)

| Entity / Check | Total | Has Data | No Data | Notes |
| --- | --- | --- | --- | --- |
| Calendar years fetched -> have events | 102 | 93 (91.2%) | 9 (8.8%) | range 1926-2027, 2 pending |
| Tournaments -> match list fetched | 2,969 | 2,843 (95.8%) | 126 (4.2%) | 2 pending |
| Tournaments -> have matches (>0) | 2,969 | 535 (18.0%) | 2,434 (82.0%) | remaining fetched events returned [] or are pending |
| Tournaments -> sponsors fetched | 2,969 | 2,843 (95.8%) | 126 (4.2%) | 2 pending |
| Tournaments -> have sponsors (>0) | 2,969 | 508 (17.1%) | 2,461 (82.9%) | remaining fetched events had 0 sponsors or are pending |
| Matches -> match details fetched | 202,031 | 202,022 (100.0%) | 9 (0.0%) | 240 pending |
| Matches -> have non-empty details | 202,031 | 51,637 (25.6%) | 150,394 (74.4%) | 150,385 fetched rows returned empty {} |
| Matches -> have unpacked players | 202,031 | 51,636 (25.6%) | 150,395 (74.4%) | from match_details_players_unpacked |
| Matches -> commentary fetched | 202,031 | 202,031 (100.0%) | 0 (0.0%) | 231 pending |
| Matches -> have commentary (>0 rows) | 202,031 | 68,128 (33.7%) | 133,903 (66.3%) | 6,731,794 total rows |
| Seen players -> WTT details fetched | 6,782 | 6,782 (100.0%) | 0 (0.0%) | 0 pending |
| Seen players -> WTT profile unpacked | 6,782 | 6,432 (94.8%) | 350 (5.2%) | non-empty GetPlayers response |
| Seen players -> ITTF profile | 6,782 | 2,931 (43.2%) | 3,851 (56.8%) | 3,851 pending; 2,931 total ITTF rows |

## Tournaments & Calendar

- **Calendar years fetched**: 102 (1926-2027), with **93** having events and **9** empty.
- **Calendar event rows**: 2,971 unpacked from 2,990 raw items.
- **Distinct tournaments (`event_id`)**: 2,969 (date span: 1926-12-06 to 2027-12-12).

## Players & Profile Enrichment

- **Distinct players seen in matches**: 6,782
- **WTT `player_details_raw` rows**: 6,782 (unpacked: 6,432)
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
| wtt_matchcentre | 204,287 | 204,188 (100.0%) | 99 (0.0%) | 2026-11-22 14:24 UTC |
| wtt_match_commentary | 204,147 | 204,147 (100.0%) | 0 (0.0%) | 2026-11-22 14:24 UTC |
| wtt_player_details | 6,782 | 6,782 (100.0%) | 0 (0.0%) | 2026-11-22 14:23 UTC |
| wtt_geteventequipmentwithlogo | 2,863 | 2,863 (100.0%) | 0 (0.0%) | 2026-11-22 14:24 UTC |
| wtt_getofficialresult | 2,863 | 2,863 (100.0%) | 0 (0.0%) | 2026-11-22 14:24 UTC |
| wtt_eventcalendar | 122 | 122 (100.0%) | 0 (0.0%) | 2026-11-22 14:24 UTC |

## Table Inventory (Row Counts & Disk Size)

| Table | Rows | Total Size |
| --- | --- | --- |
| `ingest_ittf.ittf_player_profiles` | 2,931 | 1752 kB |
| `ingest_wtt.match_details_raw` | 202,022 | 972 MB |
| `ingest_wtt.match_commentary_raw` | 202,031 | 627 MB |
| `ingest_wtt.ingest_runs` | 421,064 | 166 MB |
| `ingest_wtt.match_entries_unpacked` | 202,031 | 45 MB |
| `ingest_wtt.match_details_players_unpacked` | 109,105 | 29 MB |
| `ingest_wtt.player_details_raw` | 6,782 | 8160 kB |
| `ingest_wtt.match_entries_payloads_raw` | 2,843 | 3744 kB |
| `ingest_wtt.event_sponsors_raw` | 2,843 | 904 kB |
| `ingest_wtt.events_unpacked` | 2,971 | 840 kB |
| `ingest_wtt.events_by_year_raw` | 102 | 640 kB |
| `intermediate.int_event_equipment_sponsors` | 419 | 40 kB |
| `marts_wtt.fct_match_commentary` | 6,720,243 | 1970 MB |
| `marts_wtt.dim_players` | 6,432 | 1064 kB |
| `marts_wtt.dim_events` | 2,969 | 520 kB |
| `seeds.event_sponsors_manual` | 242 | 56 kB |
| `seeds.action_types` | 23 | 16 kB |
| `seeds.sponsor_domains` | 17 | 16 kB |
| `seeds.player_identity_overrides` | 0 | 0 bytes |
| `staging_wtt.stg_match_commentary` | 6,720,243 | 1289 MB |
| `staging_wtt.stg_match_entries` | 202,031 | 25 MB |
| `staging_wtt.stg_match_details` | 51,637 | 23 MB |
| `staging_wtt.stg_wtt_players` | 6,432 | 944 kB |
| `staging_wtt.stg_events` | 2,971 | 552 kB |
| `staging_wtt.stg_event_sponsors` | 1,639 | 312 kB |
