#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from yt_dlp import YoutubeDL


SEARCH_RESULT_COUNT = 5
INDEX_FILE_NAME = "index.json"
METADATA_JSON_NAME = "metadata.json"
METADATA_CSV_NAME = "metadata.csv"
RUN_LOG_DAILY_TEMPLATE = "run_log_{date}.csv"

FORMAT_SORT_PREFERENCES = [
    "vcodec:av01,vp9,avc1",
    "res",
    "vbr",
    "acodec:opus,mp4a,aac",
    "abr",
]

EXCLUDED_TERMS_DEFAULT = [
    "live",
    "concert",
    "remix",
    "sped up",
    "slowed",
    "karaoke",
    "instrumental",
]

OFFICIAL_MARKERS = [
    "official",
    "official music video",
    "official video",
    "official clip",
    "official lyric video",
    "lyric video",
    "music video",
    "vevo",
]

LYRIC_EXCLUDED_TERMS = {
    "lyrics",
    "lyric video",
}


@dataclass
class CandidateEvaluation:
    info: dict[str, Any]
    score: float
    best_height: int
    best_audio_abr: float
    reasons: list[str]


@dataclass
class Track:
    artist: str
    title: str

    @property
    def key(self) -> str:
        return f"{self.artist.strip().lower()}::{self.title.strip().lower()}"

    @property
    def query(self) -> str:
        return f"{self.artist} - {self.title}"

    @property
    def safe_name(self) -> str:
        raw = f"{self.artist} - {self.title}"
        safe = re.sub(r'[\\/:*?"<>|]+', "_", raw)
        safe = re.sub(r"\s+", " ", safe).strip().strip(".")
        return safe or "track"


def parse_track_line(line: str) -> Track | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None

    if " - " in stripped:
        artist, title = stripped.split(" - ", 1)
    else:
        raise ValueError(f"Invalid format (expected: artist - title with spaces around hyphen): {line.rstrip()}")

    artist = artist.strip()
    title = title.strip()
    if not artist or not title:
        raise ValueError(f"Invalid format (empty artist or title): {line.rstrip()}")

    return Track(artist=artist, title=title)


def iter_tracks(input_file: Path) -> Iterable[Track]:
    with input_file.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            try:
                track = parse_track_line(line)
                if track is not None:
                    yield track
            except ValueError as error:
                raise ValueError(f"Line {line_number}: {error}") from error


def build_auth_options(
    cookies_from_browser: str | None,
    cookies_file: Path | None,
) -> dict[str, Any]:
    auth_options: dict[str, Any] = {}
    if cookies_from_browser:
        auth_options["cookiesfrombrowser"] = (cookies_from_browser,)
    if cookies_file:
        auth_options["cookiefile"] = str(cookies_file)
    node_path = shutil.which("node")
    if node_path:
        auth_options["js_runtimes"] = {"node": {"path": node_path}}
    auth_options["remote_components"] = ["ejs:github"]
    return auth_options


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower()).strip()


def normalize_loose(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return normalize_text(without_accents)


def parse_fps(value: str | None) -> float:
    if not value or value in {"0/0", "N/A"}:
        return 0.0
    if "/" not in value:
        try:
            return float(value)
        except ValueError:
            return 0.0
    left, right = value.split("/", 1)
    try:
        denominator = float(right)
        if denominator == 0:
            return 0.0
        return float(left) / denominator
    except ValueError:
        return 0.0


def find_search_entries(query: str, auth_options: dict[str, Any], max_results: int) -> list[dict[str, Any]]:
    options = {
        "quiet": True,
        "noprogress": True,
        "default_search": f"ytsearch{max_results}",
        "skip_download": True,
        "extract_flat": False,
    }
    options.update(auth_options)

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)

    entries = info.get("entries") if isinstance(info, dict) else None
    if not entries:
        raise RuntimeError(f"No YouTube results found for: {query}")

    return [entry for entry in entries if isinstance(entry, dict)]


def fetch_video_info(url: str, auth_options: dict[str, Any]) -> dict[str, Any]:
    options = {
        "quiet": True,
        "noprogress": True,
        "skip_download": True,
        "noplaylist": True,
    }
    options.update(auth_options)

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    if not isinstance(info, dict):
        raise RuntimeError("Invalid yt-dlp response")
    return info


