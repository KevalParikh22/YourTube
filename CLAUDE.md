# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

YT KB (YouTube Knowledge Base), Phase 0: a local Python tool that reads a YouTube playlist (often a private "Watch Later"-style backlog), sorts its videos into topic categories, works out watch time at 1x through 2x speed, and tracks progress in a local dashboard. Specs: `docs/PRD-Phase-0.md` (original) and `docs/Phase-0-Research-and-Improvements.md` (research findings, revised requirements v0.2, roadmap). The second document supersedes the first where they disagree.

**Hard rule: the source playlist is never modified.** Non-destructiveness is a core requirement. Many playlists can be ingested; each is a *source* feeding one deduplicated library. A video that disappears from a source is flagged (`playlist_items.removed = 1`), never deleted, and **stays in the library (and in remaining-time totals) until it is marked watched**.

## Commands

```bash
pip install -r requirements.txt          # only dependency: yt-dlp
python dashboard.py                      # local dashboard at http://127.0.0.1:8765 (--port, --no-open)
python main.py "<playlist URL | ID | WL>" [--browser firefox | --cookies cookies.txt] [--no-open]
python main.py                           # interactive prompts
```

There is no test suite, linter, or build step. To try things without touching the real `yt_kb.db`, set `YTKB_DB=<path>` (and `YTKB_TAXONOMY=<path>` for `taxonomy.json`). Both `storage.py` and `classifier.py` read these at import time.

## Architecture

Every stage passes around a list of plain video dicts with the keys `video_id, title, channel, duration_seconds, url, position, category, status`. The pipeline in `main.py` `run_pipeline()` runs these stages:

1. **`extractor.py`**: yt-dlp flat extraction with `skip_download`. It uses no Google API quota and needs no OAuth. Private access comes from browser cookies or a cookies.txt file. The YouTube Data API cannot read Watch Later, which is why yt-dlp is used. On Windows, Chromium cookies usually fail because of app-bound encryption, so Firefox or a cookies.txt file is recommended.
2. **`classifier.py`**: keyword scoring over title + channel (single word scores 1, multi-word phrase scores 2). The taxonomy comes from `taxonomy.json`, written by the dashboard, or `DEFAULT_TAXONOMY` if that file is absent. A video with no match goes to `FALLBACK_CATEGORY` ("General / Other").
3. **`storage.py`**: SQLite. `init_db()` creates the tables and migrates older DBs in place. Tables:
   - `playlists`
   - `videos`: one row per video. Category, status, notes, and `category_locked` live here.
   - `playlist_items`: many-to-many membership with `position` and `removed`.

   `save_playlist()` keeps status and notes, and keeps the category when `category_locked = 1`. It marks videos missing from the latest extraction as removed, and never does so from an empty extraction. After saving, `main.py` reloads from the DB so reports reflect manual edits.
4. **`metrics.py`**: counts and durations at 1.0/1.25/1.5/1.75/2.0x. The remaining time leaves out `completed` videos.
5. **`reporter.py`**: writes static snapshots to `reports/`. User content must be HTML-escaped.
6. **`exporter.py`**: builds `watch_videos?video_ids=` queue URLs. The endpoint is undocumented and capped at 50 IDs, so use the chunked `generate_youtube_watch_queue_urls`. Also handles CSV and JSON export.

**Dashboard**: `dashboard.py` is a stdlib `ThreadingHTTPServer` bound to 127.0.0.1. It serves `web/index.html`, a single-file vanilla JS app with hash routing (`#/` library with status/category/playlist filters in the left rail, `#/plan`, `#/sources`, `#/categories`). `/api/state` returns the whole library via `storage.get_library()` (every video with its `sources` and `in_source`); all filtering happens client-side, and a JSON API:
- `GET /api/state?playlist=`, `/api/job`, `/api/export`
- `POST /api/videos/update`, `/api/taxonomy`, `/api/categories/{rename,delete}`, `/api/reclassify`, `/api/ingest`

POSTs must send the `X-YTKB: 1` header and JSON content. The Host header must be localhost. Together these act as CSRF and DNS-rebinding protection; keep them. Ingestion runs `run_pipeline` in one background thread with stdout captured into the job log. The client computes metrics itself (mirroring `metrics.py`) from the video list in `/api/state`. Setting a category through the API locks it; `reset_category` unlocks it and re-classifies.

## Not built yet (see the research doc §5–6)

- An LLM classifier with suggestion review.
- Writing `[YT-KB]` playlists back via the Data API: 50 units per insert against a 10k/day quota, so it needs a paced multi-day queue, and the source playlist must be hard-blocked from every write.
- Automated tests.
