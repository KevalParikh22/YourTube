# YourTube

A local-first assistant and knowledge base for your YouTube backlog and playlists.

Many people have a Watch Later playlist running into hundreds of videos with no way to see how many hours it actually takes to watch. YouTube provides no tools to organise, summarise, or judge which videos are worth your time. YourTube solves this on your own computer: your playlists, sign-in, notes, and data stay entirely private on your machine.

## What it does

- **Library**
  - Read playlists, including private playlists and Watch Later via browser sign-in or cookies.
  - Combine multiple playlists into one deduplicated library, or maintain separate libraries (e.g. Tech, Personal).
  - Calculate total watch time at 1x to 2x speed with daily finish-date planning.
  - Read-only: never modifies, reorders, or removes anything on YouTube.
- **Understand**
  - Fetch descriptions, chapters, and captions anonymously.
  - Transcribe videos locally with Whisper when captions are missing or inaccurate.
  - Read slides, whiteboards, and on-screen code using vision models.
  - Search across titles, transcripts, chapters, summaries, and notes by words or by meaning.
- **Decide**
  - AI Watch / Skim / Skip ratings with reasons, interest matching, and specific parts worth watching.
  - Detailed video summaries with TL;DR, timestamped minutes, takeaways, and check questions.
  - Knowledge synthesis pages built across summarised videos in each category.
  - Ask questions across your library with answers citing exact video timestamps.
- **Tools**
  - Playlist length calculator: compute watch time at 1x to 2x for up to 10 playlists or video ranges without saving them.
  - Compare playlists: find overlapping vs unique videos and combined watch time.
- **Your data**
  - Back up the library and restore any backup (Settings → Storage); clear caches and manage earlier exports.
  - Export library to CSV or JSON, and export Markdown notes formatted for Obsidian.
  - Interactive API reference (`/docs/api`) backed by OpenAPI 3.1.

## Privacy and AI

- Local first: runs on your machine; your library, sign-in cookies and database stay on your computer. If you choose a cloud AI for a task, only the text that task needs (for example a transcript to rate) is sent to that provider.
- Local AI powered by Ollama (text and vision) and Whisper (speech-to-text), with GPU acceleration on NVIDIA, AMD Radeon, and Apple Silicon.
- Optional cloud AI providers: Claude (Anthropic), Gemini (Google), OpenAI, Groq, or OpenAI-compatible endpoints.
- API keys stay in `settings.json` or environment variables; keys are never logged or sent to the browser.
- AI only suggests: ratings, category assignments, and knowledge pages are proposals you review and accept.

## Install

### Requirements

- Linux, macOS, or Windows 10/11
- Python 3.10 or newer
- ffmpeg (optional, for local transcription and slide reading)
- GPU optional: NVIDIA (CUDA), AMD Radeon, or Apple Silicon makes local AI fast; otherwise runs on CPU

### Native setup (recommended)

Linux and macOS:
```bash
bash scripts/setup.sh              # app only
bash scripts/setup.sh --local-ai   # app plus local AI (Ollama and a model sized for your GPU)
bash scripts/setup.sh --local-ai --service   # recommended: also keep dashboard running as a service
```

Windows (PowerShell):
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1            # app only
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -LocalAI   # app plus local AI
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 -LocalAI -Service
```

### Docker (optional)

```bash
mkdir data
docker compose up -d --build
docker compose exec yourtube python dashboard.py --print-url
```

For manual setup, GPU drivers, and detailed configuration, see [docs/SETUP.md](docs/SETUP.md).

## Quick start

1. **Start the dashboard**: Run `.venv/bin/python dashboard.py` (or `bash scripts/service.sh open`) and open the access link (`http://127.0.0.1:8765/?t=...`).
2. **Add a playlist**: On **Playlists**, paste a playlist URL, ID, or `WL` (Watch Later), and select **Detect my browser login**.
3. **Sync**: Click **Sync** (or run `.venv/bin/python main.py <ID> --browser auto`) to import videos into your library.
4. **Rate**: On **Library**, click **Rate unrated** to generate Watch / Skim / Skip recommendations matched to your interests.
5. **Ask**: Click the **Ask** button at bottom right to ask questions across your library with cited video moments.

## Project layout

```text
yourtube/          Application modules (ingest, storage, classifier, AI, search, etc.)
dashboard.py       Dashboard server entry point shim
main.py            CLI and sync pipeline entry point shim
local_setup.py     Local AI and GPU detection entry point shim
web/               Dashboard single-page interface (HTML, CSS, JS, API docs)
scripts/           Setup and background service scripts (Linux, macOS, Windows)
tests/             Unit test suite (unittest)
docs/              Setup guide, documentation index, architecture, OpenAPI, PRDs
spikes/            Experimental research scripts (ASR, downloads, embeddings)
```

## Development

Run the test suite:
```bash
.venv/bin/python -m unittest
```

Interactive API documentation is available at `http://127.0.0.1:8765/docs/api` while the dashboard is running.
See [docs/architecture.md](docs/architecture.md) for code structure and data flow.

## License and security

- Licensed under the [MIT License](LICENSE).
- Security policies and reporting: [SECURITY.md](SECURITY.md).
- Disclaimer: YourTube is an independent open-source project and is not affiliated with or endorsed by YouTube or Google.
