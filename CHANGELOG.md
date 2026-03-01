# yt-fetcher Changelog

All notable changes to `yt-fetcher` are documented in this file.

This format is inspired by Keep a Changelog and semantic versioning.

## 0.1.0 - 2026-03-01

### Added
- Two-step `yt-fetcher` pipeline:
  - YouTube source acquisition (`download_tracks.py`)
  - standardized output transcoding (`transcode_sources.py`)
- Source selection with quality/relevance filters (`official-preferred`, `strict-official`).
- Synchronized acquisition mode (`--skip-existing`) with robust resume:
  - index-based detection
  - direct on-disk detection
  - incremental index and metadata updates.
- Source metadata exports:
  - `sources/metadata.json`
  - `sources/metadata.csv`
- Daily run log:
  - `sources/run_log_YYYY-MM-DD.csv`
- Acquisition audit mode without writes (`--dry-run`) + faster variants (`--dry-run-no-auth`, `--start-at`, `--limit-tracks`).
- Explicit source format sorting (codec/resolution/bitrate priority).
- Selective transcoding:
  - `--generate both|audio|video`
- No-reencode mode:
  - `--no-transcode` (video copy + audio stream copy extraction).
