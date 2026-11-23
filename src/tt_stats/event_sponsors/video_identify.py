"""Identify missing ``table_sponsor`` values using YouTube frames and Gemini Vision.

Pipeline per pending event in ``dbt/seeds/event_sponsors_manual.csv``:
1. Search YouTube via ``yt-dlp`` using the normalized event name and start year.
2. Score candidates by official channel, year/keyword match, duration, and upload
   date window, then resolve a direct ``<=720p`` stream URL without downloading
   the full video.
3. Extract evenly spaced JPEG frames via ``ffmpeg`` (using HTTP range seeks).
4. Send all frames for the event together in one Gemini Vision API call with a
   structured JSON schema constrained to canonical table manufacturers.
5. Update ``table_sponsor`` and set ``table_sponsor_source = 'gemini_youtube'``
   without ever overwriting existing non-empty values.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import tempfile
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import imageio_ffmpeg
import yt_dlp
from google import genai
from google.genai import types
from pydantic import BaseModel, Field
from tqdm import tqdm

from tt_stats.core.config import get_settings, project_root
from tt_stats.core.logging_config import configure_logging, get_logger
from tt_stats.event_sponsors.manual_sync import (
    DEFAULT_CSV_PATH,
    read_manual_sponsors_csv,
    render_manual_sponsors_csv,
)

logger = get_logger(__name__)

AUTO_SOURCE_TAG = "gemini_youtube"

CANONICAL_MANUFACTURERS: tuple[str, ...] = (
    "Andro",
    "Butterfly",
    "DHS",
    "Donic",
    "Double Fish",
    "Joola",
    "Nittaku",
    "San-Ei",
    "Stag",
    "Stiga",
    "Tibhar",
    "Yinhe",
)

ManufacturerLabel = Literal[
    "Andro",
    "Butterfly",
    "DHS",
    "Donic",
    "Double Fish",
    "Joola",
    "Nittaku",
    "San-Ei",
    "Stag",
    "Stiga",
    "Tibhar",
    "Yinhe",
    "Other",
    "Unknown",
]
ConfidenceLevel = Literal["high", "medium", "low"]
OutcomeStatus = Literal[
    "identified",
    "skipped_low_confidence",
    "skipped_unknown",
    "no_video",
    "no_frames",
    "error",
]

CONFIDENCE_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}
OFFICIAL_CHANNEL_TOKENS: tuple[str, ...] = (
    "world table tennis",
    "wtt",
    "ittf",
)

DEFAULT_FRAME_COUNT = 6
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
DEFAULT_SEARCH_RESULTS = 5
DEFAULT_MAX_CANDIDATE_CHECKS = 3
DEFAULT_FFMPEG_TIMEOUT_SECONDS = 30
DEFAULT_SLEEP_SECONDS = 1.0
UPLOAD_DATE_BEFORE_DAYS = 3
UPLOAD_DATE_AFTER_DAYS = 60
DEFAULT_FORMAT_SELECTOR = "bestvideo[height<=720][ext=mp4]/best[height<=720]/best"
DEFAULT_FRAMES_DIR = project_root() / "data" / "sponsor_frames"

_PRESENTED_BY_RE = re.compile(r"\bpresented\s+by\b.*$", re.IGNORECASE)
_PAREN_SUFFIX_RE = re.compile(r"\([^)]*\)")
_YEAR_RE = re.compile(r"\b(20\d{2})\b")
_WORD_RE = re.compile(r"[a-z0-9]+")
_GENERIC_EVENT_TOKENS: frozenset[str] = frozenset(
    {
        "wtt",
        "ittf",
        "contender",
        "star",
        "feeder",
        "champions",
        "smash",
        "finals",
        "cup",
        "world",
        "table",
        "tennis",
        "championships",
        "international",
        "open",
        "senior",
        "men",
        "women",
        "mixed",
        "team",
        "the",
        "and",
        "of",
        "by",
        "ii",
        "iii",
    }
)

IDENTIFICATION_PROMPT = """You are inspecting broadcast frames from a professional
table tennis tournament. Your task is to identify the TABLE MANUFACTURER
(the brand that made the playing table itself).

