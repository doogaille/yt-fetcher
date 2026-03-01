#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

INDEX_FILE_NAME = "index.json"


def load_records(sources_dir: Path) -> list[dict[str, Any]]:
    index_file = sources_dir / INDEX_FILE_NAME
    if not index_file.exists():
        raise FileNotFoundError(f"Index not found: {index_file}")

    try:
        with index_file.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid index JSON: {index_file}") from error

    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return []

    return [record for record in records if isinstance(record, dict)]


def safe_stem(record: dict[str, Any]) -> str:
    artist = str(record.get("artist") or "").strip()
    title = str(record.get("title") or "").strip()

    base = f"{artist} - {title}".strip(" -")

    sanitized = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in base)
    sanitized = " ".join(sanitized.split()).strip().strip(".")
    return sanitized or "track"


def normalize_loose(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_accents.lower().split()).strip()


def strip_trailing_id(stem: str) -> str:
    if stem.endswith("]") and " [" in stem:
        return stem.rsplit(" [", 1)[0]
    return stem


def resolve_source_file(record: dict[str, Any], sources_dir: Path) -> Path:
    source_relative = str(record.get("source_file") or "")
    if source_relative:
        direct = sources_dir / source_relative
        if direct.exists():
            return direct

    videos_dir = sources_dir / "videos"
    if not videos_dir.exists():
        return sources_dir / source_relative

    target = normalize_loose(strip_trailing_id(safe_stem(record)))
    for candidate in videos_dir.iterdir():
        if not candidate.is_file():
            continue
        candidate_name = normalize_loose(strip_trailing_id(candidate.stem))
        if candidate_name == target:
            return candidate

    return sources_dir / source_relative


def run_ffmpeg(command: list[str]) -> None:
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        raise RuntimeError(process.stderr.strip() or "ffmpeg failed")


def build_video_command(
    ffmpeg_binary: str,
    source_file: Path,
    target_file: Path,
    video_codec: str,
    video_crf: int,
    video_preset: str,
    video_audio_codec: str,
    video_audio_bitrate: str,
) -> list[str]:
    return [
        ffmpeg_binary,
        "-y",
        "-i",
        str(source_file),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        video_codec,
        "-preset",
        video_preset,
        "-crf",
        str(video_crf),
        "-c:a",
        video_audio_codec,
        "-b:a",
        video_audio_bitrate,
        str(target_file),
    ]


def build_audio_command(
    ffmpeg_binary: str,
    source_file: Path,
    target_file: Path,
    audio_format: str,
    audio_codec: str,
    audio_bitrate: str,
    sample_rate: int,
    channels: int,
) -> list[str]:
    command = [
        ffmpeg_binary,
        "-y",
        "-i",
        str(source_file),
        "-vn",
        "-ac",
        str(channels),
        "-ar",
        str(sample_rate),
    ]

    if audio_format == "wav":
        command.extend(["-c:a", "pcm_s16le"])
    elif audio_format == "flac":
        command.extend(["-c:a", "flac"])
    elif audio_format == "m4a":
        effective_codec = audio_codec if audio_codec in {"aac", "libfdk_aac"} else "aac"
        command.extend(["-c:a", effective_codec, "-b:a", audio_bitrate])
    else:
        command.extend(["-c:a", audio_codec, "-b:a", audio_bitrate])

    command.append(str(target_file))
    return command


def build_audio_copy_command(
    ffmpeg_binary: str,
    source_file: Path,
    target_file: Path,
) -> list[str]:
    return [
        ffmpeg_binary,
        "-y",
        "-i",
        str(source_file),
        "-vn",
        "-c:a",
        "copy",
        str(target_file),
    ]


def infer_audio_copy_extension(record: dict[str, Any]) -> str:
    probe = record.get("probe") if isinstance(record.get("probe"), dict) else {}
    audio_codec = str(probe.get("audio_codec") or "").lower()

    if audio_codec == "opus":
        return "opus"
    if audio_codec in {"aac", "mp4a"}:
        return "m4a"
    if audio_codec == "flac":
        return "flac"
    if audio_codec == "mp3":
        return "mp3"
    if audio_codec == "vorbis":
        return "ogg"
    return "mka"


