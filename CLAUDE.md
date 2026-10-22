# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

YT KB (YouTube Knowledge Base): a local Python tool that reads YouTube playlists (often a private "Watch Later"-style backlog), sorts videos into topic categories, works out watch time at 1x through 2x speed, fetches transcripts, rates videos with AI (Watch / Skim / Skip), searches everything, and tracks progress in a local dashboard. Specs:
- `docs/PRD-Phase-0.md` (original) and `docs/Phase-0-Research-and-Improvements.md` (research, revised requirements v0.2, roadmap, release checklist). The second supersedes the first where they disagree.
- `docs/PRD-Phase-1.md`: complete.
- `docs/PRD-Phase-2.5.md`: everything processed locally (complete).
- `docs/PRD-Phase-2.md`: ask your library, answers cited to videos and moments (complete).
- `docs/MANUAL-TESTS.md`: checks that need the user (real playlists, hardware, cloud keys).
- `docs/SETUP.md`: the user-facing setup guide.

**Hard rule: the source playlist is never modified.** Non-destructiveness is a core requirement. Many playlists can be ingested; each is a *source* feeding one deduplicated library. A video that disappears from a source is flagged (`playlist_items.removed = 1`), never deleted, and **stays in the library (and in remaining-time totals) until it is marked watched**.

## Commands

```bash
python3 -m venv --without-pip .venv && python3 -m pip --python .venv/bin/python install -r requirements.txt
.venv/bin/python dashboard.py            # local dashboard at http://127.0.0.1:8765 (--port, --no-open)
.venv/bin/python main.py "<playlist URL | ID | WL>" [--browser auto|firefox|chrome | --cookies cookies.txt] [--ai] [--no-open]
.venv/bin/python main.py --list [--browser auto]   # list the signed-in account's playlists and IDs
.venv/bin/python main.py --details [N]             # fetch details/captions for up to N videos (default 200)
.venv/bin/python main.py --embed                   # update the meaning-based search index (Ollama embedding model)
.venv/bin/python -m unittest             # test suite (tests/), runs against temp files; API keys are stripped
YTKB_LIVE=1 ANTHROPIC_API_KEY=... .venv/bin/python -m unittest tests.test_live -v   # opt-in checks against real cloud AIs
bash scripts/setup.sh [--local-ai]       # first-time setup for users (venv, deps, optional Ollama + model); guide: docs/SETUP.md
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
4. **`metrics.py`**: counts and durations at 1.0/1.25/1.5/1.75/2.0x. The remaining time leaves out done videos (`completed` and `skipped`).
5. **`reporter.py`**: writes static snapshots to `reports/`. User content must be HTML-escaped.
**AI layer (optional)**: `llm.py` holds the provider registry and `settings.json` (git-ignored; API keys, presets, category notes, a pending proposal). Every provider reduces to `complete(system, prompt) -> text`. Claude goes through the `anthropic` SDK. Gemini uses REST. OpenAI-compatible endpoints cover open-weight hosts and local Ollama/LM Studio. `public_settings()` is the only form sent to the browser and never includes keys. `ai_classifier.py` has `discover_categories` (proposes a category set that keeps the user's presets verbatim) and `assign_categories` (indexed lines, batches of at most 50 videos packed to the model's context, unknown answers go to the fallback). `run_discover`/`run_suggest` are the job entry points. AI output is always a proposal or suggestion that the user accepts in the dashboard.

6. **`exporter.py`**: builds `watch_videos?video_ids=` queue URLs. The endpoint is undocumented and capped at 50 IDs, so use the chunked `generate_youtube_watch_queue_urls`. Also handles CSV and JSON export.

**Dashboard**: `dashboard.py` is a stdlib `ThreadingHTTPServer` bound to 127.0.0.1. It serves `web/index.html`, a single-file vanilla JS app with hash routing (`#/` library with status/category/playlist filters in the left rail, including an "AI suggestions" view, `#/plan`, `#/sources`, `#/categories` with presets ("Keep") and proposal review, `#/ai` provider settings, `#/triage`, `#/search`, `#/ask`, `#/setup`). `/api/state` returns the whole library via `storage.get_library()` (every video with its `sources` and `in_source`); all filtering happens client-side, and a JSON API:
- `GET /api/state`, `/api/job`, `/api/export?format=&playlist=`, `/api/export/markdown?id=`, `/api/video?id=`, `/api/search?q=&kinds=`, `/api/local/status`, `/docs/setup`, `/download/<name>`
- `POST /api/videos/update`, `/api/taxonomy`, `/api/categories/{rename,delete}`, `/api/reclassify`, `/api/ingest`, `/api/presets`, `/api/job/cancel`
- `POST /api/ai/{settings,test,models,discover,suggest}`, `/api/ai/proposal/{apply,dismiss}`, `/api/ai/suggestions/{accept,dismiss}`
- `POST /api/details/{fetch,settings}`, `/api/rate`, `/api/rate/estimate`, `/api/local/{pull,use}`, `/api/export/markdown`

