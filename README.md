# YouTube Fetcher

`yt-fetcher` is a two-step Python pipeline to:

1. fetch the **best possible YouTube source videos** (with relevance filters),
2. generate standardized outputs (audio/video), with or without transcoding.

It is designed to run on **macOS, Linux, and Windows**.

Current status: **v0.1.0 (testing phase)**.

---

## How it works

### Step 1 — Acquisition (`download_tracks.py`)

- Reads `tracks.txt` (strict format: `artist - title`, one track per line)
- Searches YouTube and filters unwanted content
- Prioritizes official sources (`--official-preferred` or `--strict-official`)
- Downloads source files into `sources/videos/`
- Continuously updates:
  - `sources/index.json`
  - `sources/metadata.json`
  - `sources/metadata.csv`
  - `sources/run_log_YYYY-MM-DD.csv`

### Step 2 — Transcoding (`transcode_sources.py`)

- Reads source entries from `sources/index.json`
- Generates output files in `output/`:
  - `output/videos/`
  - `output/audios/`
- Supports:
  - full transcoding
  - copy/demux without transcoding (`--no-transcode`)
  - selective generation (`--generate audio|video|both`)

---

## Requirements

- Python 3.10+
- `ffmpeg` (including `ffprobe`) in your PATH
- `node` in your PATH (used by `yt-dlp` for YouTube challenge handling)

### Install system dependencies

#### macOS (Homebrew)

```bash
brew install ffmpeg node
```

#### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y ffmpeg nodejs npm
```

#### Windows

Install `ffmpeg` and `node` via `winget`, Chocolatey, or official installers.

Verify:

```powershell
ffmpeg -version
node --version
```

---

## Project setup

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Windows CMD

```bat
py -m venv .venv
.venv\Scripts\activate.bat
pip install -r requirements.txt
```

---

## Input format (`tracks.txt`)

One line = one track:

```txt
artist - title
```

Example:

```txt
Flume & Kai - Never Be Like You
José González - Heartbeats
The Midnight - Los Angeles
```

Notes:
- empty lines and lines starting with `#` are ignored
- the separator must be strictly ` space-hyphen-space `

---

## Quickstart

### 1) Download/update source files

```bash
python download_tracks.py \
  --input tracks.txt \
  --sources-dir sources \
  --skip-existing \
  --cookies-from-browser chrome \
  --official-preferred
```

### 2) Generate standardized audio+video outputs

```bash
python transcode_sources.py \
  --sources-dir sources \
  --output-dir output \
  --generate both \
  --audio-format wav \
  --video-format mp4 \
  --skip-existing
```

On Windows, replace `python` with `py` if needed.

---

## Common workflows

### Quick audit without downloading

```bash
python download_tracks.py --input tracks.txt --dry-run --limit-tracks 5
```

### Faster audit without cookies

```bash
python download_tracks.py --input tracks.txt --dry-run --dry-run-no-auth --limit-tracks 5
```

### Start at line 11 and process 10 tracks

```bash
python download_tracks.py --input tracks.txt --start-at 11 --limit-tracks 10 --skip-existing
```

### Strict official-only mode (more selective)

```bash
python download_tracks.py --input tracks.txt --skip-existing --strict-official --cookies-from-browser chrome
```

### Audio only (faster)

```bash
python transcode_sources.py --sources-dir sources --output-dir output --generate audio --audio-format mp3 --skip-existing
```

### Video only

```bash
python transcode_sources.py --sources-dir sources --output-dir output --generate video --video-format mp4 --skip-existing
```

### No transcoding (copy/demux)

```bash
python transcode_sources.py --sources-dir sources --output-dir output --no-transcode --generate both --skip-existing
```

---

## Generated outputs

### `sources/` directory

- `videos/`: raw YouTube source files
- `index.json`: synchronized track/source state
- `metadata.json` and `metadata.csv`: technical metadata (codec, bitrate, resolution, etc.)
- `run_log_YYYY-MM-DD.csv`: incremental daily run log

### `output/` directory

- `videos/`
- `audios/`

Output names are standardized as `Artist - Title` (without YouTube ID).

---

## Main CLI options

### `download_tracks.py`

- `--input`: input file
- `--sources-dir`: sources directory
- `--skip-existing`: synchronized mode
- `--official-preferred`: prefer official sources with fallback
- `--strict-official`: official sources only
- `--dry-run`: audit without writing files
- `--dry-run-no-auth`: audit without cookies
- `--start-at`, `--limit-tracks`: slice processing
- `--cookies-from-browser` / `--cookies-file`: YouTube authentication

### `transcode_sources.py`

- `--sources-dir`, `--output-dir`
- `--generate both|audio|video`
- `--no-transcode`
- `--video-format`, `--audio-format`
- `--video-codec`, `--video-crf`, `--video-preset`
- `--audio-codec`, `--audio-bitrate`, `--sample-rate`, `--channels`
- `--skip-existing`

For `--audio-format m4a`, codec is automatically aligned to AAC when needed.

---

## Exit behavior

- `download_tracks.py`
  - normal mode: non-zero exit code if any track fails
  - `--dry-run` mode: always exits with `0` (audit mode)
- `transcode_sources.py`
  - exits with code `2` when transcoding failures occur

---

## Troubleshooting

### YouTube blocks extraction (“Sign in to confirm you’re not a bot”)

Use browser cookies or a Netscape cookie export:

```bash
python download_tracks.py --input tracks.txt --cookies-from-browser chrome
# or
python download_tracks.py --input tracks.txt --cookies-file /path/to/cookies.txt
```

### Already-downloaded tracks are searched again

Always run with `--skip-existing`.

### Interrupted run (Ctrl+C)

Rerun the same command with `--skip-existing`: index + source files allow resume.

### `transcode_sources.py` cannot find sources

Check that `sources/index.json` exists and files are present in `sources/videos/`.

---

## Audio/video quality notes

- YouTube streams are mostly lossy (commonly Opus/AAC)
- Converting to WAV/FLAC does not add loss, but cannot recreate true lossless source
- To minimize cumulative losses:
  - keep original source files
  - avoid repeated lossy re-encodes

---

## License / usage

Make sure your use of downloaded content complies with local laws and platform terms.