CRITICAL RULES:
1. Look ONLY at branding printed on the TABLE ITSELF: the side apron, end edge,
   table legs, or underside frame.
2. IGNORE all court surround barriers, LED boards, floor mats, scoreboards,
   umpire tables, and player shirts -- tournaments frequently display different
   ball sponsors or event sponsors on the barriers and floor.
3. Cross-check across all provided frames; use the clearest, unobstructed views
   of the table.
4. Map the brand to one of these canonical names exactly:
   Andro, Butterfly, DHS, Donic, Double Fish, Joola, Nittaku, San-Ei, Stag,
   Stiga, Tibhar, Yinhe.
   - If you clearly see another table manufacturer not in that list, return
     "Other" and put its name in other_brand_name.
   - If the table branding is not legible or not clearly visible in any frame,
     return "Unknown" with confidence "low".
5. Set confidence:
   - "high": The brand name or unmistakable logo is clearly legible on the
     table itself in at least one frame.
   - "medium": Partial logo or strong visual cues on the table itself,
     slightly blurry or obscured.
   - "low": Unclear, obscured, or only guessing.
6. In `evidence`, briefly describe what you saw on the table itself and which
   frame(s) showed it.
"""


class TableIdentification(BaseModel):
    """Structured Gemini Vision response for one tournament."""

    table_manufacturer: ManufacturerLabel
    confidence: ConfidenceLevel
    other_brand_name: str | None = Field(default=None)
    evidence: str


@dataclass(frozen=True)
class PendingEvent:
    """One CSV event queued for table manufacturer identification."""

    event_id: int
    event_name: str
    start_date: date | None


@dataclass(frozen=True)
class VideoCandidate:
    """One flat YouTube search result returned by ``yt-dlp``."""

    video_id: str
    url: str
    title: str
    channel: str
    duration: float | None
    upload_date: date | None


@dataclass(frozen=True)
class ResolvedVideo:
    """A verified YouTube video with a direct stream URL ready for ``ffmpeg``."""

    video_id: str
    webpage_url: str
    stream_url: str
    title: str
    channel: str
    duration: float | None
    upload_date: date | None
    http_headers: dict[str, str]


@dataclass(frozen=True)
class EventIdentificationOutcome:
    """Result of running the identification pipeline on one event."""

    event_id: int
    event_name: str
    status: OutcomeStatus
    manufacturer: str | None = None
    confidence: str | None = None
    evidence: str | None = None
    video_url: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class IdentifyRunSummary:
    """Aggregate summary of one ``tt-event-sponsors-identify`` run."""

    csv_path: Path
    candidates: int
    updated: int
    skipped: int
    errors: int
    wrote_file: bool
    outcomes: tuple[EventIdentificationOutcome, ...]


def clean_event_name(event_name: str) -> str:
    """Strip sponsor suffixes and parenthetical notes for cleaner YouTube search."""
    cleaned = _PRESENTED_BY_RE.sub("", event_name)
    cleaned = _PAREN_SUFFIX_RE.sub("", cleaned)
    cleaned = cleaned.replace("\u00a0", " ")
    return " ".join(cleaned.split()).strip(" -\u2013\u2014")


def build_search_query(event_name: str, start_date: date | None) -> str:
    """Construct a YouTube search query from event name and optional start date."""
    cleaned = clean_event_name(event_name)
    if start_date is not None and str(start_date.year) not in cleaned:
        cleaned = f"{cleaned} {start_date.year}"
    return f"{cleaned} table tennis match"


def parse_iso_date(raw: str | None) -> date | None:
    """Parse an ISO ``YYYY-MM-DD`` string into a date, returning None if blank."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_yt_upload_date(raw: Any) -> date | None:
    """Parse a ``yt-dlp`` ``YYYYMMDD`` upload_date string into a date."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def is_within_date_window(
    upload_date: date | None,
    start_date: date | None,
    *,
    before_days: int = UPLOAD_DATE_BEFORE_DAYS,
    after_days: int = UPLOAD_DATE_AFTER_DAYS,
) -> bool:
    """Return True when ``upload_date`` is near ``start_date`` (or either is unset)."""
    if upload_date is None or start_date is None:
        return True
    earliest = start_date - timedelta(days=before_days)
    latest = start_date + timedelta(days=after_days)
    return earliest <= upload_date <= latest


def _fold_ascii(text: str) -> str:
    """Fold Unicode accents (e.g. Düsseldorf, Laško) to lowercase ASCII."""
    return (
        unicodedata.normalize("NFKD", text)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )


def _distinctive_tokens(event_name: str) -> set[str]:
    """Return location/tournament-specific tokens from an event name."""
    cleaned = _fold_ascii(clean_event_name(event_name))
    tokens = set(_WORD_RE.findall(cleaned))
    return {
        tok
        for tok in tokens
        if len(tok) >= 3 and tok not in _GENERIC_EVENT_TOKENS and not tok.isdigit()
    }


def score_candidate(
    candidate: VideoCandidate,
    event_name: str,
    start_date: date | None,
) -> float:
    """Score a YouTube search candidate; higher is a better match."""
    score = 0.0
    title_lower = _fold_ascii(candidate.title)
    channel_lower = _fold_ascii(candidate.channel)

    if any(token in channel_lower for token in OFFICIAL_CHANNEL_TOKENS):
        score += 30.0

    # Prefer videos whose title mentions the tournament's location/distinctive words.
    distinctive = _distinctive_tokens(event_name)
    if distinctive:
        matches = sum(1 for tok in distinctive if tok in title_lower)
        score += 15.0 * (matches / len(distinctive))

    # Year alignment in title prevents grabbing an earlier/later edition.
    if start_date is not None:
        target_year = str(start_date.year)
        title_years = set(_YEAR_RE.findall(candidate.title))
        if target_year in title_years:
            score += 20.0
        elif title_years:
            score -= 40.0

    # Upload date window check when upload_date is already available.
    if candidate.upload_date is not None and start_date is not None:
        if is_within_date_window(candidate.upload_date, start_date):
            score += 25.0
        else:
            score -= 60.0

    # Duration preference: match/highlight videos (90s..60m) beat Shorts/livestreams.
    if candidate.duration is not None:
        if 90 <= candidate.duration <= 3600:
            score += 15.0
        elif 3600 < candidate.duration <= 14400:
            score += 5.0
        elif candidate.duration < 60:
            score -= 35.0
        else:
            score -= 10.0

    return score


def compute_frame_timestamps(
    duration: float | None,
    num_frames: int = DEFAULT_FRAME_COUNT,
) -> list[float]:
    """Compute evenly spaced seek timestamps (seconds) across a video."""
    if num_frames <= 0:
        return []
    if duration is None or duration <= 0:
        return [float(20 + i * 30) for i in range(num_frames)]
    if duration <= 12:
        step = duration / (num_frames + 1)
        return [round(step * (i + 1), 2) for i in range(num_frames)]

    start = duration * 0.15
    end = duration * 0.85
    if num_frames == 1:
        return [round((start + end) / 2.0, 2)]
    step = (end - start) / (num_frames - 1)
    return [round(start + i * step, 2) for i in range(num_frames)]


def select_pending_events(
    rows: Sequence[Mapping[str, str]],
    *,
    event_ids: Sequence[int] | None = None,
    limit: int | None = None,
    today: date | None = None,
) -> list[PendingEvent]:
    """Select events from the manual sponsors CSV to process.

    When ``event_ids`` is given, those exact events are returned in CSV order
    (useful for targeted runs or ``--dry-run`` calibration on known events).
    Otherwise, only rows with a blank ``table_sponsor`` and a ``start_date`` on
    or before ``today`` (or blank) are returned.
    """
    reference_day = today or date.today()
    requested_ids = set(event_ids) if event_ids is not None else None
    selected: list[PendingEvent] = []

    for row in rows:
        raw_id = (row.get("event_id") or "").strip()
        if not raw_id:
            continue
        event_id = int(raw_id)
        event_name = (row.get("event_name") or "").strip()
        start_date = parse_iso_date(row.get("start_date"))

        if requested_ids is not None:
            if event_id not in requested_ids:
                continue
        else:
            if (row.get("table_sponsor") or "").strip():
                continue
            if start_date is not None and start_date > reference_day:
                continue

        if not event_name:
            logger.warning("Skipping event_id=%d: blank event_name", event_id)
            continue

        selected.append(
            PendingEvent(
                event_id=event_id,
                event_name=event_name,
                start_date=start_date,
            )
        )

    if limit is not None:
        return selected[:limit]
    return selected


def resolve_ffmpeg_executable() -> str:
    """Return the system ``ffmpeg`` path, falling back to ``imageio-ffmpeg``."""
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    return str(imageio_ffmpeg.get_ffmpeg_exe())


def _candidate_from_flat_entry(entry: Mapping[str, Any]) -> VideoCandidate | None:
    if entry.get("_type") == "playlist":
        return None
    video_id = str(entry.get("id") or "").strip()
    if not video_id:
        return None
    raw_url = str(entry.get("url") or "").strip()
    if raw_url.startswith("http"):
        if "/playlist?" in raw_url or "/channel/" in raw_url or "/@" in raw_url:
            return None
        url = raw_url
    else:
        url = f"https://www.youtube.com/watch?v={video_id}"
    raw_duration = entry.get("duration")
    duration = float(raw_duration) if isinstance(raw_duration, (int, float)) else None
    channel = str(entry.get("channel") or entry.get("uploader") or "").strip()
    return VideoCandidate(
        video_id=video_id,
        url=url,
        title=str(entry.get("title") or "").strip(),
        channel=channel,
        duration=duration,
        upload_date=parse_yt_upload_date(entry.get("upload_date")),
    )


def search_and_resolve_video(
    event: PendingEvent,
    *,
    search_results: int = DEFAULT_SEARCH_RESULTS,
    max_checks: int = DEFAULT_MAX_CANDIDATE_CHECKS,
    ydl_factory: Callable[[dict[str, Any]], Any] = yt_dlp.YoutubeDL,
) -> ResolvedVideo | None:
    """Search YouTube for ``event`` and resolve the highest-ranked valid video."""
    query = build_search_query(event.event_name, event.start_date)
    logger.info("Searching YouTube for event_id=%d: %s", event.event_id, query)

    flat_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
    }
    with ydl_factory(flat_opts) as ydl:
        search_info = ydl.extract_info(
            f"ytsearch{search_results}:{query}",
            download=False,
        )

    entries = search_info.get("entries") if isinstance(search_info, Mapping) else None
    if not entries:
        return None

    candidates = [
        cand
        for entry in entries
        if isinstance(entry, Mapping)
        and (cand := _candidate_from_flat_entry(entry)) is not None
    ]
    if not candidates:
        return None

    ranked = sorted(
        candidates,
        key=lambda c: score_candidate(c, event.event_name, event.start_date),
        reverse=True,
    )

    resolve_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "format": DEFAULT_FORMAT_SELECTOR,
    }
    with ydl_factory(resolve_opts) as ydl:
        for candidate in ranked[:max_checks]:
            try:
                info = ydl.extract_info(candidate.url, download=False)
            except Exception as exc:
                logger.warning(
                    "Failed to resolve candidate %s for event_id=%d: %s",
                    candidate.video_id,
                    event.event_id,
                    exc,
                )
                continue

            if not isinstance(info, Mapping):
                continue

            if info.get("is_live"):
                logger.info(
                    "Rejecting live stream %s for event_id=%d",
                    candidate.video_id,
                    event.event_id,
                )
                continue

            upload_date = parse_yt_upload_date(info.get("upload_date"))
            if not is_within_date_window(upload_date, event.start_date):
                logger.info(
                    "Rejecting candidate %s (%s) for event_id=%d: "
                    "upload_date=%s outside window for start_date=%s",
                    candidate.video_id,
                    candidate.title,
                    event.event_id,
                    upload_date,
                    event.start_date,
                )
                continue

            stream_url = str(info.get("url") or "").strip()
            if not stream_url:
                continue

            raw_duration = info.get("duration")
            duration = (
                float(raw_duration)
                if isinstance(raw_duration, (int, float))
                else candidate.duration
            )
            raw_headers = info.get("http_headers")
            http_headers = (
                {str(k): str(v) for k, v in raw_headers.items()}
                if isinstance(raw_headers, Mapping)
                else {}
            )
            webpage_url = str(info.get("webpage_url") or candidate.url)
            return ResolvedVideo(
                video_id=candidate.video_id,
                webpage_url=webpage_url,
                stream_url=stream_url,
                title=str(info.get("title") or candidate.title),
                channel=str(
                    info.get("channel") or info.get("uploader") or candidate.channel
                ),
                duration=duration,
                upload_date=upload_date,
                http_headers=http_headers,
            )

    return None


def extract_frames(
    video: ResolvedVideo,
    output_dir: Path,
    *,
    num_frames: int = DEFAULT_FRAME_COUNT,
    ffmpeg_bin: str | None = None,
    timeout_seconds: int = DEFAULT_FFMPEG_TIMEOUT_SECONDS,
    runner: Callable[..., Any] = subprocess.run,
) -> list[Path]:
    """Extract evenly spaced JPEG frames from ``video.stream_url`` via ``ffmpeg``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    executable = ffmpeg_bin or resolve_ffmpeg_executable()
    timestamps = compute_frame_timestamps(video.duration, num_frames=num_frames)
    user_agent = video.http_headers.get("User-Agent")

    extracted: list[Path] = []
    for idx, ts in enumerate(timestamps, start=1):
        frame_path = output_dir / f"frame_{idx:02d}.jpg"
        cmd: list[str] = [executable, "-hide_banner", "-loglevel", "error"]
        if user_agent:
            cmd.extend(["-user_agent", user_agent])
        cmd.extend(
            [
                "-ss",
                f"{ts:.2f}",
                "-i",
                video.stream_url,
                "-frames:v",
                "1",
                "-q:v",
                "2",
                "-y",
                str(frame_path),
            ]
        )
        try:
            proc = runner(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.warning("ffmpeg timed out at %.2fs for video %s", ts, video.video_id)
            continue

        if (
            proc.returncode == 0
            and frame_path.exists()
            and frame_path.stat().st_size > 0
        ):
            extracted.append(frame_path)
        else:
            logger.warning(
                "ffmpeg failed at %.2fs for video %s (rc=%s): %s",
                ts,
                video.video_id,
                proc.returncode,
                (proc.stderr or "").strip(),
            )

    return extracted


def identify_table_manufacturer(
    frame_paths: Sequence[Path],
    *,
    event: PendingEvent,
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL,
    client: Any | None = None,
) -> TableIdentification:
    """Send all extracted JPEG frames in one Gemini Vision call and parse JSON."""
    if not frame_paths:
        raise ValueError("At least one frame is required for Gemini identification")

    genai_client = client if client is not None else genai.Client(api_key=api_key)
    parts: list[Any] = [
        types.Part.from_bytes(data=path.read_bytes(), mime_type="image/jpeg")
        for path in frame_paths
    ]
    event_context = (
        f"Tournament: {event.event_name}\n"
        f"Start date: {event.start_date.isoformat() if event.start_date else 'unknown'}"
        f"\nFrames provided: {len(frame_paths)}\n\n"
        f"{IDENTIFICATION_PROMPT}"
    )
    parts.append(event_context)

    response = genai_client.models.generate_content(
        model=model,
        contents=parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=TableIdentification,
            temperature=0.0,
        ),
    )
    raw_text = (response.text or "").strip()
    if not raw_text:
        raise RuntimeError("Gemini returned an empty response")
    return TableIdentification.model_validate_json(raw_text)


