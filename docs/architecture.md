# Architecture

Map of the codebase. Specs are `docs/PRD-*.md`; the user guide is `docs/SETUP.md`.

## Folders

- **Project root:** Entry point shims (`dashboard.py`, `main.py`, `local_setup.py`), dependency manifests (`requirements.txt`, `requirements-local.txt`, `requirements-nvidia.txt`), configuration, and local data files (`yt_kb.db`, `settings.json`, `taxonomy.json`).
- **`yourtube/`:** Application modules (one module per pipeline stage: ingest, storage, classifier, AI, search, etc.). Added to `sys.path` by entry points and tests.
- **`web/`:** Dashboard frontend: `web/index.html` (markup), `web/static/app.js` and `app.css` (the app, served no-cache), fonts and icon, `web/static/api.html` plus the pinned `swagger/` bundle for `/docs/api`. Everything under `/static/` goes through the `dashboard.STATIC` allow-list.
- **`yourtube/files.py`:** Settings → Storage: `usage()`, `backup()` / `restore()` (SQLite backup API; never settings.json), `clear(key)`, `save_export()` (every export is also kept in `exports/`).
- **`docs/openapi.json`:** OpenAPI 3.1 description of every `/api/` route; `tests/test_openapi.py` fails when a route is added without it.
- **`docs/`:** Documentation index (`docs/README.md`), setup guide (`docs/SETUP.md`), manual-test guides, PRDs, and architecture map.
- **`scripts/`:** Linux/macOS and Windows setup and service scripts (`scripts/setup.sh`, `scripts/service.sh`, `scripts/setup.ps1`, `scripts/service.ps1`).
- **`tests/`:** `unittest` coverage by module; test setup redirects DB, taxonomy, and settings to temporary files (`tests/__init__.py`).
- **`spikes/`:** Experimental scripts for ASR, downloads, frames, embeddings, and evaluation (`spikes/README.md`).

## Data flow

1. **Ingest:** The root `main.py` runs `yourtube/main.py`, which calls `extractor.extract_playlist_metadata` (or dashboard sync routes through the same pipeline). `extractor.py` uses `yt-dlp` to read playlist metadata; private playlists and Watch Later can use browser or file cookies.
2. **Classify and persist:** `yourtube/classifier.py` applies taxonomy rules; `yourtube/storage.py` saves playlist/video state to SQLite. Missing source videos are flagged as removed and kept locally.
3. **Enrich and analyze:** `yourtube/enrich.py` fetches descriptions, chapters, and captions; `yourtube/local_asr.py` optionally transcribes locally with Whisper; `yourtube/local_visual.py` reads slide frames. `yourtube/ai_classifier.py` proposes category suggestions, while `yourtube/rater.py` rates videos. `yourtube/llm.py` provides configurable AI providers.
4. **Search and ask:** `yourtube/storage.py` maintains text search (SQLite FTS5) and transcript sources; `yourtube/embedder.py` builds meaning-based vector indexes; `yourtube/retrieval.py` retrieves matching material; `yourtube/asker.py` answers with cited moments.
5. **Present and export:** `dashboard.py` (via `yourtube/dashboard.py`) serves the local API and `web/index.html`; `yourtube/metrics.py` and `yourtube/exporter.py` provide watch-time calculations and export formats (CSV, JSON, Markdown notes).

## Key modules

- **Root entry points:** `dashboard.py`, `main.py`, and `local_setup.py` are thin shims that insert `yourtube/` onto `sys.path` and run the corresponding module via `runpy`.
- **`yourtube/main.py`:** CLI and sync pipeline; options include browser/cookies, playlist listing, details, AI suggestions, and embedding.
- **`yourtube/dashboard.py`:** Local HTTP server, API handlers, and queued background jobs (`dashboard.Job`).
- **`yourtube/storage.py`:** SQLite schema/migrations and persistence for playlists, video details, transcripts, search, embeddings, Q&A, and libraries.
- **`yourtube/classifier.py`, `enrich.py`, `local_asr.py`, `local_visual.py`, `ai_classifier.py`, `rater.py`:** Categorization, metadata/caption fetching, optional local transcription and visual analysis, AI category proposals, and ratings.
- **`yourtube/llm.py`, `embedder.py`, `retrieval.py`, `asker.py`:** AI provider configuration, embedding, retrieval, and question answering.
- **`yourtube/metrics.py`, `exporter.py`, `tools.py`, `knowledge.py`, `voice.py`:** Watch-time metrics, exports, playlist tools, knowledge synthesis pages, and voice recording transcription.

## Run and test

- Setup: `bash scripts/setup.sh` (optionally `--local-ai`, `--service`); details in `docs/SETUP.md`.
- Dashboard: `.venv/bin/python dashboard.py` at `http://127.0.0.1:8765`.
- CLI sync: `.venv/bin/python main.py "<playlist URL | ID | WL>"`.
- Local AI setup: `.venv/bin/python local_setup.py`.
- Tests: `.venv/bin/python -m unittest`. `tests/test_live.py` is opt-in via `YTKB_LIVE=1`.
