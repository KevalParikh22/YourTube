# AGENTS.md — rules for every AI working in this repo

Claude Code, Gemini CLI and Codex CLI (and any optional worker, such as opencode or Kimi) read this file.
It is the single source of truth. Follow it exactly, whichever model you are.

---

## 1. Project facts

- **What this product is:** YourTube (formerly YT KB): a local Python tool that reads YouTube playlists (including private ones and Watch Later via browser cookies), sorts videos into categories, works out watch time, fetches or locally transcribes transcripts, rates videos with AI (Watch / Skim / Skip), searches them and answers questions with cited moments, all tracked in a local dashboard.
- **Stack:** Python 3 stdlib (`http.server` dashboard, SQLite with FTS5) + `yt-dlp`; vanilla JS single-file frontend `web/index.html`; optional AI through `llm.py` (Claude via `anthropic`, Gemini REST, OpenAI-compatible, Ollama native API); optional local ASR (faster-whisper) and ffmpeg.
- **Run locally:** `.venv/bin/python dashboard.py` (http://127.0.0.1:8765); pipeline: `.venv/bin/python main.py "<playlist URL | ID | WL>"`
- **Verify (must pass before any task is "done"):** `.venv/bin/python -m unittest`
- **Architecture summary:** see the "Architecture" and phase sections of `CLAUDE.md`, and `docs/architecture.md`. Specs are `docs/PRD-*.md`.
- **Never touch:** `yt_kb.db` and `*.db` backups (the user's real library), `settings.json` (API keys), `taxonomy*.json`, `cookies*.txt`, `media_cache/`, `frames/`, `reports/`, `.venv/`. To experiment, point `YTKB_DB`, `YTKB_TAXONOMY` and `YTKB_SETTINGS` at temp paths.
- **Hard rule:** the source YouTube playlist is never modified. Videos missing from a source are flagged (`playlist_items.removed = 1`), never deleted.

## 2. Conventions

- Python: snake_case, small top-level modules (one per pipeline stage), stdlib first; no new dependencies unless the task says so. No linter or build step.
- Video records are plain dicts with keys `video_id, title, channel, duration_seconds, url, position, category, status`.
- DB access: always `with storage.get_connection() as conn:` (the connection closes at block end). Schema changes go in `storage.init_db()` as in-place migrations. Call `storage._reindex` whenever searchable text changes.
- Transcript readers go through `_active_source`. Per-video yt-dlp reads are anonymous (no cookies); cookies are only for listing playlists.
- Dashboard: POSTs require the `X-YTKB: 1` header, JSON body and a localhost Host header; keep these checks. Long work goes through the `dashboard.Job` queue. Job output is plain text, no emojis; errors start with "Error:", warnings with "Warning:".
- Escape user and AI content in HTML (`web/index.html`) and Markdown (`exporter._md_text`). `public_settings()` is the only settings shape sent to the browser; never expose keys.
- AI output is always a proposal or suggestion the user accepts; never apply it automatically. Use `llm.make_completer(settings, task)` with the task name.
- Tests: `unittest` in `tests/test_<module>.py`; they run against temp files (`tests/__init__.py`) with API keys stripped. Live cloud tests are opt-in (`YTKB_LIVE=1`).
- Keep `docs/SETUP.md` in step with CLI flags, `settings.json` keys and env vars.

---

<!-- llm-team:start -->
## 3. How to work (the "Claude standard")

These habits are what make output reliable. They are mandatory.

1. **Read before you write.** Open the files you will change and the files that call them. Match the existing style, naming and patterns. Never guess at an API, function or file that you have not seen. Search the repo first.
2. **Stay in scope.** Do only the task you were given. No drive-by refactors, renames, reformatting or "improvements" to unrelated code. If you notice a real problem elsewhere, mention it in your report instead of fixing it.
3. **Small, complete diffs.** Prefer the smallest change that fully solves the task. No TODO stubs, no placeholder logic, no commented-out code left behind.
4. **No invented dependencies.** Don't add a package unless the task says so. If you think one is needed, say so in the report.
5. **Handle the unhappy path.** Validate inputs at boundaries, handle errors explicitly, think about empty, null and very large cases.
6. **Tests prove it.** Add or update tests for behaviour you change. Run the verify command from section 1. A task is NOT done until it passes.
7. **Don't fake success.** If checks fail or you could not finish, say so plainly with the error output. A truthful `PARTIAL` or `BLOCKED` is far better than a false `DONE`.
8. **Security basics.** Never hard-code secrets. Never log tokens or passwords. Use parameterised queries. Escape user content in HTML.
9. **Comments explain why, not what.** Keep comment density similar to the surrounding code.

---

## 4. Team protocol (multi-LLM)

This repo is worked on by several AI tools. **Claude is the orchestrator**: it plans, assigns tasks, verifies and commits. Gemini and Codex are **workers** by default; opencode and Kimi can be added in `.team/team.conf`.

**If you are a worker (Gemini / Codex / any optional worker):**
- Your task arrives as a prompt that references a plan file in `docs/plan/`. Read that plan and do ONLY the task id you were given.
- **Do not** `git commit`, `git push`, switch branches, or edit `HANDOFF.md` or `docs/plan/*`. The orchestrator does that.
- **Do not** edit `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.claude/`, `.team/`.
- End every response with the report format below, exactly.

```
## RESULT: DONE | PARTIAL | BLOCKED
## Files changed
- path — one-line summary
## Checks run
- command → pass/fail (paste the key error lines if it failed)
## Notes
- assumptions, open questions, problems spotted outside scope
```

**Shared state files:**
- `docs/plan/<feature>.md`: the plan and task list (owned by the orchestrator)
- `HANDOFF.md`: where things stand right now. Read it first when you start a session.
- `docs/architecture.md`: a map of the codebase. Read it instead of scanning everything.
<!-- llm-team:end -->
