# yt-fetcher Release v0.1.0

## Summary
First testing release of `yt-fetcher`:
- best-effort YouTube source acquisition,
- standardized audio/video transcoding,
- synchronized runs,
- audit mode and exported metadata.

## Main features
- `download_tracks.py`
  - source selection with quality filters
  - `--official-preferred` and `--strict-official`
  - audit mode `--dry-run`
  - resume/sync mode `--skip-existing`
- `transcode_sources.py`
  - `--generate both|audio|video`
  - `--no-transcode` (copy/demux)
  - outputs in `output/videos` and `output/audios`

## Quality and robustness
- Explicit YouTube format sorting (codec/resolution/bitrate)
- Incremental index and metadata updates
- Daily run log: `sources/run_log_YYYY-MM-DD.csv`
- Improved handling for invalid JSON index and missing sources

## Compatibility
- macOS
- Linux (Ubuntu/Debian)
- Windows (PowerShell/CMD)

## Migration / breaking changes
- Audio outputs are now stored in `output/audios`.
- Output names are standardized without YouTube ID (`Artist - Title`).

## Reference commands
```bash
python download_tracks.py --input tracks.txt --sources-dir sources --skip-existing --cookies-from-browser chrome --official-preferred
python transcode_sources.py --sources-dir sources --output-dir output --generate both --audio-format wav --video-format mp4 --skip-existing
```

## Known limitations
- YouTube streams remain mostly lossy sources.
- For anti-bot challenges, browser cookies or Netscape cookie export may be required.

## Release checklist
- [ ] Git tag created: `v0.1.0`
- [ ] Changelog reviewed
- [ ] README reviewed
- [ ] Smoke checks executed (`py_compile` + basic runs)