POSTs must send the `X-YTKB: 1` header and JSON content. The Host header must be localhost. Together these act as CSRF and DNS-rebinding protection; keep them. Syncs, details fetches, AI runs and model downloads share one background `Job` slot (`kind`: sync, details, discover, suggest, rate, pull), with stdout captured into the job log. A job can be stopped with `POST /api/job/cancel` if it checks `JOB.stop_requested`. When `auto_after_sync` is on, a sync also asks for suggestions on unsorted videos. The client computes metrics itself (mirroring `metrics.py`) from the video list in `/api/state`. Setting a category through the API locks it; `reset_category` unlocks it and re-classifies.

## Phase 1 (complete)

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

**Local transcription (Phase 2.5 L1, `local_asr.py`)**:
- `run_asr` (job kind `asr`) works through `storage.videos_needing_asr()`. Its reasons are `no_captions`, and `wrong_script` from `captions_look_wrong()`: auto-captions in a non-Latin script for a Latin-script title.
- Per video:
  1. Download the **original** audio track (`AUDIO_FORMAT`; dubbed videos carry many), anonymously.
  2. Decode with ffmpeg; don't use faster-whisper's PyAV decoder, which breaks.
  3. Unload Ollama, then transcribe (`large-v3-turbo` int8 on CUDA, `small` int8 on CPU; `settings["asr_model"]`).
  4. Delete the audio, then pause if the GPU runs hot.
- `media_jobs` tracks each job's status, tries, progress and timings. Downloaded media lives only in git-ignored `media_cache/`.
- `transcript_segments.source` is `captions` or `local-asr`. A video can have both; `video_details.transcript_source` picks one (NULL = captions, or the local transcript when there are no captions). **Every transcript reader must go through `_active_source`.** `save_details` replaces only `captions` rows.
- `requirements-local.txt` holds faster-whisper and the NVIDIA cuBLAS/cuDNN wheels; `preload_cuda_libs()` loads them without LD_LIBRARY_PATH.

**Slides and whiteboards (Phase 2.5 L2, `local_visual.py`)**:
- Videos are read when `videos.has_visuals = 1` (set by the user), or when their category's switch is on (`settings["visuals"]`) and they aren't marked 0. `storage.visual_hints()` only *suggests* marks, from title words or transcript phrases like "this slide" or "as you can see".
- `run_visual` (job kind `visual`) per video:
  1. Download the video only, ≤480p (`visual_height`), whole file, anonymously.
  2. `extract_frames`: ffmpeg scene change + every 30 s, then `select_frames` (10 s gap, dHash Hamming ≤ 6, ≤ 150 frames).
  3. `read_frame`: Ollama vision model (`vision_model` or the Ollama text model; checked with `/api/show` capabilities). It returns `content`, `text` and `visual`; frames where `content` is false are dropped.
  4. `save_thumb` writes `frames/<id>/<sec>.jpg` (320 px, git-ignored), then the video is deleted.
- `visual_segments` holds the results. Search kind `slide`; ratings get on-screen text (at most ¼ of the budget); notes get an "On screen" section. `GET /frames/<id>/<sec>.jpg` serves only that exact shape.