def evaluate_candidate(track: Track, info: dict[str, Any], excluded_terms: list[str]) -> CandidateEvaluation | None:
    title = str(info.get("title") or "")
    channel = str(info.get("channel") or info.get("uploader") or "")
    live_status = str(info.get("live_status") or "").lower()
    was_live = bool(info.get("was_live"))
    combined = normalize_text(f"{title} {channel}")

    artist_tokens = {token for token in re.split(r"[^a-z0-9]+", normalize_text(track.artist)) if len(token) >= 3}
    channel_norm = normalize_text(channel)
    title_norm = normalize_text(title)
    marker_match = any(marker in title_norm or marker in channel_norm for marker in OFFICIAL_MARKERS)
    channel_verified = bool(info.get("channel_is_verified"))
    artist_match = bool(artist_tokens) and any(token in channel_norm for token in artist_tokens)
    trusted_official = artist_match and (marker_match or channel_verified)

    for term in excluded_terms:
        term_normalized = normalize_text(term)
        if term_normalized in combined:
            if term_normalized in LYRIC_EXCLUDED_TERMS and trusted_official:
                continue
            return None

    if live_status in {"is_live", "was_live", "post_live"} or was_live:
        return None

    formats = info.get("formats") or []
    if not isinstance(formats, list):
        formats = []

    if formats:
        has_video = any(str(fmt.get("vcodec") or "none") != "none" for fmt in formats if isinstance(fmt, dict))
        has_audio = any(str(fmt.get("acodec") or "none") != "none" for fmt in formats if isinstance(fmt, dict))
        if not has_video or not has_audio:
            return None

        best_height = max(
            int(fmt.get("height") or 0)
            for fmt in formats
            if isinstance(fmt, dict) and str(fmt.get("vcodec") or "none") != "none"
        )
        best_audio_abr = max(
            float(fmt.get("abr") or 0.0)
            for fmt in formats
            if isinstance(fmt, dict) and str(fmt.get("acodec") or "none") != "none"
        )
    else:
        best_height = int(info.get("height") or 0)
        best_audio_abr = float(info.get("abr") or 0.0)

    reasons: list[str] = []
    score = 0.0

    if artist_tokens and any(token in channel_norm for token in artist_tokens):
        score += 45
        reasons.append("channel_match_artist")

    if "official" in channel_norm or "official" in title_norm:
        score += 20
        reasons.append("official_marker")

    if "vevo" in channel_norm:
        score += 20
        reasons.append("vevo_channel")

    if "topic" in channel_norm:
        score -= 25
        reasons.append("topic_channel_penalty")

    if any(kw in title_norm for kw in ["official music video", "official video", "official clip", "music video"]):
        score += 30
        reasons.append("video_marker")

    duration = int(info.get("duration") or 0)
    if 120 <= duration <= 480:
        score += 10
        reasons.append("duration_ok")
    elif duration > 0:
        score -= 5
        reasons.append("duration_penalty")

    score += min(best_height, 2160) / 24.0
    score += min(best_audio_abr, 320.0) / 8.0
    reasons.append("quality_score")

    return CandidateEvaluation(
        info=info,
        score=score,
        best_height=best_height,
        best_audio_abr=best_audio_abr,
        reasons=reasons,
    )


def is_official_candidate(track: Track, evaluation: CandidateEvaluation) -> bool:
    info = evaluation.info
    title = normalize_text(str(info.get("title") or ""))
    channel = normalize_text(str(info.get("channel") or info.get("uploader") or ""))

    artist_tokens = {token for token in re.split(r"[^a-z0-9]+", normalize_text(track.artist)) if len(token) >= 3}
    artist_match = bool(artist_tokens) and any(token in channel for token in artist_tokens)
    marker_match = any(marker in title or marker in channel for marker in OFFICIAL_MARKERS)
    return artist_match and marker_match