def transcode(
    sources_dir: Path,
    output_dir: Path,
    ffmpeg_binary: str,
    video_format: str,
    audio_format: str,
    video_codec: str,
    video_crf: int,
    video_preset: str,
    video_audio_codec: str,
    video_audio_bitrate: str,
    audio_codec: str,
    audio_bitrate: str,
    sample_rate: int,
    channels: int,
    skip_existing: bool,
    no_transcode: bool,
    generate: str,
) -> tuple[int, int, int]:
    records = load_records(sources_dir)
    if not records:
        print("No sources found in index.")
        return (0, 0, 0)

    videos_dir = output_dir / "videos"
    audio_dir = output_dir / "audios"
    videos_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    seen_sources: set[Path] = set()
    success = 0
    skipped = 0
    failed = 0
    want_video = generate in {"both", "video"}
    want_audio = generate in {"both", "audio"}

    for index, record in enumerate(records, start=1):
        source_file = resolve_source_file(record, sources_dir)
        source_relative = str(record.get("source_file") or "")
        if not source_relative and not source_file.exists():
            continue
        if source_file in seen_sources:
            continue
        seen_sources.add(source_file)

        if not source_file.exists() or not source_file.is_file():
            print(f"[{index}] Missing source: {source_file}", file=sys.stderr)
            failed += 1
            continue

        stem = safe_stem(record)
        if no_transcode:
            source_ext = source_file.suffix.lstrip(".") or "mp4"
            audio_copy_ext = infer_audio_copy_extension(record)
            video_target = videos_dir / f"{stem}.{source_ext}"
            audio_target = audio_dir / f"{stem}.{audio_copy_ext}"
        else:
            video_target = videos_dir / f"{stem}.{video_format}"
            audio_target = audio_dir / f"{stem}.{audio_format}"

        should_skip = False
        if skip_existing:
            if want_video and want_audio:
                should_skip = video_target.exists() and audio_target.exists()
            elif want_video:
                should_skip = video_target.exists()
            elif want_audio:
                should_skip = audio_target.exists()

        if should_skip:
            print(f"[{index}] Skipped (already generated): {stem}")
            skipped += 1
            continue

        try:
            if want_video and not (skip_existing and video_target.exists()):
                if no_transcode:
                    print(f"[{index}] Copying video without transcode: {video_target.name}")
                    shutil.copy2(source_file, video_target)
                else:
                    print(f"[{index}] Transcoding video: {video_target.name}")
                    video_command = build_video_command(
                        ffmpeg_binary=ffmpeg_binary,
                        source_file=source_file,
                        target_file=video_target,
                        video_codec=video_codec,
                        video_crf=video_crf,
                        video_preset=video_preset,
                        video_audio_codec=video_audio_codec,
                        video_audio_bitrate=video_audio_bitrate,
                    )
                    run_ffmpeg(video_command)

            if want_audio and not (skip_existing and audio_target.exists()):
                if no_transcode:
                    print(f"[{index}] Extracting audio without transcode: {audio_target.name}")
                    audio_command = build_audio_copy_command(
                        ffmpeg_binary=ffmpeg_binary,
                        source_file=source_file,
                        target_file=audio_target,
                    )
                else:
                    print(f"[{index}] Transcoding audio: {audio_target.name}")
                    audio_command = build_audio_command(
                        ffmpeg_binary=ffmpeg_binary,
                        source_file=source_file,
                        target_file=audio_target,
                        audio_format=audio_format,
                        audio_codec=audio_codec,
                        audio_bitrate=audio_bitrate,
                        sample_rate=sample_rate,
                        channels=channels,
                    )
                run_ffmpeg(audio_command)

            success += 1
            print(f"[{index}] OK: {stem}")
        except Exception as error:
            print(f"[{index}] ERROR: {stem}: {str(error).strip() or repr(error)}", file=sys.stderr)
            failed += 1

        print(f"Completed. Success: {success} | Skipped: {skipped} | Failures: {failed}")
    return (success, skipped, failed)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="yt-fetcher: transcode local sources (sources/) into target formats in output/ in synchronized mode.",
    )
    parser.add_argument("--sources-dir", type=Path, default="sources", help="Sources directory")
    parser.add_argument("--output-dir", type=Path, default="output", help="Output directory")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="Path to ffmpeg")
    parser.add_argument(
        "--generate",
        choices=["both", "audio", "video"],
        default="both",
        help="Choose what to generate: audio, video, or both (default).",
    )

    parser.add_argument("--video-format", default="mp4", help="Target video format")
    parser.add_argument("--audio-format", default="wav", choices=["wav", "mp3", "flac", "m4a"], help="Target audio format")

    parser.add_argument("--video-codec", default="libx264", help="Target video codec")
    parser.add_argument("--video-crf", type=int, default=18, help="Video CRF (lower = better quality)")
    parser.add_argument("--video-preset", default="medium", help="Video encoder preset")
    parser.add_argument("--video-audio-codec", default="aac", help="Audio codec inside output video file")
    parser.add_argument("--video-audio-bitrate", default="320k", help="Audio bitrate inside output video file")

    parser.add_argument("--audio-codec", default="libmp3lame", help="Audio codec (used for lossy formats)")
    parser.add_argument("--audio-bitrate", default="320k", help="Target audio bitrate (lossy formats)")
    parser.add_argument("--sample-rate", type=int, default=44100, help="Audio sample rate")
    parser.add_argument("--channels", type=int, default=2, help="Audio channel count")

    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Synchronized mode: do not overwrite existing target files",
    )
    parser.add_argument(
        "--no-transcode",
        action="store_true",
        help="Do not re-encode: copy source video and extract audio via stream copy.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        _, _, failed = transcode(
            sources_dir=args.sources_dir,
            output_dir=args.output_dir,
            ffmpeg_binary=args.ffmpeg,
            video_format=args.video_format,
            audio_format=args.audio_format,
            video_codec=args.video_codec,
            video_crf=args.video_crf,
            video_preset=args.video_preset,
            video_audio_codec=args.video_audio_codec,
            video_audio_bitrate=args.video_audio_bitrate,
            audio_codec=args.audio_codec,
            audio_bitrate=args.audio_bitrate,
            sample_rate=args.sample_rate,
            channels=args.channels,
            skip_existing=args.skip_existing,
            no_transcode=args.no_transcode,
            generate=args.generate,
        )
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 1
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1

    if failed > 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