def apply_identifications(
    rows: Sequence[Mapping[str, str]],
    outcomes: Sequence[EventIdentificationOutcome],
    *,
    min_confidence: ConfidenceLevel = "high",
) -> tuple[list[dict[str, str]], int]:
    """Apply qualifying identifications without overwriting existing table sponsors."""
    min_rank = CONFIDENCE_RANK[min_confidence]
    qualifying: dict[int, str] = {}
    for outcome in outcomes:
        if outcome.status != "identified":
            continue
        if outcome.manufacturer not in CANONICAL_MANUFACTURERS:
            continue
        if CONFIDENCE_RANK.get(outcome.confidence or "", -1) < min_rank:
            continue
        qualifying[outcome.event_id] = outcome.manufacturer

    updated_rows: list[dict[str, str]] = []
    updated_count = 0
    for row in rows:
        copy_row = {k: (v or "").strip() for k, v in row.items()}
        copy_row.setdefault("table_sponsor_source", "")
        raw_id = copy_row.get("event_id", "")
        if raw_id and int(raw_id) in qualifying:
            if not copy_row.get("table_sponsor"):
                copy_row["table_sponsor"] = qualifying[int(raw_id)]
                copy_row["table_sponsor_source"] = AUTO_SOURCE_TAG
                updated_count += 1
            else:
                logger.info(
                    "Preserving existing table_sponsor=%r for event_id=%s",
                    copy_row["table_sponsor"],
                    raw_id,
                )
        updated_rows.append(copy_row)

    return updated_rows, updated_count