def get_rejection_reason(info: dict[str, Any], excluded_terms: list[str]) -> str | None:
    title = str(info.get("title") or "")
    channel = str(info.get("channel") or info.get("uploader") or "")
    combined = normalize_text(f"{title} {channel}")

    for term in excluded_terms:
        term_normalized = normalize_text(term)
        if term_normalized and term_normalized in combined:
            return f"excluded_term:{term_normalized}"

    live_status = str(info.get("live_status") or "").lower()
    was_live = bool(info.get("was_live"))
    if live_status in {"is_live", "was_live", "post_live"} or was_live:
        return "live_content"

    formats = info.get("formats") or []
    if isinstance(formats, list) and formats:
        has_video = any(str(fmt.get("vcodec") or "none") != "none" for fmt in formats if isinstance(fmt, dict))
        has_audio = any(str(fmt.get("acodec") or "none") != "none" for fmt in formats if isinstance(fmt, dict))
        if not has_video or not has_audio:
            return "no_av_formats"

    return None


def format_rejection_counts(rejection_counts: Counter[str]) -> str:
    if not rejection_counts:
        return "unknown"
    parts = [f"{reason}={count}" for reason, count in rejection_counts.most_common(5)]
    return ", ".join(parts)


def select_best_source(
    track: Track,
    auth_options: dict[str, Any],
    excluded_terms: list[str],
    max_results: int,
    strict_official: bool,
    official_preferred: bool,
    fast_mode: bool,
) -> CandidateEvaluation:
    entries = find_search_entries(track.query, auth_options, max_results=max_results)

    evaluated: list[CandidateEvaluation] = []
    rejection_counts: Counter[str] = Counter()
    for entry in entries:
        url_value = entry.get("webpage_url") or entry.get("url")
        if not url_value:
            continue
        url = str(url_value)
        if not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={url}"

        if fast_mode:
            info = dict(entry)
            info["webpage_url"] = url
        else:
            try:
                info = fetch_video_info(url, auth_options)
            except Exception:
                rejection_counts["fetch_error"] += 1
                continue

        evaluation = evaluate_candidate(track, info, excluded_terms)
        if evaluation is not None:
            if strict_official and not is_official_candidate(track, evaluation):
                rejection_counts["not_official_enough"] += 1
                continue
            evaluated.append(evaluation)
        else:
            reason = get_rejection_reason(info, excluded_terms) or "filtered_out"
            rejection_counts[reason] += 1

    if not evaluated:
        reasons = format_rejection_counts(rejection_counts)
        raise RuntimeError(f"No source matches the quality/origin criteria ({reasons})")

    evaluated.sort(key=lambda item: item.score, reverse=True)

    if strict_official:
        official_candidates = [item for item in evaluated if is_official_candidate(track, item)]
        if not official_candidates:
            raise RuntimeError("No official source matches the criteria")
        return official_candidates[0]

    if official_preferred:
        official_candidates = [item for item in evaluated if is_official_candidate(track, item)]
        if official_candidates:
            return official_candidates[0]

    return evaluated[0]


def resolve_downloaded_file(base_output_path: Path, downloaded_info: dict[str, Any] | None = None) -> Path:
    if isinstance(downloaded_info, dict):
        candidates_from_info: list[str] = []

        single_filename = downloaded_info.get("_filename")
        if isinstance(single_filename, str):
            candidates_from_info.append(single_filename)

        filepath = downloaded_info.get("filepath")
        if isinstance(filepath, str):
            candidates_from_info.append(filepath)

        requested = downloaded_info.get("requested_downloads")
        if isinstance(requested, list):
            for item in requested:
                if not isinstance(item, dict):
                    continue
                item_path = item.get("filepath")
                if isinstance(item_path, str):
                    candidates_from_info.append(item_path)

        for candidate in candidates_from_info:
            candidate_path = Path(candidate)
            if candidate_path.exists():
                return candidate_path

    mp4_path = base_output_path.with_suffix(".mp4")
    if mp4_path.exists():
        return mp4_path

    candidates = sorted(base_output_path.parent.glob(f"{base_output_path.name}.*"))
    if not candidates:
        raise RuntimeError(f"Download finished but file not found for {base_output_path.name}")
    return candidates[0]


