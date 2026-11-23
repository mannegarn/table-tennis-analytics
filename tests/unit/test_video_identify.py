"""Unit tests for YouTube + Gemini Vision table manufacturer identification."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tt_stats.event_sponsors.manual_sync import (
    read_manual_sponsors_csv,
    render_manual_sponsors_csv,
)
from tt_stats.event_sponsors.video_identify import (
    AUTO_SOURCE_TAG,
    EventIdentificationOutcome,
    PendingEvent,
    ResolvedVideo,
    TableIdentification,
    VideoCandidate,
    _classify_identification,
    _distinctive_tokens,
    _parse_event_ids,
    apply_identifications,
    build_search_query,
    clean_event_name,
    compute_frame_timestamps,
    extract_frames,
    identify_missing_sponsors,
    identify_table_manufacturer,
    is_within_date_window,
    main,
    parse_yt_upload_date,
    score_candidate,
    search_and_resolve_video,
    select_pending_events,
)


def test_clean_event_name_and_build_search_query() -> None:
    assert (
        clean_event_name("WTT Contender Panagyurishte 2026 Presented by ASAREL")
        == "WTT Contender Panagyurishte 2026"
    )
    assert (
        clean_event_name("WTT Star Contender Doha 2021 (Mar 2021)")
        == "WTT Star Contender Doha 2021"
    )
    assert (
        build_search_query("WTT Macao", date(2022, 1, 19))
        == "WTT Macao 2022 table tennis match"
    )
    assert (
        build_search_query("WTT Feeder Doha 2026", date(2026, 1, 27))
        == "WTT Feeder Doha 2026 table tennis match"
    )


def test_parse_yt_upload_date_and_date_window() -> None:
    assert parse_yt_upload_date("20260910") == date(2026, 9, 10)
    assert parse_yt_upload_date("bad") is None
    assert parse_yt_upload_date(None) is None

    start = date(2026, 9, 8)
    assert is_within_date_window(date(2026, 9, 5), start) is True
    assert is_within_date_window(date(2026, 9, 12), start) is True
    assert is_within_date_window(date(2026, 11, 7), start) is True
    assert is_within_date_window(date(2025, 9, 10), start) is False
    assert is_within_date_window(date(2026, 12, 1), start) is False
    assert is_within_date_window(None, start) is True


def test_score_candidate_prefers_official_matching_year_and_duration() -> None:
    event_name = "WTT Feeder Cappadocia 2026"
    start = date(2026, 2, 2)

    good = VideoCandidate(
        video_id="good1",
        url="https://www.youtube.com/watch?v=good1",
        title="Full Match | WTT Feeder Cappadocia 2026 | Round of 16",
        channel="World Table Tennis",
        duration=1400.0,
        upload_date=date(2026, 2, 4),
    )
    wrong_year = VideoCandidate(
        video_id="old1",
        url="https://www.youtube.com/watch?v=old1",
        title="Full Match | WTT Feeder Cappadocia 2025 | Final",
        channel="World Table Tennis",
        duration=1400.0,
        upload_date=date(2025, 2, 20),
    )
    short_clip = VideoCandidate(
        video_id="short1",
        url="https://www.youtube.com/watch?v=short1",
        title="Crazy point at Cappadocia 2026 #shorts",
        channel="Random Fan",
        duration=22.0,
        upload_date=date(2026, 2, 4),
    )

    good_score = score_candidate(good, event_name, start)
    assert good_score > score_candidate(wrong_year, event_name, start)
    assert good_score > score_candidate(short_clip, event_name, start)


def test_compute_frame_timestamps() -> None:
    assert compute_frame_timestamps(1000.0, num_frames=5) == [
        150.0,
        325.0,
        500.0,
        675.0,
        850.0,
    ]
    assert compute_frame_timestamps(1000.0, num_frames=1) == [500.0]
    assert compute_frame_timestamps(1000.0, num_frames=0) == []
    assert compute_frame_timestamps(None, num_frames=3) == [20.0, 50.0, 80.0]
    assert len(compute_frame_timestamps(10.0, num_frames=4)) == 4


def test_select_pending_events_filters_blank_and_future() -> None:
    rows = [
        {
            "event_id": "1",
            "ball_sponsor": "",
            "table_sponsor": "DHS",
            "start_date": "2026-01-07",
            "event_name": "Already Filled 2026",
            "table_sponsor_source": "",
        },
        {
            "event_id": "2",
            "ball_sponsor": "",
            "table_sponsor": "",
            "start_date": "2026-02-02",
            "event_name": "Past Pending 2026",
            "table_sponsor_source": "",
        },
        {
            "event_id": "3",
            "ball_sponsor": "",
            "table_sponsor": "",
            "start_date": "2026-12-20",
            "event_name": "Future Event 2026",
            "table_sponsor_source": "",
        },
        {
            "event_id": "4",
            "ball_sponsor": "",
            "table_sponsor": "",
            "start_date": "2026-03-02",
            "event_name": "Second Pending 2026",
            "table_sponsor_source": "",
        },
    ]
    today = date(2026, 11, 23)

    pending = select_pending_events(rows, today=today)
    assert [e.event_id for e in pending] == [2, 4]

    limited = select_pending_events(rows, limit=1, today=today)
    assert [e.event_id for e in limited] == [2]

    # Explicit event_ids allows calibrating on already-filled or future events.
    explicit = select_pending_events(rows, event_ids=[1, 3], today=today)
    assert [e.event_id for e in explicit] == [1, 3]


def test_search_and_resolve_video_skips_out_of_window_candidate() -> None:
    event = PendingEvent(
        event_id=3266,
        event_name="WTT Feeder Cappadocia 2026",
        start_date=date(2026, 2, 2),
    )

    class FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            self.opts = opts

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            assert download is False
            if url.startswith("ytsearch"):
                return {
                    "entries": [
                        {
                            "id": "wrong_date",
                            "title": "WTT Feeder Cappadocia 2026 Preview",
                            "channel": "World Table Tennis",
                            "duration": 600,
                        },
                        {
                            "id": "valid_match",
                            "title": "WTT Feeder Cappadocia 2026 Match",
                            "channel": "World Table Tennis",
                            "duration": 1200,
                        },
                    ]
                }
            if "wrong_date" in url:
                return {
                    "id": "wrong_date",
                    "upload_date": "20250205",
                    "url": "https://cdn.example/old.mp4",
                }
            return {
                "id": "valid_match",
                "title": "WTT Feeder Cappadocia 2026 Match",
                "channel": "World Table Tennis",
                "webpage_url": "https://www.youtube.com/watch?v=valid_match",
                "upload_date": "20260204",
                "duration": 1200,
                "url": "https://cdn.example/valid.mp4",
                "http_headers": {"User-Agent": "TestAgent/1.0"},
            }

    resolved = search_and_resolve_video(event, ydl_factory=FakeYDL)
    assert resolved is not None
    assert resolved.video_id == "valid_match"
    assert resolved.stream_url == "https://cdn.example/valid.mp4"
    assert resolved.upload_date == date(2026, 2, 4)
    assert resolved.http_headers == {"User-Agent": "TestAgent/1.0"}


def test_extract_frames_creates_jpegs_and_tolerates_single_timeout(
    tmp_path: Path,
) -> None:
    video = ResolvedVideo(
        video_id="vid1",
        webpage_url="https://www.youtube.com/watch?v=vid1",
        stream_url="https://cdn.example/stream.mp4",
        title="Sample",
        channel="WTT",
        duration=1000.0,
        upload_date=date(2026, 2, 4),
        http_headers={"User-Agent": "CustomUA"},
    )
    calls: list[list[str]] = []

    def fake_runner(cmd: list[str], **kwargs: Any) -> Any:
        calls.append(cmd)
        if len(calls) == 2:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)
        out_path = Path(cmd[-1])
        out_path.write_bytes(b"\xff\xd8\xff\xe0fakejpeg")
        return SimpleNamespace(returncode=0, stderr="")

    frames = extract_frames(
        video,
        tmp_path,
        num_frames=3,
        ffmpeg_bin="/usr/bin/ffmpeg",
        runner=fake_runner,
    )

    assert len(calls) == 3
    assert "-user_agent" in calls[0]
    assert "CustomUA" in calls[0]
    assert len(frames) == 2
    assert all(p.exists() for p in frames)


def test_identify_table_manufacturer_sends_all_frames_in_one_call(
    tmp_path: Path,
) -> None:
    f1 = tmp_path / "frame_01.jpg"
    f2 = tmp_path / "frame_02.jpg"
    f1.write_bytes(b"\xff\xd8\xffframe1")
    f2.write_bytes(b"\xff\xd8\xffframe2")

    captured: dict[str, Any] = {}

    class FakeModels:
        def generate_content(
            self,
            *,
            model: str,
            contents: Sequence[Any],
            config: Any,
        ) -> Any:
            captured["model"] = model
            captured["contents"] = list(contents)
            captured["config"] = config
            return SimpleNamespace(
                text=(
                    '{"table_manufacturer": "Tibhar", "confidence": "high", '
                    '"other_brand_name": null, '
                    '"evidence": "Clear TIBHAR logo on table side apron in frame 1."}'
                )
            )

    fake_client = SimpleNamespace(models=FakeModels())
    event = PendingEvent(
        event_id=3268,
        event_name="WTT Feeder Otocec 2026",
        start_date=date(2026, 3, 7),
    )

    result = identify_table_manufacturer(
        [f1, f2],
        event=event,
        api_key="test-key",
        model="gemini-3.8-flash",
        client=fake_client,
    )

    assert result.table_manufacturer == "Tibhar"
    assert result.confidence == "high"
    # 2 image parts + 1 text prompt in a single API call.
    assert len(captured["contents"]) == 3
    assert captured["model"] == "gemini-3.8-flash"


def test_apply_identifications_respects_confidence_and_never_overwrites() -> None:
    rows = [
        {
            "event_id": "10",
            "ball_sponsor": "",
            "table_sponsor": "DHS",
            "start_date": "2026-01-01",
            "event_name": "Existing Table",
            "table_sponsor_source": "",
        },
        {
            "event_id": "20",
            "ball_sponsor": "",
            "table_sponsor": "",
            "start_date": "2026-02-01",
            "event_name": "High Confidence Event",
            "table_sponsor_source": "",
        },
        {
            "event_id": "30",
            "ball_sponsor": "",
            "table_sponsor": "",
            "start_date": "2026-03-01",
            "event_name": "Medium Confidence Event",
            "table_sponsor_source": "",
        },
    ]
    outcomes = [
        EventIdentificationOutcome(
            event_id=10,
            event_name="Existing Table",
            status="identified",
            manufacturer="Butterfly",
            confidence="high",
        ),
        EventIdentificationOutcome(
            event_id=20,
            event_name="High Confidence Event",
            status="identified",
            manufacturer="Stag",
            confidence="high",
        ),
        EventIdentificationOutcome(
            event_id=30,
            event_name="Medium Confidence Event",
            status="identified",
            manufacturer="Donic",
            confidence="medium",
        ),
    ]

    high_only, count_high = apply_identifications(rows, outcomes, min_confidence="high")
    assert count_high == 1
    assert high_only[0]["table_sponsor"] == "DHS"
    assert high_only[0]["table_sponsor_source"] == ""
    assert high_only[1]["table_sponsor"] == "Stag"
    assert high_only[1]["table_sponsor_source"] == AUTO_SOURCE_TAG
    assert high_only[2]["table_sponsor"] == ""
    assert high_only[2]["table_sponsor_source"] == ""

    with_medium, count_med = apply_identifications(
        rows, outcomes, min_confidence="medium"
    )
    assert count_med == 2
    assert with_medium[2]["table_sponsor"] == "Donic"
    assert with_medium[2]["table_sponsor_source"] == AUTO_SOURCE_TAG


def test_identify_missing_sponsors_end_to_end_and_error_isolation(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "event_sponsors_manual.csv"
    initial_rows = [
        {
            "event_id": "3266",
            "ball_sponsor": "",
            "table_sponsor": "",
            "start_date": "2026-02-02",
            "event_name": "WTT Feeder Cappadocia 2026",
            "table_sponsor_source": "",
        },
        {
            "event_id": "3267",
            "ball_sponsor": "",
            "table_sponsor": "",
            "start_date": "2026-03-02",
            "event_name": "WTT Feeder Dusseldorf 2026",
            "table_sponsor_source": "",
        },
    ]
    csv_path.write_text(render_manual_sponsors_csv(initial_rows), encoding="utf-8")

    def fake_search(event: PendingEvent) -> ResolvedVideo | None:
        if event.event_id == 3266:
            raise RuntimeError("transient YouTube error")
        return ResolvedVideo(
            video_id="v3267",
            webpage_url="https://www.youtube.com/watch?v=v3267",
            stream_url="https://cdn.example/v3267.mp4",
            title=event.event_name,
            channel="World Table Tennis",
            duration=1200.0,
            upload_date=date(2026, 3, 4),
            http_headers={},
        )

    def fake_extract(
        video: ResolvedVideo,
        output_dir: Path,
        *,
        num_frames: int = 6,
    ) -> list[Path]:
        output_dir.mkdir(parents=True, exist_ok=True)
        frame = output_dir / "frame_01.jpg"
        frame.write_bytes(b"\xff\xd8\xffframe")
        return [frame]

    def fake_identify(
        frame_paths: Sequence[Path],
        *,
        event: PendingEvent,
        api_key: str,
        model: str,
    ) -> TableIdentification:
        return TableIdentification(
            table_manufacturer="Donic",
            confidence="high",
            evidence="DONIC printed on side edge of table.",
        )

    frames_dir = tmp_path / "saved_frames"

    # Dry run does not write CSV.
    dry_summary = identify_missing_sponsors(
        csv_path=csv_path,
        api_key="key",
        dry_run=True,
        sleep_seconds=0.0,
        today=date(2026, 11, 23),
        search_fn=fake_search,
        extract_fn=fake_extract,
        identify_fn=fake_identify,
    )
    assert dry_summary.wrote_file is False
    assert dry_summary.updated == 1
    assert dry_summary.errors == 1
    assert read_manual_sponsors_csv(csv_path)[1]["table_sponsor"] == ""

    # Live run writes CSV and saves frames when requested.
    live_summary = identify_missing_sponsors(
        csv_path=csv_path,
        api_key="key",
        dry_run=False,
        save_frames=True,
        frames_dir=frames_dir,
        sleep_seconds=0.0,
        today=date(2026, 11, 23),
        search_fn=fake_search,
        extract_fn=fake_extract,
        identify_fn=fake_identify,
    )
    assert live_summary.wrote_file is True
    assert live_summary.updated == 1
    assert live_summary.errors == 1
    assert (frames_dir / "3267" / "frame_01.jpg").exists()

    reloaded = read_manual_sponsors_csv(csv_path)
    assert reloaded[0]["table_sponsor"] == ""
    assert reloaded[1]["table_sponsor"] == "Donic"
    assert reloaded[1]["table_sponsor_source"] == AUTO_SOURCE_TAG


def test_parse_event_ids_supports_spaces_and_commas() -> None:
    assert _parse_event_ids(None) is None
    assert _parse_event_ids(["2603", "3250"]) == [2603, 3250]
    assert _parse_event_ids(["2603,3250", "3251"]) == [2603, 3250, 3251]


def test_main_errors_cleanly_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "   ")
    monkeypatch.setattr(
        "tt_stats.event_sponsors.video_identify.get_settings",
        lambda: SimpleNamespace(gemini_api_key=None, gemini_model="gemini-2.5-flash"),
    )
    monkeypatch.setattr(
        "tt_stats.event_sponsors.video_identify.configure_logging",
        lambda: None,
    )

    rc = main(["--dry-run"])
    assert rc == 1
    assert "GEMINI_API_KEY" in capsys.readouterr().out


def test_accented_city_names_fold_to_ascii_tokens() -> None:
    assert "lasko" in _distinctive_tokens("WTT Contender Laško 2021")
    assert "dusseldorf" in _distinctive_tokens("WTT Feeder Düsseldorf 2026")
    assert "asuncion" in _distinctive_tokens("WTT Feeder Asunción 2026")


def test_search_skips_playlists_livestreams_and_failed_resolutions() -> None:
    event = PendingEvent(
        event_id=3267,
        event_name="WTT Feeder Düsseldorf 2026",
        start_date=date(2026, 3, 2),
    )

    class FakeYDL:
        def __init__(self, opts: dict[str, Any]) -> None:
            self.opts = opts

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
            if url.startswith("ytsearch"):
                return {
                    "entries": [
                        {
                            "_type": "playlist",
                            "id": "PL123",
                            "title": "WTT Feeder Dusseldorf 2026 Playlist",
                            "channel": "World Table Tennis",
                        },
                        {
                            "id": "live1",
                            "title": "WTT Feeder Dusseldorf 2026 LIVE",
                            "channel": "World Table Tennis",
                            "duration": 1800,
                        },
                        {
                            "id": "broken1",
                            "title": "WTT Feeder Dusseldorf 2026 Match A",
                            "channel": "World Table Tennis",
                            "duration": 1200,
                        },
                        {
                            "id": "good1",
                            "title": "WTT Feeder Dusseldorf 2026 Match B",
                            "channel": "World Table Tennis",
                            "duration": 1200,
                        },
                    ]
                }
            if "live1" in url:
                return {"id": "live1", "is_live": True, "url": "https://cdn/live.m3u8"}
            if "broken1" in url:
                raise RuntimeError("video unavailable")
            return {
                "id": "good1",
                "title": "WTT Feeder Dusseldorf 2026 Match B",
                "channel": "World Table Tennis",
                "webpage_url": "https://www.youtube.com/watch?v=good1",
                "upload_date": "20260303",
                "duration": 1200,
                "url": "https://cdn.example/good1.mp4",
            }

    resolved = search_and_resolve_video(event, ydl_factory=FakeYDL)
    assert resolved is not None
    assert resolved.video_id == "good1"


def test_classify_identification_handles_unknown_other_and_low_confidence() -> None:
    event = PendingEvent(event_id=1, event_name="Sample 2026", start_date=None)
    video = ResolvedVideo(
        video_id="v1",
        webpage_url="https://www.youtube.com/watch?v=v1",
        stream_url="https://cdn/v1.mp4",
        title="Sample",
        channel="WTT",
        duration=600.0,
        upload_date=None,
        http_headers={},
    )

    unknown = _classify_identification(
        event,
        video,
        TableIdentification(
            table_manufacturer="Unknown",
            confidence="low",
            evidence="Table obscured.",
        ),
        min_confidence="high",
    )
    assert unknown.status == "skipped_unknown"

    other = _classify_identification(
        event,
        video,
        TableIdentification(
            table_manufacturer="Other",
            confidence="high",
            other_brand_name="Cornilleau",
            evidence="Cornilleau visible.",
        ),
        min_confidence="high",
    )
    assert other.status == "skipped_unknown"
    assert other.manufacturer == "Cornilleau"

    low_conf = _classify_identification(
        event,
        video,
        TableIdentification(
            table_manufacturer="Stag",
            confidence="medium",
            evidence="Blurry logo.",
        ),
        min_confidence="high",
    )
    assert low_conf.status == "skipped_low_confidence"


def test_identify_table_manufacturer_rejects_empty_frames_and_empty_response(
    tmp_path: Path,
) -> None:
    event = PendingEvent(event_id=1, event_name="Sample 2026", start_date=None)
    with pytest.raises(ValueError, match="At least one frame"):
        identify_table_manufacturer([], event=event, api_key="k")

    frame = tmp_path / "f1.jpg"
    frame.write_bytes(b"\xff\xd8\xff")
    empty_client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=lambda **kwargs: SimpleNamespace(text="")
        )
    )
    with pytest.raises(RuntimeError, match="empty response"):
        identify_table_manufacturer(
            [frame],
            event=event,
            api_key="k",
            client=empty_client,
        )


@pytest.mark.parametrize(
    "bad_argv",
    [
        ["--event-ids", "abc"],
        ["--limit", "0"],
        ["--frames", "0"],
        ["--sleep-seconds", "-1"],
    ],
)
def test_cli_validation_rejects_invalid_arguments(
    bad_argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tt_stats.event_sponsors.video_identify.configure_logging",
        lambda: None,
    )
    with pytest.raises(SystemExit) as exc_info:
        main(bad_argv)
    assert exc_info.value.code == 2
