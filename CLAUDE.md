# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

YT KB (YouTube Knowledge Base), Phase 0: a local Python tool that reads a YouTube playlist (often a private "Watch Later"-style backlog), sorts its videos into topic categories, works out watch time at 1x through 2x speed, and tracks progress in a local dashboard. Specs: `docs/PRD-Phase-0.md` (original) and `docs/Phase-0-Research-and-Improvements.md` (research findings, revised requirements v0.2, roadmap). The second document supersedes the first where they disagree.

**Hard rule: the source playlist is never modified.** Non-destructiveness is a core requirement. Many playlists can be ingested; each is a *source* feeding one deduplicated library. A video that disappears from a source is flagged (`playlist_items.removed = 1`), never deleted, and **stays in the library (and in remaining-time totals) until it is marked watched**.

## Commands

```bash
python3 -m venv --without-pip .venv && python3 -m pip --python .venv/bin/python install -r requirements.txt
.venv/bin/python dashboard.py            # local dashboard at http://127.0.0.1:8765 (--port, --no-open)
.venv/bin/python main.py "<playlist URL | ID | WL>" [--browser auto|firefox|chrome | --cookies cookies.txt] [--ai] [--no-open]
.venv/bin/python main.py --list [--browser auto]   # list the signed-in account's playlists and IDs
.venv/bin/python main.py --details [N]             # fetch details/captions for up to N videos (default 200)
.venv/bin/python -m unittest             # test suite (tests/), runs against temp files
bash scripts/setup.sh [--local-ai]       # first-time setup for users (venv, deps, optional Ollama + model)
.venv/bin/python local_setup.py [--pull M | --use M | --recommended]   # local AI check and model setup
```

Dependencies: `yt-dlp`, `anthropic` (only for the Claude provider), `secretstorage` (Linux, to decrypt Chrome cookies). The system Python has no `ensurepip`, which is why the venv is created `--without-pip`. There is no linter or build step. To try things without touching the real data, set `YTKB_DB`, `YTKB_TAXONOMY` and `YTKB_SETTINGS` to other paths. `storage.py`, `classifier.py` and `llm.py` read them at import time, and `tests/__init__.py` sets them to a temp dir.

## Architecture

Every stage passes around a list of plain video dicts with the keys `video_id, title, channel, duration_seconds, url, position, category, status`. The pipeline in `main.py` `run_pipeline()` runs these stages:

1. **`extractor.py`**: yt-dlp flat extraction with `skip_download`. It uses no Google API quota and needs no OAuth. Private access comes from browser cookies or a cookies.txt file. The YouTube Data API cannot read Watch Later, which is why yt-dlp is used. `browser="auto"` (`detect_browser()`) picks the first browser with YouTube login cookies. On this Linux machine that is Chrome, because Firefox is not signed in. On Windows, Chromium cookies usually fail because of app-bound encryption, so Firefox or a cookies.txt file is recommended there.
2. **`classifier.py`**: keyword scoring over title + channel (single word scores 1, multi-word phrase scores 2). The taxonomy comes from `taxonomy.json`, written by the dashboard, or `DEFAULT_TAXONOMY` if that file is absent. A video with no match goes to `FALLBACK_CATEGORY` ("General / Other").
3. **`storage.py`**: SQLite. `init_db()` creates the tables and migrates older DBs in place. Tables:
   - `playlists`
   - `videos`: one row per video. Category, status, notes, and `category_locked` live here.
   - `playlist_items`: many-to-many membership with `position` and `removed`.
   - `category_suggestions`: pending AI suggestions (one per video), shown for review and never applied automatically.

   `videos.category_source` is `keywords` (unlocked), `manual` or `ai`. Accepting a suggestion locks the category with source `ai`. A manual category change deletes any pending suggestion.

   `save_playlist()` keeps status and notes, and keeps the category when `category_locked = 1`. It marks videos missing from the latest extraction as removed, and never does so from an empty extraction. After saving, `main.py` reloads from the DB so reports reflect manual edits.