def _download_video_with_format(
    url: str,
    output_path: Path,
    auth_options: dict[str, Any],
    fmt: str,
) -> tuple[Path, dict[str, Any]]:
    options = {
        "format": fmt,
        "format_sort": FORMAT_SORT_PREFERENCES,
        "merge_output_format": "mp4",
        "outtmpl": str(output_path.with_suffix(".%(ext)s")),
        "noplaylist": True,
        "quiet": False,
        "noprogress": False,
    }
    options.update(auth_options)

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)

    if not isinstance(info, dict):
        raise RuntimeError("Invalid yt-dlp response after download")

    return resolve_downloaded_file(output_path, downloaded_info=info), info


def download_video(url: str, output_path: Path, auth_options: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    format_fallbacks = [
        "bestvideo*+bestaudio/best",
        "bv*+ba/b",
        "best[ext=mp4]/best",
        "18/best",
    ]

    errors: list[str] = []
    for fmt in format_fallbacks:
        try:
            return _download_video_with_format(url, output_path, auth_options, fmt)
        except Exception as error:
            errors.append(f"{fmt}: {str(error).strip() or repr(error)}")

    raise RuntimeError(" ; ".join(errors))


def run_ffprobe(file_path: Path, ffprobe_binary: str) -> dict[str, Any]:
    command = [
        ffprobe_binary,
        "-v",
        "error",
        "-show_entries",
        "format=bit_rate,size,duration,format_name:stream=index,codec_type,codec_name,bit_rate,width,height,avg_frame_rate,sample_rate,channels",
        "-of",
        "json",
        str(file_path),
    ]

    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {file_path.name}: {process.stderr.strip()}")
    try:
        return json.loads(process.stdout or "{}")
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid ffprobe JSON output for {file_path.name}") from error


def summarize_probe(probe_data: dict[str, Any]) -> dict[str, Any]:
    streams = probe_data.get("streams") if isinstance(probe_data.get("streams"), list) else []
    format_data = probe_data.get("format") if isinstance(probe_data.get("format"), dict) else {}

    video_stream = next(
        (stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"),
        {},
    )
    audio_stream = next(
        (stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "audio"),
        {},
    )

    return {
        "container": str(format_data.get("format_name") or ""),
        "file_size": int(float(format_data.get("size") or 0) if format_data.get("size") else 0),
        "duration": float(format_data.get("duration") or 0.0),
        "overall_bitrate": int(float(format_data.get("bit_rate") or 0) if format_data.get("bit_rate") else 0),
        "video_codec": str(video_stream.get("codec_name") or ""),
        "video_bitrate": int(float(video_stream.get("bit_rate") or 0) if video_stream.get("bit_rate") else 0),
        "width": int(video_stream.get("width") or 0),
        "height": int(video_stream.get("height") or 0),
        "fps": round(parse_fps(str(video_stream.get("avg_frame_rate") or "0/0")), 3),
        "audio_codec": str(audio_stream.get("codec_name") or ""),
        "audio_bitrate": int(float(audio_stream.get("bit_rate") or 0) if audio_stream.get("bit_rate") else 0),
        "sample_rate": int(float(audio_stream.get("sample_rate") or 0) if audio_stream.get("sample_rate") else 0),
        "channels": int(audio_stream.get("channels") or 0),
    }


def load_index(index_file: Path) -> list[dict[str, Any]]:
    if not index_file.exists():
        return []

    try:
        with index_file.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return []

    records = raw.get("records") if isinstance(raw, dict) else None
    if not isinstance(records, list):
        return []

    valid_records: list[dict[str, Any]] = []
    for record in records:
        if isinstance(record, dict):
            valid_records.append(record)
    return valid_records


def save_index(index_file: Path, records: list[dict[str, Any]]) -> None:
    payload = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "records": records,
    }
    with index_file.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def record_file_exists(record: dict[str, Any], sources_dir: Path) -> bool:
    relative = str(record.get("source_file") or "").strip()
    if not relative:
        return False
    return (sources_dir / relative).exists()


def build_lookups(records: list[dict[str, Any]], sources_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_track: dict[str, dict[str, Any]] = {}
    by_video_id: dict[str, dict[str, Any]] = {}

    for record in records:
        track_key = str(record.get("track_key") or "")
        video_id = str(record.get("youtube_id") or "")
        if track_key and record_file_exists(record, sources_dir):
            by_track[track_key] = record
        if video_id and record_file_exists(record, sources_dir):
            by_video_id[video_id] = record

    return by_track, by_video_id


def build_record(
    track: Track,
    selected: CandidateEvaluation,
    source_file: Path,
    source_file_relative: str,
    ffprobe_binary: str,
) -> dict[str, Any]:
    probe = summarize_probe(run_ffprobe(source_file, ffprobe_binary=ffprobe_binary))
    info = selected.info
    youtube_id = str(info.get("id") or "")

    return {
        "track_key": track.key,
        "artist": track.artist,
        "title": track.title,
        "query": track.query,
        "youtube_id": youtube_id,
        "url": str(info.get("webpage_url") or f"https://www.youtube.com/watch?v={youtube_id}"),
        "video_title": str(info.get("title") or ""),
        "channel": str(info.get("channel") or info.get("uploader") or ""),
        "duration": int(info.get("duration") or 0),
        "selection_score": round(selected.score, 3),
        "selection_reasons": selected.reasons,
        "best_height_detected": selected.best_height,
        "best_audio_abr_detected": round(selected.best_audio_abr, 3),
        "source_file": source_file_relative,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "probe": probe,
    }


def write_metadata_exports(records: list[dict[str, Any]], sources_dir: Path) -> None:
    metadata_json = sources_dir / METADATA_JSON_NAME
    metadata_csv = sources_dir / METADATA_CSV_NAME

    json_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(records),
        "records": records,
    }
    with metadata_json.open("w", encoding="utf-8") as handle:
        json.dump(json_payload, handle, ensure_ascii=False, indent=2)

    fieldnames = [
        "track_key",
        "artist",
        "title",
        "youtube_id",
        "url",
        "video_title",
        "channel",
        "duration",
        "source_file",
        "container",
        "file_size",
        "overall_bitrate",
        "video_codec",
        "video_bitrate",
        "width",
        "height",
        "fps",
        "audio_codec",
        "audio_bitrate",
        "sample_rate",
        "channels",
    ]

    with metadata_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            probe = record.get("probe") if isinstance(record.get("probe"), dict) else {}
            writer.writerow(
                {
                    "track_key": record.get("track_key", ""),
                    "artist": record.get("artist", ""),
                    "title": record.get("title", ""),
                    "youtube_id": record.get("youtube_id", ""),
                    "url": record.get("url", ""),
                    "video_title": record.get("video_title", ""),
                    "channel": record.get("channel", ""),
                    "duration": record.get("duration", 0),
                    "source_file": record.get("source_file", ""),
                    "container": probe.get("container", ""),
                    "file_size": probe.get("file_size", 0),
                    "overall_bitrate": probe.get("overall_bitrate", 0),
                    "video_codec": probe.get("video_codec", ""),
                    "video_bitrate": probe.get("video_bitrate", 0),
                    "width": probe.get("width", 0),
                    "height": probe.get("height", 0),
                    "fps": probe.get("fps", 0),
                    "audio_codec": probe.get("audio_codec", ""),
                    "audio_bitrate": probe.get("audio_bitrate", 0),
                    "sample_rate": probe.get("sample_rate", 0),
                    "channels": probe.get("channels", 0),
                }
            )


def append_run_log_row(sources_dir: Path, row: dict[str, Any]) -> None:
    day = datetime.now(timezone.utc).date().isoformat()
    run_log_file = sources_dir / RUN_LOG_DAILY_TEMPLATE.format(date=day)
    fieldnames = [
        "timestamp",
        "track_key",
        "query",
        "status",
        "detail",
        "youtube_id",
        "source_file",
    ]

    should_write_header = not run_log_file.exists()
    with run_log_file.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if should_write_header:
            writer.writeheader()
        writer.writerow(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "track_key": row.get("track_key", ""),
                "query": row.get("query", ""),
                "status": row.get("status", ""),
                "detail": row.get("detail", ""),
                "youtube_id": row.get("youtube_id", ""),
                "source_file": row.get("source_file", ""),
            }
        )


