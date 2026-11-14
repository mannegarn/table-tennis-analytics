# Database Data Summary

- **Generated at**: 2026-11-14 09:36:21 UTC
- **Database**: `tt_stats` (4586 MB)
- **Applied migrations**: 17

## Executive Data Overview

| Entity | Actual Count | Usable Data Snapshot |
| --- | --- | --- |
| Tournaments | 2,965 | 533 with matches; 506 with sponsors |
| Matches | 201,399 | 21,955 with non-empty details (21,955 with players); 67,883 with commentary (6,674,908 rows) |
| Players | 4,394 | 4,394 with WTT profile; 1,068 with ITTF profile (2,931 total ITTF rows) |

## High-Level Data Coverage (Has Data vs. Does Not Have Data)

| Entity / Check | Total | Has Data | No Data | Notes |
| --- | --- | --- | --- | --- |
| Calendar years fetched -> have events | 102 | 93 (91.2%) | 9 (8.8%) | range 1926-2027, 2 pending |
| Tournaments -> match list fetched | 2,965 | 2,841 (95.8%) | 124 (4.2%) | 5 pending |
| Tournaments -> have matches (>0) | 2,965 | 533 (18.0%) | 2,432 (82.0%) | remaining fetched events returned [] or are pending |
| Tournaments -> sponsors fetched | 2,965 | 2,840 (95.8%) | 125 (4.2%) | 5 pending |
| Tournaments -> have sponsors (>0) | 2,965 | 506 (17.1%) | 2,459 (82.9%) | remaining fetched events had 0 sponsors or are pending |
| Matches -> match details fetched | 201,399 | 201,202 (99.9%) | 197 (0.1%) | 564 pending |
| Matches -> have non-empty details | 201,399 | 21,955 (10.9%) | 179,444 (89.1%) | 179,247 fetched rows returned empty {} |
| Matches -> have unpacked players | 201,399 | 21,955 (10.9%) | 179,444 (89.1%) | from match_details_players_unpacked |
| Matches -> commentary fetched | 201,399 | 201,399 (100.0%) | 0 (0.0%) | 555 pending |
| Matches -> have commentary (>0 rows) | 201,399 | 67,883 (33.7%) | 133,516 (66.3%) | 6,674,908 total rows |
| Seen players -> WTT details fetched | 4,394 | 4,394 (100.0%) | 0 (0.0%) | 0 pending |
| Seen players -> WTT profile unpacked | 4,394 | 4,394 (100.0%) | 0 (0.0%) | non-empty GetPlayers response |
| Seen players -> ITTF profile | 4,394 | 1,068 (24.3%) | 3,326 (75.7%) | 3,326 pending; 2,931 total ITTF rows |

## Tournaments & Calendar

- **Calendar years fetched**: 102 (1926-2027), with **93** having events and **9** empty.
- **Calendar event rows**: 2,967 unpacked from 2,986 raw items.
- **Distinct tournaments (`event_id`)**: 2,965 (date span: 1926-12-06 to 2027-12-12).

## Event Sponsors Field Completeness (`ingest.event_sponsors_unpacked`)

| Field | Total Rows | Has Data | Missing / Empty |
| --- | --- | --- | --- |
| sponsor_type_id | 1,635 | 1,635 (100.0%) | 0 (0.0%) |
| sponsor_type_name | 1,635 | 1,635 (100.0%) | 0 (0.0%) |
| logo | 1,635 | 1,635 (100.0%) | 0 (0.0%) |
| dark_logo | 1,635 | 813 (49.7%) | 822 (50.3%) |
| sponsor_link | 1,635 | 1,230 (75.2%) | 405 (24.8%) |

## Match Commentary Field Completeness (`ingest.match_commentary_unpacked`)

| Field | Total Rows | Has Data | Missing / Empty |
| --- | --- | --- | --- |
| commentary_id | 6,674,908 | 6,674,908 (100.0%) | 0 (0.0%) |
| action_type | 6,674,908 | 6,674,908 (100.0%) | 0 (0.0%) |
| commentary_text | 6,674,908 | 6,674,558 (100.0%) | 350 (0.0%) |
| utc_date | 6,674,908 | 6,674,908 (100.0%) | 0 (0.0%) |
| local_date | 6,674,908 | 6,263,330 (93.8%) | 411,578 (6.2%) |
| source_ts | 6,674,908 | 6,674,908 (100.0%) | 0 (0.0%) |

## Players & Profile Enrichment

- **Distinct players seen in matches**: 4,394
- **WTT `player_details_raw` rows**: 4,394 (unpacked: 4,394)
- **ITTF `ittf_player_profiles` rows**: 2,931 (1,068 matched to seen players, 1,863 not yet seen in WTT matches)