def write_manual_sponsors_csv_atomic(
    csv_path: Path,
    rows: Sequence[Mapping[str, str]],
) -> None:
    """Write ``event_sponsors_manual.csv`` atomically with UTF-8 LF newlines."""
    rendered = render_manual_sponsors_csv(rows)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{csv_path.name}.",
        suffix=".tmp",
        dir=str(csv_path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(rendered)
        mode = csv_path.stat().st_mode & 0o777 if csv_path.exists() else 0o644
        tmp_path.chmod(mode)
        tmp_path.replace(csv_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _classify_identification(
    event: PendingEvent,
    video: ResolvedVideo,
    result: TableIdentification,
    *,
    min_confidence: ConfidenceLevel,
) -> EventIdentificationOutcome:
    manufacturer = result.table_manufacturer
    if manufacturer in ("Unknown", "Other"):
        label = result.other_brand_name if manufacturer == "Other" else None
        return EventIdentificationOutcome(
            event_id=event.event_id,
            event_name=event.event_name,
            status="skipped_unknown",
            manufacturer=label or manufacturer,
            confidence=result.confidence,
            evidence=result.evidence,
            video_url=video.webpage_url,
        )
    if CONFIDENCE_RANK[result.confidence] < CONFIDENCE_RANK[min_confidence]:
        return EventIdentificationOutcome(
            event_id=event.event_id,
            event_name=event.event_name,
            status="skipped_low_confidence",
            manufacturer=manufacturer,
            confidence=result.confidence,
            evidence=result.evidence,
            video_url=video.webpage_url,
        )
    return EventIdentificationOutcome(
        event_id=event.event_id,
        event_name=event.event_name,
        status="identified",
        manufacturer=manufacturer,
        confidence=result.confidence,
        evidence=result.evidence,
        video_url=video.webpage_url,
    )


def _format_outcome_lines(outcome: EventIdentificationOutcome) -> list[str]:
    """Format one event outcome into human-readable status lines."""
    detail = outcome.manufacturer or outcome.error or "-"
    conf = f" ({outcome.confidence})" if outcome.confidence else ""
    lines = [
        f"[{outcome.status}] {outcome.event_id} | "
        f"{outcome.event_name} -> {detail}{conf}"
    ]
    if outcome.evidence:
        lines.append(f"    evidence: {outcome.evidence}")
    if outcome.video_url:
        lines.append(f"    video:    {outcome.video_url}")
    return lines


def identify_missing_sponsors(
    *,
    csv_path: Path = DEFAULT_CSV_PATH,
    api_key: str,
    model: str = DEFAULT_GEMINI_MODEL,
    event_ids: Sequence[int] | None = None,
    limit: int | None = None,
    min_confidence: ConfidenceLevel = "high",
    num_frames: int = DEFAULT_FRAME_COUNT,
    dry_run: bool = False,
    save_frames: bool = False,
    frames_dir: Path = DEFAULT_FRAMES_DIR,
    sleep_seconds: float = DEFAULT_SLEEP_SECONDS,
    show_progress: bool = False,
    today: date | None = None,
    search_fn: Callable[[PendingEvent], ResolvedVideo | None] = (
        search_and_resolve_video
    ),
    extract_fn: Callable[..., list[Path]] = extract_frames,
    identify_fn: Callable[..., TableIdentification] = identify_table_manufacturer,
) -> IdentifyRunSummary:
    """Run the full YouTube -> frames -> Gemini Vision backfill on ``csv_path``."""
    rows = read_manual_sponsors_csv(csv_path)
    pending = select_pending_events(
        rows,
        event_ids=event_ids,
        limit=limit,
        today=today,
    )

    if show_progress:
        if not pending:
            print(  # noqa: T201
                "Nothing pending: every eligible event already has a table_sponsor."
            )
        else:
            mode_tag = " (dry-run)" if dry_run else ""
            print(  # noqa: T201
                f"Starting table manufacturer identification for {len(pending)} "
                f"event(s) using {model}{mode_tag}..."
            )

    shared_client = (
        genai.Client(api_key=api_key)
        if pending and identify_fn is identify_table_manufacturer
        else None
    )

    outcomes: list[EventIdentificationOutcome] = []
    iterator: Any = (
        tqdm(pending, desc="Identifying tables", unit="event")
        if show_progress and pending
        else pending
    )

    def _set_stage(event_id: int, stage: str) -> None:
        if show_progress and hasattr(iterator, "set_postfix_str"):
            iterator.set_postfix_str(f"event_id={event_id} | {stage}")
            if hasattr(iterator, "refresh"):
                iterator.refresh()

    for index, event in enumerate(iterator):
        if index > 0 and sleep_seconds > 0:
            _set_stage(event.event_id, "waiting")
            time.sleep(sleep_seconds)
        try:
            _set_stage(event.event_id, "finding YouTube video")
            video = search_fn(event)
            if video is None:
                logger.info(
                    "No matching YouTube video found for event_id=%d",
                    event.event_id,
                )
                outcome = EventIdentificationOutcome(
                    event_id=event.event_id,
                    event_name=event.event_name,
                    status="no_video",
                )
            else:
                identify_kwargs: dict[str, Any] = {
                    "event": event,
                    "api_key": api_key,
                    "model": model,
                }
                if shared_client is not None:
                    identify_kwargs["client"] = shared_client

                if save_frames:
                    event_dir = frames_dir / str(event.event_id)
                    _set_stage(event.event_id, "getting frames")
                    frame_paths = extract_fn(video, event_dir, num_frames=num_frames)
                    if not frame_paths:
                        outcome = EventIdentificationOutcome(
                            event_id=event.event_id,
                            event_name=event.event_name,
                            status="no_frames",
                            video_url=video.webpage_url,
                        )
                    else:
                        _set_stage(event.event_id, "parsing with LLM")
                        result = identify_fn(frame_paths, **identify_kwargs)
                        outcome = _classify_identification(
                            event,
                            video,
                            result,
                            min_confidence=min_confidence,
                        )
                else:
                    prefix = f"tt_frames_{event.event_id}_"
                    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
                        _set_stage(event.event_id, "getting frames")
                        frame_paths = extract_fn(
                            video, Path(tmp), num_frames=num_frames
                        )
                        if not frame_paths:
                            outcome = EventIdentificationOutcome(
                                event_id=event.event_id,
                                event_name=event.event_name,
                                status="no_frames",
                                video_url=video.webpage_url,
                            )
                        else:
                            _set_stage(event.event_id, "parsing with LLM")
                            result = identify_fn(frame_paths, **identify_kwargs)
                            outcome = _classify_identification(
                                event,
                                video,
                                result,
                                min_confidence=min_confidence,
                            )
        except Exception as exc:
            logger.exception(
                "Error identifying table sponsor for event_id=%d",
                event.event_id,
            )
            outcome = EventIdentificationOutcome(
                event_id=event.event_id,
                event_name=event.event_name,
                status="error",
                error=str(exc),
            )

        outcomes.append(outcome)
        if show_progress:
            for line in _format_outcome_lines(outcome):
                tqdm.write(line)

    updated_rows, updated_count = apply_identifications(
        rows,
        outcomes,
        min_confidence=min_confidence,
    )
    wrote_file = False
    if updated_count > 0 and not dry_run:
        write_manual_sponsors_csv_atomic(csv_path, updated_rows)
        wrote_file = True

    errors = sum(1 for o in outcomes if o.status == "error")
    skipped = len(outcomes) - updated_count - errors
    return IdentifyRunSummary(
        csv_path=csv_path,
        candidates=len(pending),
        updated=updated_count,
        skipped=max(skipped, 0),
        errors=errors,
        wrote_file=wrote_file,
        outcomes=tuple(outcomes),
    )


def _parse_event_ids(raw_values: Sequence[str] | None) -> list[int] | None:
    """Parse space- and/or comma-separated integer event IDs."""
    if not raw_values:
        return None
    ids: list[int] = []
    for token in raw_values:
        for part in token.split(","):
            stripped = part.strip()
            if stripped:
                ids.append(int(stripped))
    return ids or None


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Identify missing table_sponsor values in "
            "dbt/seeds/event_sponsors_manual.csv using YouTube frames "
            "(yt-dlp + ffmpeg) and the Gemini Vision API."
        )
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV_PATH,
        help=f"Target manual sponsors CSV path (default: {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--event-ids",
        nargs="+",
        default=None,
        help="Explicit event IDs (space- or comma-separated) to inspect.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of pending events to process.",
    )
    parser.add_argument(
        "--min-confidence",
        choices=("high", "medium"),
        default="high",
        help="Minimum Gemini confidence to write table_sponsor (default: high).",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=DEFAULT_FRAME_COUNT,
        help=(
            f"Number of video frames to extract per event "
            f"(default: {DEFAULT_FRAME_COUNT})."
        ),
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help=(
            f"Gemini model override (defaults to GEMINI_MODEL or "
            f"{DEFAULT_GEMINI_MODEL})."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run search, frame extraction, and Gemini without modifying the CSV.",
    )
    parser.add_argument(
        "--save-frames",
        action="store_true",
        help=f"Save extracted frames under {DEFAULT_FRAMES_DIR}/<event_id>/.",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help=(
            f"Pause between events to stay polite to YouTube "
            f"(default: {DEFAULT_SLEEP_SECONDS})."
        ),
    )
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be a positive integer")
    if args.frames < 1:
        parser.error("--frames must be at least 1")
    if args.sleep_seconds < 0:
        parser.error("--sleep-seconds must be >= 0")
    try:
        args.parsed_event_ids = _parse_event_ids(args.event_ids)
    except ValueError as exc:
        parser.error(f"invalid --event-ids value: {exc}")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    """Console entry point used by ``tt-event-sponsors-identify``."""
    configure_logging()
    args = _parse_args(argv)
    settings = get_settings()

    api_key = (
        settings.gemini_api_key or os.environ.get("GEMINI_API_KEY") or ""
    ).strip()
    if not api_key:
        print(  # noqa: T201
            "Error: GEMINI_API_KEY is not set. "
            "Add GEMINI_API_KEY=... to .env or your environment."
        )
        return 1

    model = args.model or settings.gemini_model
    summary = identify_missing_sponsors(
        csv_path=args.csv,
        api_key=api_key,
        model=model,
        event_ids=args.parsed_event_ids,
        limit=args.limit,
        min_confidence=args.min_confidence,
        num_frames=args.frames,
        dry_run=args.dry_run,
        save_frames=args.save_frames,
        sleep_seconds=args.sleep_seconds,
        show_progress=True,
    )

    mode_note = " (dry-run; CSV not modified)" if args.dry_run else ""
    wrote_note = f" -> wrote {summary.csv_path}" if summary.wrote_file else ""
    print(  # noqa: T201
        f"Finished {summary.candidates} event(s): "
        f"{summary.updated} updated, {summary.skipped} skipped, "
        f"{summary.errors} errors{mode_note}{wrote_note}."
    )
    return 0 if summary.errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