def parse_youtube_id_from_filename(file_path: Path) -> str:
    stem = file_path.stem
    match = re.search(r"\[([^\]]+)\]$", stem)
    if not match:
        return ""
    return match.group(1).strip()


def find_existing_source_file_for_track(track: Track, videos_dir: Path) -> Path | None:
    if not videos_dir.exists():
        return None

    target = normalize_loose(track.safe_name)
    for candidate in videos_dir.iterdir():
        if not candidate.is_file():
            continue
        stem = candidate.stem
        name_part = stem.rsplit(" [", 1)[0] if " [" in stem else stem
        if normalize_loose(name_part) == target:
            return candidate
    return None


def build_record_from_existing_file(
    track: Track,
    source_file: Path,
    sources_dir: Path,
    ffprobe_binary: str,
) -> dict[str, Any]:
    youtube_id = parse_youtube_id_from_filename(source_file)
    source_relative = source_file.relative_to(sources_dir).as_posix()
    probe = summarize_probe(run_ffprobe(source_file, ffprobe_binary=ffprobe_binary))
    url = f"https://www.youtube.com/watch?v={youtube_id}" if youtube_id else ""

    return {
        "track_key": track.key,
        "artist": track.artist,
        "title": track.title,
        "query": track.query,
        "youtube_id": youtube_id,
        "url": url,
        "video_title": source_file.stem,
        "channel": "",
        "duration": int(round(float(probe.get("duration") or 0.0))),
        "selection_score": 0.0,
        "selection_reasons": ["recovered_from_disk"],
        "best_height_detected": int(probe.get("height") or 0),
        "best_audio_abr_detected": float(probe.get("audio_bitrate") or 0),
        "source_file": source_relative,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "probe": probe,
    }


