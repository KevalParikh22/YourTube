# Architecture

Map of the codebase for people and AI workers. Details per phase live in `CLAUDE.md`; specs are `docs/PRD-*.md`.

## Folders

- **Project root:** Python modules for playlist sync, storage, dashboard, AI, search, and exports. `requirements.txt` lists app dependencies; `requirements-local.txt` covers optional local features.
- **`web/`:** Single-file dashboard frontend at `web/index.html`; the server points to this directory ([dashboard.py:26-33](dashboard.py)).
- **`docs/`:** Setup and manual-test guides, PRDs, and the architecture stub. `docs/SETUP.md` documents setup and launch ([docs/SETUP.md:20-42](docs/SETUP.md)).
- **`scripts/`:** Linux/macOS and Windows setup scripts ([docs/SETUP.md:22-36](docs/SETUP.md)).
- **`tests/`:** `unittest` coverage by module; test setup redirects DB, taxonomy, and settings to temporary files ([tests/__init__.py:1-18](tests/__init__.py)).
- **`spikes/`:** Experimental scripts for ASR, downloads, frames, embeddings, and evaluation ([spikes/README.md](spikes/README.md)).

## Data flow

1. **Ingest:** `main.py` calls `extractor.extract_playlist_metadata` (or dashboard sync routes through the same pipeline). `extractor.py` uses `yt-dlp` to read playlist metadata; private playlists and Watch Later can use browser or file cookies ([main.py:16-23](main.py), [main.py:36-41](main.py), [extractor.py:86-100](extractor.py)).
2. **Classify and persist:** `classifier.py` applies taxonomy rules; `storage.py` saves playlist/video state to SQLite. Missing source videos are flagged as removed and kept locally ([main.py:65-74](main.py), [AGENTS.md:15-16](AGENTS.md)).
3. **Enrich and analyze:** `enrich.py` fetches descriptions, chapters, and captions; `local_asr.py` optionally transcribes locally. `ai_classifier.py` proposes category suggestions, while `rater.py` rates videos. `llm.py` provides configurable AI providers ([enrich.py:149](enrich.py), [local_asr.py:159-165](local_asr.py), [ai_classifier.py:221-232](ai_classifier.py), [rater.py:242-245](rater.py), [llm.py:1-12](llm.py)).
4. **Search and ask:** `storage.py` maintains text search and transcript sources; `embedder.py` builds meaning-based indexes; `retrieval.py` retrieves matching material; `asker.py` answers with cited moments ([storage.py:916-925](storage.py), [storage.py:1019-1027](storage.py), [embedder.py:54-62](embedder.py), [retrieval.py:73-84](retrieval.py), [asker.py:146-159](asker.py)).
5. **Present and export:** `dashboard.py` serves the local API and `web/index.html`; `metrics.py`, `reporter.py`, and `exporter.py` provide watch-time calculations and report/export formats ([dashboard.py:1-4](dashboard.py), [metrics.py:39-47](metrics.py), [reporter.py:16-23](reporter.py), [exporter.py:32-41](exporter.py)).

## Key modules

- **`main.py`:** CLI and sync pipeline; options include browser/cookies, playlist listing, details, AI suggestions, and embedding ([main.py:139-149](main.py)).
- **`dashboard.py`:** Local HTTP server, API handlers, and queued background jobs ([dashboard.py:39-43](dashboard.py), [dashboard.py:267-310](dashboard.py)).
- **`storage.py`:** SQLite schema/migrations and persistence for playlists, video details, transcripts, search, embeddings, Q&A, and libraries ([storage.py:31-36](storage.py), [storage.py:71-75](storage.py), [storage.py:259-268](storage.py)).
- **`classifier.py`, `enrich.py`, `local_asr.py`, `local_visual.py`, `ai_classifier.py`, `rater.py`:** Categorization, metadata/caption fetching, optional local transcription and visual analysis, AI category proposals, and ratings.
- **`llm.py`, `embedder.py`, `retrieval.py`, `asker.py`:** AI provider configuration, embedding, retrieval, and question answering.
- **`metrics.py`, `reporter.py`, `exporter.py`:** Watch-time metrics, generated reports, and exports.

## Run and test

- Setup: `bash scripts/setup.sh` (optionally `--local-ai`); details in [docs/SETUP.md:20-42](docs/SETUP.md).
- Dashboard: `.venv/bin/python dashboard.py` at `http://127.0.0.1:8765` ([AGENTS.md:10-13](AGENTS.md)).
- CLI sync: `.venv/bin/python main.py "<playlist URL | ID | WL>"` ([AGENTS.md:10-13](AGENTS.md)).
- Tests: `.venv/bin/python -m unittest` ([AGENTS.md:12-13](AGENTS.md)). `tests/test_live.py` is opt-in via `YTKB_LIVE=1` ([AGENTS.md:27](AGENTS.md)).