**Meaning-based retrieval (Phase 2)**:
- `search_docs.hash` (sha1 of kind + text) keys `embedding_cache(model, hash, dim, vector)`, a float32 unit vector.
- `embedder.run_embed` embeds only missing hashes through Ollama `/api/embed` (`settings["embed_model"]`, default `qwen3-embedding:0.6b`) and prunes orphans. The dashboard `Job` runs it after `sync`, `details`, `asr`, `visual` and `rate` jobs (`AUTO_EMBED_KINDS`).
- `retrieval.retrieve(q, k, filters)` fuses meaning (cosine over an in-memory matrix, refreshed when `storage.index_version()` changes; queries use `embedder.QUERY_PREFIX`) with FTS any-term keywords at weight 0.5 (RRF k=60). It caps 4 pieces per video and falls back to keywords alone. `GET /api/retrieve?q=&k=&categories=&playlists=&videos=&done=`.
- `storage.get_connection()` returns a connection that **closes** when its `with` block ends. Always use it in a `with` block.
- Retrieval keeps the top `KEEP_TOP` (6) pieces, then caps 4 per video.

**Ask (`#/ask`, `asker.py`)**: `ask()` retrieves 8 pieces, numbers them as excerpts, calls the `ask` task (`llm.TASK_FOLLOWS`: unset `tasks.ask` uses `tasks.rate`, then the default) and runs `check_citations`: invalid numbers dropped; a citation moves to another excerpt only when that one matches its sentence ≥ `MOVE_MARGIN` better and ≥ `MOVE_FLOOR` (embedding cosine; skipped when pieces have no vectors or Ollama is down). The AI page's *Meaning-based search* card (`local_setup.embed_status`, `EMBED_MODELS`) downloads/switches the embedding model, shows index status, and runs `POST /api/embed {rebuild}`; downloading the model in use builds the index (`dashboard._pull_then_index`). Answers are kept in `ask_history` (`storage.save_ask/list_asks/get_ask/delete_ask`), with the excerpts sent. Follow-ups: `ask(..., follow_up_of=id)` joins that conversation (`thread_id` = first question's id), keeps its filters, and rewrites the question with `standalone_question` before retrieval. `list_asks` returns conversations; `get_thread`, `delete_ask` (whole conversation) and `exporter.ask_markdown(thread)` work on conversations. Search page **By meaning** = `retrieval.search_by_meaning` (`/api/search?meaning=1`), shaped like `storage.search`. One answer at a time (`asker._lock`); `POST /api/ask` runs synchronously, not in the Job slot. API: `POST /api/ask`, `GET /api/ask/history?q=`, `/api/ask/item?id=`, `/api/export/ask?id=`, `POST /api/ask/delete`.

**Search (`#/search?q=`)**:
- `search_docs` holds one row per searchable piece (`kind`: title, chapter, summary, notes, description, transcript; `t` = second). The FTS5 table `search_index` (trigram tokenizer, external content) follows it through insert and delete triggers.
- `storage._reindex(cursor, ids, kinds)` must run whenever indexed text changes. It is already called from `save_playlist` (title), `update_videos` (notes), `save_details` (description, chapter, transcript) and `save_rating` (summary). `init_db` fills the index for existing databases.
- `storage.search()` turns user text into quoted FTS terms (3+ characters; quotes stripped), weights hits by kind, groups them by video, and crops snippets with `\x02`/`\x03` markers that the client escapes and highlights. `GET /api/search?q=&kinds=`.

**Smarter categories and notes export**: `run_suggest` adds `storage.ai_context()` (summary, description and transcript opening) to each video, and `ai_classifier._pack` sizes batches to `llm.context_chars(settings, "categorize")`. `exporter.video_markdown` and `markdown_zip` build the notes. They escape user and AI text with `_md_text` (but keep the user's own notes as Markdown) and never include transcripts. `GET /api/export/markdown?id=` returns one note; `POST /api/export/markdown {ids}` returns a zip.

**Setup page (`#/setup`)** renders `docs/SETUP.md` with a small escaping Markdown renderer (`md()` in index.html). `GET /download/<name>` serves only the files in `dashboard.DOWNLOADS` (setup.sh, setup.ps1, SETUP.md). Keep docs/SETUP.md in step with CLI flags, `settings.json` keys and environment variables whenever they change.

**Triage (`#/triage`)** is a ranked list (highest score first). It has verdict chips, Accept all skips, Undo, Rate (batch or one video), and a headline comparing time if you follow the ratings with time for everything. Rows and the library show YouTube thumbnails from `i.ytimg.com`.

## Not built yet

- Writing `[YT-KB]` playlists back via the Data API: 50 units per insert against a 10k/day quota, so it needs a paced multi-day queue, and the source playlist must be hard-blocked from every write. The user hasn't decided whether they want it.
- Public release: the checklist in section 9 of the research doc (deferred).