def persist_records(records_store: dict[str, dict[str, Any]], sources_dir: Path, index_file: Path) -> None:
    final_records = [records_store[key] for key in sorted(records_store.keys())]
    save_index(index_file, final_records)
    write_metadata_exports(final_records, sources_dir)


def process_tracks(
    input_file: Path,
    sources_dir: Path,
    ffprobe_binary: str,
    skip_existing: bool,
    cookies_from_browser: str | None,
    cookies_file: Path | None,
    excluded_terms: list[str],
    max_search_results: int,
    strict_official: bool,
    official_preferred: bool,
    dry_run: bool,
    dry_run_no_auth: bool,
    limit_tracks: int,
    start_at: int,
) -> tuple[int, int]:
    videos_dir = sources_dir / "videos"
    if not dry_run:
        videos_dir.mkdir(parents=True, exist_ok=True)
    index_file = sources_dir / INDEX_FILE_NAME

    tracks = list(iter_tracks(input_file))
    start_index = max(0, start_at - 1)
    if start_index > 0:
        tracks = tracks[start_index:]
    if limit_tracks > 0:
        tracks = tracks[:limit_tracks]
    if not tracks:
        print("No tracks to process.")
        return (0, 0)

    total = len(tracks)
    print(f"{total} track(s) detected.")
    auth_options = build_auth_options(cookies_from_browser, cookies_file)
    if dry_run and dry_run_no_auth:
        auth_options.pop("cookiesfrombrowser", None)
        auth_options.pop("cookiefile", None)
        print("Fast DRY-RUN: no cookie authentication mode.")
    success_count = 0
    failure_count = 0
    existing_records = load_index(index_file)
    records_by_track, records_by_video_id = build_lookups(existing_records, sources_dir)
    records_store: dict[str, dict[str, Any]] = {str(record.get("track_key") or ""): record for record in existing_records if isinstance(record, dict) and str(record.get("track_key") or "")}
    dry_run_rejections: list[tuple[str, str]] = []

    for index, track in enumerate(tracks, start=1):
        if skip_existing and track.key in records_by_track:
            print(f"[{index}/{total}] Skipped (source already synced): {track.query}")
            if not dry_run:
                existing = records_by_track.get(track.key, {})
                append_run_log_row(
                    sources_dir,
                    {
                        "track_key": track.key,
                        "query": track.query,
                        "status": "skipped_from_index",
                        "detail": "source already synced in index",
                        "youtube_id": str(existing.get("youtube_id") or ""),
                        "source_file": str(existing.get("source_file") or ""),
                    },
                )
            success_count += 1
            continue

        if skip_existing:
            existing_file = find_existing_source_file_for_track(track, videos_dir)
            if existing_file is not None:
                recovered = build_record_from_existing_file(
                    track=track,
                    source_file=existing_file,
                    sources_dir=sources_dir,
                    ffprobe_binary=ffprobe_binary,
                )
                records_store[track.key] = recovered
                records_by_track[track.key] = recovered
                recovered_id = str(recovered.get("youtube_id") or "")
                if recovered_id:
                    records_by_video_id[recovered_id] = recovered
                if not dry_run:
                    persist_records(records_store, sources_dir, index_file)
                    append_run_log_row(
                        sources_dir,
                        {
                            "track_key": track.key,
                            "query": track.query,
                            "status": "skipped_from_disk",
                            "detail": "source detected directly on disk",
                            "youtube_id": recovered_id,
                            "source_file": str(recovered.get("source_file") or ""),
                        },
                    )
                print(f"[{index}/{total}] Skipped (source found on disk): {track.query}")
                success_count += 1
                continue

            print(f"[{index}/{total}] Searching: {track.query}")
        try:
            effective_max_results = max_search_results
            if dry_run:
                effective_max_results = min(max_search_results, 3)

            selected = select_best_source(
                track,
                auth_options=auth_options,
                excluded_terms=excluded_terms,
                max_results=effective_max_results,
                strict_official=strict_official,
                official_preferred=official_preferred,
                fast_mode=dry_run,
            )

            info = selected.info
            youtube_id = str(info.get("id") or "")
            if not youtube_id:
                raise RuntimeError("Selected source has no video identifier")

            if skip_existing and youtube_id in records_by_video_id:
                reference = records_by_video_id[youtube_id]
                cloned = dict(reference)
                cloned["track_key"] = track.key
                cloned["artist"] = track.artist
                cloned["title"] = track.title
                cloned["query"] = track.query
                records_store[track.key] = cloned
                records_by_track[track.key] = cloned
                if not dry_run:
                    persist_records(records_store, sources_dir, index_file)
                    append_run_log_row(
                        sources_dir,
                        {
                            "track_key": track.key,
                            "query": track.query,
                            "status": "reused_by_video_id",
                            "detail": "youtube_id already present in sources",
                            "youtube_id": str(cloned.get("youtube_id") or ""),
                            "source_file": str(cloned.get("source_file") or ""),
                        },
                    )
                print(f"[{index}/{total}] Reused (same YouTube source already present): {track.query}")
                success_count += 1
                continue

            source_stem = track.safe_name
            video_target = videos_dir / source_stem
            source_url = str(info.get("webpage_url") or f"https://www.youtube.com/watch?v={youtube_id}")

            channel = str(info.get("channel") or info.get("uploader") or "")
            title = str(info.get("title") or "")
            print(
                f"[{index}/{total}] Selected source: {channel} | {title} | score={round(selected.score, 2)}",
            )

            if dry_run:
                print(f"[{index}/{total}] DRY-RUN: no download for {track.query}")
                success_count += 1
                continue

            print(f"[{index}/{total}] Downloading source: {source_url}")
            source_file, _ = download_video(source_url, video_target, auth_options=auth_options)

            source_relative = source_file.relative_to(sources_dir).as_posix()
            record = build_record(
                track=track,
                selected=selected,
                source_file=source_file,
                source_file_relative=source_relative,
                ffprobe_binary=ffprobe_binary,
            )

            records_store[track.key] = record
            records_by_track[track.key] = record
            if youtube_id:
                records_by_video_id[youtube_id] = record
            if not dry_run:
                persist_records(records_store, sources_dir, index_file)
                append_run_log_row(
                    sources_dir,
                    {
                        "track_key": track.key,
                        "query": track.query,
                        "status": "downloaded",
                        "detail": "source downloaded",
                        "youtube_id": youtube_id,
                        "source_file": source_relative,
                    },
                )

            print(f"[{index}/{total}] Source OK: {source_file.name}")
            success_count += 1
        except Exception as error:
            details = str(error).strip() or repr(error)
            print(f"[{index}/{total}] ERROR on '{track.query}': {details}", file=sys.stderr)
            if not dry_run:
                append_run_log_row(
                    sources_dir,
                    {
                        "track_key": track.key,
                        "query": track.query,
                        "status": "error",
                        "detail": details,
                        "youtube_id": "",
                        "source_file": "",
                    },
                )
            if dry_run:
                dry_run_rejections.append((track.query, details))
            failure_count += 1

    if dry_run:
        print("DRY-RUN: no files written (index/metadata unchanged).")
        if dry_run_rejections:
            grouped = Counter(reason for _, reason in dry_run_rejections)
            print("Dry-run rejection summary:")
            for reason, count in grouped.most_common():
                print(f"  - {reason}: {count}")
    else:
        persist_records(records_store, sources_dir, index_file)

    print(f"Completed. Success: {success_count} | Failures: {failure_count}")
    return (success_count, failure_count)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="yt-fetcher: download high-quality YouTube source videos into sources/, then export metadata (JSON/CSV).",
    )
    parser.add_argument(
        "--input",
        default="tracks.txt",
        type=Path,
        help="Input text file (one line per track: artist - title)",
    )
    parser.add_argument(
        "--sources-dir",
        default="sources",
        type=Path,
        help="Directory used to store YouTube sources",
    )
    parser.add_argument(
        "--ffprobe",
        default="ffprobe",
        help="Path to ffprobe binary",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Synced mode: skip tracks already present in source index",
    )
    parser.add_argument(
        "--cookies-from-browser",
        default=None,
        help="Browser name used to load cookies (e.g. chrome, firefox, safari)",
    )
    parser.add_argument(
        "--cookies-file",
        type=Path,
        default=None,
        help="Exported Netscape cookies file",
    )
    parser.add_argument(
        "--max-search-results",
        type=int,
        default=SEARCH_RESULT_COUNT,
        help="Number of YouTube search results evaluated per track",
    )
    parser.add_argument(
        "--exclude-term",
        action="append",
        default=None,
        help="Excluded term (repeatable option). If omitted, the default list is used.",
    )
    parser.add_argument(
        "--strict-official",
        action="store_true",
        help="Only accept candidates with official markers + artist/channel match.",
    )
    parser.add_argument(
        "--official-preferred",
        action="store_true",
        help="Prefer official candidates, but fall back to best candidate if none are official.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate and select sources without downloading or modifying index.",
    )
    parser.add_argument(
        "--dry-run-no-auth",
        action="store_true",
        help="In dry-run mode, do not use cookies (faster, may reduce accuracy with YouTube blocks).",
    )
    parser.add_argument(
        "--limit-tracks",
        type=int,
        default=0,
        help="Limit processing to first N lines (useful for quick tests). 0 = all tracks.",
    )
    parser.add_argument(
        "--start-at",
        type=int,
        default=1,
        help="Start at line N (1-based index) in tracks.txt. Default: 1.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.input.exists():
        print(f"File not found: {args.input}", file=sys.stderr)
        return 1

    try:
        excluded_terms = args.exclude_term if args.exclude_term else EXCLUDED_TERMS_DEFAULT
        if args.strict_official and args.official_preferred:
            print("--official-preferred is implicit with --strict-official (--strict-official takes priority).", file=sys.stderr)
        _, failure_count = process_tracks(
            input_file=args.input,
            sources_dir=args.sources_dir,
            ffprobe_binary=args.ffprobe,
            skip_existing=args.skip_existing,
            cookies_from_browser=args.cookies_from_browser,
            cookies_file=args.cookies_file,
            excluded_terms=excluded_terms,
            max_search_results=max(1, int(args.max_search_results)),
            strict_official=args.strict_official,
            official_preferred=args.official_preferred,
            dry_run=args.dry_run,
            dry_run_no_auth=args.dry_run_no_auth,
            limit_tracks=max(0, int(args.limit_tracks)),
            start_at=max(1, int(args.start_at)),
        )
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2

    if args.dry_run:
        if failure_count > 0:
            print(
                "Dry-run completed: some tracks do not meet current criteria, "
                "but exit code remains 0 (audit mode).",
                file=sys.stderr,
            )
        return 0

    if failure_count > 0:
        print(
            "Some tracks failed (often due to YouTube anti-bot checks). "
            "Try --cookies-file with a recent Netscape export.",
            file=sys.stderr,
        )
        return 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