### WTT Player Details Completeness (`ingest.player_details_unpacked`)

| Field | Total Rows | Has Data | Missing / Empty |
| --- | --- | --- | --- |
| player_name | 4,394 | 4,394 (100.0%) | 0 (0.0%) |
| country_code | 4,394 | 4,394 (100.0%) | 0 (0.0%) |
| country_name | 4,394 | 4,394 (100.0%) | 0 (0.0%) |
| nationality_code | 4,394 | 4,394 (100.0%) | 0 (0.0%) |
| gender | 4,394 | 4,394 (100.0%) | 0 (0.0%) |
| age | 4,394 | 4,394 (100.0%) | 0 (0.0%) |
| dob | 4,394 | 4,394 (100.0%) | 0 (0.0%) |
| handedness | 4,394 | 637 (14.5%) | 3,757 (85.5%) |
| grip | 4,394 | 634 (14.4%) | 3,760 (85.6%) |
| style | 4,394 | 0 (0.0%) | 4,394 (100.0%) |
| equipment_sponsor | 4,394 | 0 (0.0%) | 4,394 (100.0%) |
| blade_type | 4,394 | 0 (0.0%) | 4,394 (100.0%) |
| racket_covering_a | 4,394 | 0 (0.0%) | 4,394 (100.0%) |
| racket_covering_b | 4,394 | 0 (0.0%) | 4,394 (100.0%) |
| active_since | 4,394 | 0 (0.0%) | 4,394 (100.0%) |
| earnings_career | 4,394 | 0 (0.0%) | 4,394 (100.0%) |
| head_shot | 4,394 | 956 (21.8%) | 3,438 (78.2%) |
| bio | 4,394 | 0 (0.0%) | 4,394 (100.0%) |

### ITTF Player Profiles Completeness (`ingest.ittf_player_profiles`)

| Field | Total Rows | Has Data | Missing / Empty |
| --- | --- | --- | --- |
| style | 2,931 | 2,846 (97.1%) | 85 (2.9%) |
| grip | 2,931 | 2,846 (97.1%) | 85 (2.9%) |
| hand | 2,931 | 2,846 (97.1%) | 85 (2.9%) |

### ITTF Profiles by Source

| Source | Rows | With Style | With Grip | With Hand |
| --- | --- | --- | --- | --- |
| 20260615_master_players.csv | 2,931 | 2,846 | 2,846 | 2,846 |

## Ingestion Run History (`ingest.ingest_runs`)

| Source | Total Runs | Succeeded | Failed | Last Run |
| --- | --- | --- | --- | --- |
| wtt_matchcentre | 405,481 | 204,907 (50.5%) | 200,574 (49.5%) | 2026-11-13 22:50 UTC |
| wtt_match_commentary | 211,906 | 204,473 (96.5%) | 7,433 (3.5%) | 2026-11-14 09:26 UTC |
| wtt_player_details | 4,394 | 4,394 (100.0%) | 0 (0.0%) | 2026-11-13 23:35 UTC |
| wtt_getofficialresult | 2,853 | 2,853 (100.0%) | 0 (0.0%) | 2026-11-14 09:20 UTC |
| wtt_geteventequipmentwithlogo | 2,844 | 2,844 (100.0%) | 0 (0.0%) | 2026-11-12 19:24 UTC |
| wtt_eventcalendar | 108 | 108 (100.0%) | 0 (0.0%) | 2026-11-14 09:19 UTC |

## Table Inventory (Row Counts & Disk Size)

| Table | Rows | Total Size |
| --- | --- | --- |
| `ingest.match_commentary_unpacked` | 6,674,908 | 2764 MB |
| `ingest.match_details_raw` | 201,202 | 841 MB |
| `ingest.match_commentary_raw` | 201,399 | 631 MB |
| `ingest.ingest_runs` | 627,586 | 269 MB |
| `ingest.match_entries_unpacked` | 201,399 | 45 MB |
| `ingest.match_details_players_unpacked` | 48,818 | 13 MB |
| `ingest.player_details_raw` | 4,394 | 5504 kB |
| `ingest.match_entries_payloads_raw` | 2,841 | 3704 kB |
| `ingest.player_details_unpacked` | 4,394 | 1208 kB |
| `ingest.events_unpacked` | 2,967 | 1056 kB |
| `ingest.event_sponsors_raw` | 2,840 | 888 kB |
| `ingest.ittf_player_profiles` | 2,931 | 640 kB |
| `ingest.events_by_year_raw` | 102 | 544 kB |
| `ingest.event_sponsors_unpacked` | 1,635 | 456 kB |
| `ingest.schema_version` | 17 | 32 kB |
