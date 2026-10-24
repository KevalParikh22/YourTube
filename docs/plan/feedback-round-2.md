# Plan: Feedback round 2 — Stop that works, a polished UI, and ratings you can trust

**Goal:** Stop ends any task within one step and the page shows "Stopping…". Each rating explains its score and comes with timestamped minutes you can decide from. The dashboard looks clean and professional: system fonts, styled dropdowns, a favicon, a scrollable left rail, an icon-only header search, readable categories, short labels with tooltips instead of paragraphs, and working pagination. Plus a clear plan for the remaining manual tests.
**Branch:** feat/feedback-round-2
**Out of scope:** Ask/answer redesign (Phase 3), write-back to YouTube, new AI providers, a dark-mode redesign beyond keeping the tokens working.

## Design notes

### A. Stop that works (T1, T2)
- Root cause: `ai_classifier.run_suggest` / `assign_categories` never check `JOB.stop_requested`, so Stop on "Suggest categories" is ignored until all batches finish.
- `assign_categories(..., should_stop: Callable[[], bool] = lambda: False)`: check `should_stop()` **before each batch**; when true, stop and return `{'suggestions': [...so far], 'failed_batches': n, 'stopped': True}` (key `stopped` is always present, False normally).
- `run_suggest(scope="unsorted", library=None, should_stop=lambda: False)` → passes it to `_suggest_library(scope, library, libs, should_stop)` → `assign_categories`. Suggestions made before the stop are **saved**; print `Stopped. {n} suggestions so far are saved.` and return True. The loop over libraries also checks `should_stop()` between libraries.
- `run_discover(max_categories=12, library=None, should_stop=lambda: False)`: check once before the model call (a single call can't be interrupted); if stopped, print `Stopped before asking the model.` and return False.
- `dashboard.py`: pass `should_stop=JOB.stop_requested` in the `/api/ai/discover` and `/api/ai/suggest` lambdas, and in `_sync` to `run_suggest`.
- `Job.snapshot()` adds `"stopping": bool` (True while running and the stop flag is set). `POST /api/job/cancel` returns `{"ok": bool, "stopping": bool}`.
- Every job kind is stoppable now. Client (T2): the Stop button shows for **all** running kinds. After a click it turns into a disabled "Stopping…" label (pill, page panel and task panel) until the task ends. Repeated clicks are impossible, and the finished banner says "Stopped".

### B. The engine: ratings you can trust (T3, T4)
- New rating JSON from the model (the prompt in `rater.build_prompt`; keep everything else):
  ```json
  {"breakdown": {"depth":     {"score": 0-10, "why": "<≤18 words>"},
                 "fit":       {"score": 0-10, "why": "..."},
                 "value":     {"score": 0-10, "why": "..."},
                 "freshness": {"score": 0-10, "why": "..."}},
   "verdict": "watch|skim|skip", "reason": "<≤20 words>",
   "tldr": "<≤2 sentences>",
   "minutes": [{"t": "<time>", "topic": "<≤8 words>", "notes": ["<≤20 words>", "..."]}],
   "takeaways": ["<≤20 words>", "..."],
   "worth_watching": [{"start": "<time>", "end": "<time>", "why": "..."}]}
  ```
- **The score is computed in code, not by the model** (`rater.compute_score`; agreed with the user on 2026-10-24): **score = quality − interest penalty (+ bonus)**. Quality is the weighted mean of depth 45%, value 40% and freshness 15% (the video itself). Interest adjusts it from fit: no penalty at fit ≥ 7, −2 at fit ≤ 3, linear in between, +0.5 at fit ≥ 9. So a great off-interest video (quality 9, fit 2) still scores 7. Defaults are in `rater.DEFAULT_FORMULA`; the user can override them in `settings["rating_formula"]` (`rater.formula_of` clamps: weights 0–1, penalty 0–5, bonus 0–2), saved through `POST /api/ai/settings {rating_formula}`. Each rating stores `calc = {score, quality, interest, formula}` (inside the `breakdown` JSON under `calc`; `get_library` returns it as `rating.calc`). The panel shows e.g. `4.8 = quality 4.8 (depth 4 × 45% + value 5 × 40% + freshness 7 × 15%) + interest 0`. If the breakdown is missing or partial, fall back to the model's `score` (old behaviour), and treat missing parts as absent rather than zero. The verdict is checked against the score: watch ≥ 7, skim 4–7, skip < 4 unless the model's verdict is within 1 point of a boundary.
- `minutes`: 3–8 topics in time order, each with 1–3 notes, built like meeting minutes (what is covered, the claims made, the steps shown). `takeaways`: 3–5 conclusions you could act on. `points` (old) stays readable for old ratings; new ratings fill `points` from `minutes` (`t` + `topic`) for older UI and exports.
- `validate()` clamps sub-scores to 0–10, drops minute entries without a valid time (same `sec()` rule), sorts by time, limits to 8 topics, 3 notes and 5 takeaways, and trims text lengths.
- Storage: `video_ratings` gains `breakdown TEXT DEFAULT '{}'`, `minutes TEXT DEFAULT '[]'`, `takeaways TEXT DEFAULT '[]'` (migration in `init_db`). `save_rating` stores them; `get_library` parses them into `rating.breakdown/minutes/takeaways`. The search summary text (`_reindex` kind `summary`) also includes minute topics and takeaways.
- Local 4B model: keep output short (limits above). `OUTPUT_TOKENS` becomes 1100 for cost estimates.
- UI (T4), in the video panel:
  1. Score and verdict.
  2. **"Why this score"**: four labelled bars (Depth 35%, Fit 30%, Time value 25%, Freshness 10%), each with its sub-score and `why`, plus one line like `6.4 = 7 × 35% + 6 × 30% + 7 × 25% + 4 × 10%`.
  3. TL;DR.
  4. **Minutes**: topics with clickable timestamps that seek the player, notes as bullets.
  5. Takeaways.
  6. Worth watching ranges.
  **Settings → General → "How scores are calculated"**: number inputs for the three quality weights (shown as %), the interest penalty and the bonus, a live example line, Save and "Reset to default" (sends `rating_formula: {}`). Changes apply to new ratings; old ratings keep their stored `calc`.
  Triage rows show a small "Why?" link that opens the panel at "Why this score". Old ratings without a breakdown show "Rate again for a score breakdown". Exported notes (`exporter.video_markdown`) gain "## Minutes" (timestamp links), "## Takeaways" and "## Why this score".

### C. UI polish (T5–T7)
- **Fonts:** remove the Google Fonts `<link>`s (no network for a local app). One sans stack everywhere: `--sans: "Inter", ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif` (Inter is used if installed). Drop `--serif` and serif headings: `.display` 32px/600/-0.02em, `h2.h` 17px/600, the logo 17px/700. Body 14px, line-height 1.5, numbers `tabular-nums`.
- **Inspiration:** the user named "opendots software". The orchestrator needs a link or screenshot (open question). Until then, aim for a calm, dense, tool-like look: neutral greys, 8px radius, 1px borders, subtle shadows only on popovers, a single accent colour.
- **Favicon:** inline SVG data URI in `<head>` (`<link rel="icon" href="data:image/svg+xml,…">`): a rounded square in the accent colour with a white play triangle and a check mark.
- **Dropdowns:** all `select`s get `appearance: none`, the same height (32px), padding `0 32px 0 10px`, 1px border, 8px radius, an inline-SVG chevron background, and a focus ring `0 0 0 3px color-mix(in srgb, var(--accent) 25%, transparent)`. Same styling for text inputs.
- **Header search:** remove `#gsearch` input; keep the ⌕ icon link to `#/search` (aria-label "Search", tooltip "Search (/)"). The `/` shortcut opens `#/search` and focuses `#sq`.
- **Left rail:** `.rail { position: sticky; top: 80px; max-height: calc(100vh - 96px); overflow-y: auto; overscroll-behavior: contain; }` so it scrolls on its own; thin scrollbar.
- **Category text:** rail labels, row meta, Triage and the Categories page show full names: rail items wrap to 2 lines (no ellipsis), with `title` holding the full name. The category in row meta gets its own chip (`.cat-chip`: 12px, `--ink-2` on `--sunken`, max-width 220px with ellipsis plus a `title` tooltip). Contrast is at least 4.5:1 in light and dark; check `--ink-3` on `--bg` and raise it if needed.
- **Tooltips and labels (T6a/T6b):** one helper `tip(text)` → `<button type="button" class="tip" aria-label="More info" data-tip="…">i</button>`. CSS shows the tooltip on hover and focus (`[data-tip]:hover::after, :focus-visible::after`), max-width 280px, `role`-free so screen readers read the `aria-label` + a visually hidden span holding the text. Rule: **any explanatory paragraph longer than one line becomes a short label + `tip()`**. Every input, select and checkbox has a visible short label (2–4 words). Page ledes are kept to one short line. Keep warnings (errors, "Ollama not running") as visible banners.
- **Pagination (T7):** "pagination not working". Reproduce on the Library list ("Show N more", `case 'more'` → `S.limit += 150; renderList()`), then check the same on Triage and Search (top 50). Fix whatever fails. If Library works, apply the same "Show more" to Triage and Search, which have none today. The worker reports what failed.

### D. Manual tests (T8)
- `docs/MANUAL-TESTS.md`: mark §1 done (2026-10-24, "all working"). Reorder the rest into short **sessions** with a time estimate, prerequisites and "how to report": S1 AI quality (§2 + §7.6/7.7, about 30 min), S2 Libraries and queue (§7.1–7.5, 20 min), S3 Ask (§6, 20 min), S4 Local media (§3, 30 min plus waiting), S5 Cloud keys (§5, 20 min, needs keys), S6 Setup and decisions (§4). Each session ends with "paste your notes in chat". Redo after T3/T4 land, since ratings change.

## Tasks

### T1: Stop works for category sorting and proposals (backend)
- **worker:** codex
- **status:** done
- **files:** `ai_classifier.py`, `dashboard.py`, `tests/test_ai.py`, `tests/test_queue.py`
- **steps:**
  1. Add `should_stop` to `assign_categories`, `run_suggest`, `_suggest_library` and `run_discover` exactly as in Design notes A; save partial suggestions.
  2. Pass `should_stop=JOB.stop_requested` in `/api/ai/discover`, `/api/ai/suggest` and `_sync`'s `run_suggest`.
  3. `Job.snapshot()` gets `stopping`; `/api/job/cancel` returns `{"ok", "stopping"}`.
- **acceptance:**
  - [ ] Stopping during batch 2 of 11 ends the task before batch 3; suggestions from batches 1–2 are saved.
  - [ ] `snapshot()["stopping"]` is True between the request and the end.
  - [ ] Tests cover: stop before the first batch, stop mid-run (fake completer counting calls), discover stopped before the call.
- **verify:** `.venv/bin/python -m unittest`

### T2: Stop feedback in the UI
- **worker:** gemini
- **status:** todo
- **depends on:** T1, T5
- **files:** `web/index.html`
- **steps:**
  1. Show Stop for every running kind (pill panel, page job panel).
  2. On click, call cancel and replace Stop with a disabled "Stopping…" until the task ends (use `job.stopping` from `/api/job`); toast once.
  3. The finished banner and pill say "Stopped: <label>" when `recent[0].stopped`.
- **acceptance:**
  - [ ] Clicking Stop once shows "Stopping…" everywhere; no second Stop button appears.
  - [ ] Works for suggest, discover, rate, details, asr, visual, embed and sync.
- **verify:** `.venv/bin/python -m unittest` and a manual check in the dashboard

### T3: Explainable score, minutes and takeaways (rating engine)
- **worker:** claude
- **status:** done
- **files:** `rater.py`, `storage.py`, `tests/test_rater.py`, `tests/test_storage.py`
- **steps:**
  1. New prompt and JSON shape, `WEIGHTS`, the computed score, and the verdict check (Design notes B).
  2. `validate()` for breakdown, minutes and takeaways; `points` filled from minutes.
  3. Storage migration plus save/parse; the summary search text includes minutes and takeaways.
  4. Re-rate 3 real videos on a **copy** of the DB and check the output is sensible with the local model.
- **acceptance:**
  - [ ] The score equals the weighted formula when the breakdown is complete; falls back otherwise.
  - [ ] Old ratings still load and show.
  - [ ] Tests: full breakdown, partial breakdown, invalid minute times dropped, limits applied, migration of an old `video_ratings` table.
- **verify:** `.venv/bin/python -m unittest`

### T4: Show "Why this score", minutes and takeaways
- **worker:** gemini
- **status:** todo
- **depends on:** T3, T2
- **files:** `web/index.html`, `exporter.py`, `tests/test_extractor_exporter.py`
- **steps:**
  0. Settings → General "How scores are calculated" editor (Design notes B).
  1. Video panel sections in the order in Design notes B; minute timestamps call `seek`; the formula line uses `rating.calc`.
  2. Triage "Why?" link; old ratings get the "Rate again" hint.
  3. Exported notes: Minutes, Takeaways and Why this score (escape with `_md_text`).
- **acceptance:**
  - [ ] Bars and the formula line match the stored breakdown; all AI text is escaped.
  - [ ] Export test covers the new sections.
- **verify:** `.venv/bin/python -m unittest`

### T5: Visual foundation — fonts, favicon, dropdowns, header search, rail, category text
- **worker:** gemini
- **status:** todo
- **files:** `web/index.html`
- **steps:** everything in Design notes C except tooltips and pagination: fonts, favicon, select and input styling, icon-only header search plus the `/` shortcut, rail scrolling, category chips and contrast.
- **acceptance:**
  - [ ] No request to fonts.googleapis.com; a favicon shows in the tab.
  - [ ] The left rail scrolls independently with 20+ categories; the page scrolls normally.
  - [ ] All selects look the same in light and dark; focus ring visible.
  - [ ] Long category names readable (wrap or tooltip), contrast ≥ 4.5:1.
  - [ ] No horizontal scroll at 375 px width.
- **verify:** `.venv/bin/python -m unittest` and a manual check at desktop and 375 px

### T6a: Tooltips and short labels — Library, Plan, Triage, Playlists
- **worker:** gemini
- **status:** todo
- **depends on:** T5, T4
- **files:** `web/index.html`
- **steps:** add the `tip()` helper and CSS (Design notes C); on these four pages turn multi-line explanations into labels plus tips; give every control a visible label.
- **acceptance:**
  - [ ] No explanatory paragraph longer than one line remains on these pages (warnings excepted).
  - [ ] Tooltips work on hover and keyboard focus.
- **verify:** `.venv/bin/python -m unittest` and a manual check

### T6b: Tooltips and short labels — Categories, Search, Ask, Settings (General, AI, Setup)
- **worker:** gemini
- **status:** todo
- **depends on:** T6a
- **files:** `web/index.html`
- **steps:** same rules as T6a on the remaining pages (the Setup guide content itself stays as written).
- **acceptance:** same as T6a for these pages.
- **verify:** `.venv/bin/python -m unittest` and a manual check

### T7: Fix pagination
- **worker:** codex
- **status:** todo
- **depends on:** T6b
- **files:** `web/index.html`
- **steps:** reproduce "Show more" on Library with more than 150 videos; fix it; add the same "Show more" (50 at a time) to Triage and Search results. Report what was broken.
- **acceptance:**
  - [ ] All 317 videos reachable on Library, Triage and Search.
- **verify:** `.venv/bin/python -m unittest` and a manual check

### T8: Manual test plan as sessions
- **worker:** claude
- **status:** todo
- **files:** `docs/MANUAL-TESTS.md`
- **steps:** Design notes D.
- **acceptance:**
  - [ ] §1 marked done; sessions S1–S6 with time, prerequisites and how to report.
- **verify:** read-through

## Answers from the user (2026-10-24)
1. Style reference: **OpenDots** (https://github.com/CopilotKit/OpenDots). T5 matches its look: clean, light, generous whitespace, rounded cards, a modern sans font, soft borders. The orchestrator checks the repo's screenshots before T5 starts.
2. Pagination fails on the **Library** page where it shows a page of videos ("Show more" does not load the rest). T7 starts there.
3. Category text is hard to read on the **Categories page** (the category name inputs and keyword fields). T5 fixes contrast and width there first.
