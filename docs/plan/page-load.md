# Plan: Remove the old static report, faster library load

**Goal:** Syncs stop writing the unused Phase 0 static report, and every library load (`/api/state`) skips the transcript phrase scan unless transcripts changed.
**Branch:** feat/page-load
**Out of scope:** gzip for `/api/state` (local only, saves almost nothing), changes to `metrics.py` behaviour, any UI change.

## Design notes

Measured on 2026-10-28 (313 videos): `/api/state` 0.195 s, of which `storage.visual_hints()` is 0.12 s. It runs a `LIKE` scan over every `transcript_segments` row on every call. Page rendering in the browser is under 10 ms (Triage 41 ms), and other endpoints take 0.004–0.07 s.

### T1: delete the old report
- Delete `reporter.py`. In `main.py`, remove: the `reporter` import, the `webbrowser` import, Step 5 (generate reports), `open_browser` (parameter, uses, and the `--no-open` CLI flag), and the "reports" wording in the module docstring.
- Keep Step 4: the metrics summary printed to the console (`metrics.calculate_category_metrics`). `metrics.py` stays.
- Callers: `dashboard._sync` passes `open_browser=False`, so remove that argument. Grep for `open_browser`, `reporter`, `generate_markdown_report`, `generate_html_dashboard` and `--no-open` across `*.py`, `docs/`, `README.md`, `CLAUDE.md`, `AGENTS.md`, `scripts/`, then update or remove each reference. In `CLAUDE.md`'s command list, `main.py ... [--no-open]` loses `--no-open`; `dashboard.py --no-open` stays (that is the dashboard's own flag). Remove the `reporter.py` architecture line in CLAUDE.md and `docs/architecture.md`.
- Leave the `reports/` folder and its `.gitignore` entry alone (user files).

### T2: cache the phrase scan
- In `storage.visual_hints`, the phrase counts (`{video_id: count}` from the `LIKE` query) depend only on transcripts. Cache them in a module-level dict keyed by `(db_path, index_version(db_path))`, the same idea as `retrieval._matrix`. Saving or replacing any transcript calls `_reindex`, which changes `index_version`. The second loop (titles, `has_visuals`, status) stays uncached, because the user changes `has_visuals` without touching the index.
- Keep the cache small: store only the last key and its value. Guard it with a `threading.Lock`, because the dashboard is multi-threaded.
- Test: call `visual_hints` twice and patch `get_connection` to count the `LIKE` query, so the second call doesn't scan; after `save_details` with new text that contains "this slide", the hint appears (the cache was invalidated); toggling `has_visuals` still takes effect at once.

## Tasks

### T1: Delete the old static report
- **worker:** agy
- **status:** done (by claude: agy's allow-list doesn't permit deleting files)
- **files:** `reporter.py` (delete), `main.py`, `dashboard.py`, `CLAUDE.md`, `docs/architecture.md`, `README.md` and `docs/SETUP.md` only if they mention it
- **steps:** Design notes T1.
- **acceptance:** [ ] `grep -rn "reporter\|open_browser\|generate_html_dashboard" --include=*.py .` (excluding .venv) finds nothing; [ ] `main.py --help` has no `--no-open`; [ ] tests pass.
- **verify:** `.venv/bin/python -m unittest`

### T2: Cache the transcript phrase scan in visual_hints
- **worker:** codex
- **status:** todo
- **files:** `storage.py`, `tests/test_visual.py`
- **steps:** Design notes T2.
- **acceptance:** [ ] the second call doesn't run the scan; [ ] new transcript text and `has_visuals` changes still take effect; [ ] `/api/state` takes less time than before (measure it).
- **verify:** `.venv/bin/python -m unittest`