4. **`metrics.py`**: counts and durations at 1.0/1.25/1.5/1.75/2.0x. The remaining time leaves out `completed` videos.
5. **`reporter.py`**: writes static snapshots to `reports/`. User content must be HTML-escaped.
**AI layer (optional)**: `llm.py` holds the provider registry and `settings.json` (git-ignored; API keys, presets, category notes, a pending proposal). Every provider reduces to `complete(system, prompt) -> text`. Claude goes through the `anthropic` SDK. Gemini uses REST. OpenAI-compatible endpoints cover open-weight hosts and local Ollama/LM Studio. `public_settings()` is the only form sent to the browser and never includes keys. `ai_classifier.py` has `discover_categories` (proposes a category set that keeps the user's presets verbatim) and `assign_categories` (batches of 50, indexed lines, unknown answers go to the fallback). `run_discover`/`run_suggest` are the job entry points. AI output is always a proposal or suggestion that the user accepts in the dashboard.

6. **`exporter.py`**: builds `watch_videos?video_ids=` queue URLs. The endpoint is undocumented and capped at 50 IDs, so use the chunked `generate_youtube_watch_queue_urls`. Also handles CSV and JSON export.

**Dashboard**: `dashboard.py` is a stdlib `ThreadingHTTPServer` bound to 127.0.0.1. It serves `web/index.html`, a single-file vanilla JS app with hash routing (`#/` library with status/category/playlist filters in the left rail, including an "AI suggestions" view, `#/plan`, `#/sources`, `#/categories` with presets ("Keep") and proposal review, `#/ai` provider settings). `/api/state` returns the whole library via `storage.get_library()` (every video with its `sources` and `in_source`); all filtering happens client-side, and a JSON API:
- `GET /api/state?playlist=`, `/api/job`, `/api/export`
- `POST /api/videos/update`, `/api/taxonomy`, `/api/categories/{rename,delete}`, `/api/reclassify`, `/api/ingest`, `/api/presets`
- `POST /api/ai/{settings,test,models,discover,suggest}`, `/api/ai/proposal/{apply,dismiss}`, `/api/ai/suggestions/{accept,dismiss}`

POSTs must send the `X-YTKB: 1` header and JSON content. The Host header must be localhost. Together these act as CSRF and DNS-rebinding protection; keep them. Syncs and AI runs share one background `Job` slot (`kind`: sync/discover/suggest), with stdout captured into the job log. When `auto_after_sync` is on, a sync also asks for suggestions on unsorted videos. The client computes metrics itself (mirroring `metrics.py`) from the video list in `/api/state`. Setting a category through the API locks it; `reset_category` unlocks it and re-classifies.

## Phase 1 (in progress)

Spec: `docs/PRD-Phase-1.md` (details queue, transcripts, search, AI summaries, Skipped status and triage). Per-video reads must be made **without cookies**: with cookies, yt-dlp full extraction fails with "The page needs to be reloaded". Cookies are only for listing playlists.

Statuses: `unwatched`, `in_progress`, `completed`, `skipped`. `storage.DONE_STATUSES` (completed and skipped) don't count toward remaining time, in `metrics.py` and in the client's `isDone()`. Adding a status means adding it to `VALID_STATUSES`. `init_db()` then rebuilds the `videos` table, because SQLite can't alter a CHECK constraint. **Video details (`enrich.py`)**: `run_enrich` goes through `storage.videos_needing_details()` (no row yet, or `failed` with fewer than 3 tries; not-done videos first). It makes **anonymous** yt-dlp reads, using Node/Deno as the JS runtime when one is installed. Requests are paced at about 3 s plus jitter, the default limit is 200 per run, and the run stops at the first bot-check or 429. Categories whose names look like music (`MUSIC_NAME`), or that are switched off in `settings.json["transcripts"]`, are never requested. Statuses in `video_details`: done, no_captions, music (YouTube category), unavailable, failed. A failed retry keeps earlier good data. Captions are stored in `transcript_segments`; the track preference is in `choose_track`. `/api/state` carries only a small `details` summary per video. The full text comes from `GET /api/video?id=`, and the job can be stopped with `POST /api/job/cancel`.

**AI watch ratings (`rater.py`)**:
- `run_rate` makes one call per unwatched, unrated video. It returns a score (0–10), a verdict (watch/skim/skip), a reason, skim ranges, a TL;DR and timestamped points, written in `settings["language"]`.
- The prompt weighs depth and quality, fit with `settings["interests"]` and the categories (Keep presets marked), time against value, and freshness (`video_details.upload_date` against today).
- It uses the transcript (merged into 30 s blocks, sampled to fit a character budget that is smaller for local models) or falls back to metadata only (`basis`).
- `validate()` clamps the reply and drops timestamps outside the video.
- Results go in `video_ratings`. A rating never changes a video; Triage applies the Skipped status when the user accepts. `rater.estimate()` powers the cost confirmation.

**AI per task**: `settings["tasks"]` maps `categorize` and `rate` to a provider (unset = the default `provider`). Always call `llm.make_completer(settings, task)`, `is_configured(settings, task)` and `describe(settings, task)` with the task. `llm.PRICES` holds approximate prices per model for estimates; local models are free and unknown models show "cost unknown".

**Local AI (Phase 2.5 L3)**:
- The Ollama provider uses the native API (`kind: "ollama"`, `/api/chat`), never `/v1`. `/v1` can't set `num_ctx`, so long prompts would be silently cut. Base URLs saved with `/v1` are migrated in `load_settings()`.
- `llm.context_chars()` sizes the transcript budget from `num_ctx`.
- `settings["local_only"]` blocks cloud providers in `is_configured` and in `make_completer`.
- `local_setup.py` handles the machine check, the model recommendation (`RECOMMENDED`, by VRAM), `pull_model` (job kind `pull`) and `use_model`. It also has a CLI that `scripts/setup.sh` (Linux/macOS) and `scripts/setup.ps1` (Windows, untested) use.

**Triage (`#/triage`)** is a ranked list (highest score first). It has verdict chips, Accept all skips, Undo, Rate (batch or one video), and a headline comparing time if you follow the ratings with time for everything. Rows and the library show YouTube thumbnails from `i.ytimg.com`.

## Not built yet (see the research doc §5–6)

- Writing `[YT-KB]` playlists back via the Data API: 50 units per insert against a 10k/day quota, so it needs a paced multi-day queue, and the source playlist must be hard-blocked from every write. The user hasn't decided whether they want it.
- Per-video enrichment (description and tags) for better AI classification, and captions or transcripts (Phase 1).
