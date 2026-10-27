# Plan: Phase 3 — The brain (summaries that beat YouTube's, learn more, knowledge base)

**Goal:** Any video can get a summary richer than YouTube's: minutes with timestamps, key claims and quotes, check-yourself questions, takeaways, related moments from your other videos, and a "Learn more" list (prerequisites, deeper, other views, plus YouTube searches for gaps). Topics get **knowledge pages** that combine every video's notes with citations, exportable to Obsidian. Descriptions in the video panel can be hidden.
**Branch:** feat/phase-3-brain
**Out of scope:** automatic background summarising (the user chose "only when I click Summarise"), web browsing, changing how ratings score, UI/layout changes beyond the parts below (the user will list them separately).

## Decisions (2026-10-27)
1. Summaries are made **only when the user clicks Summarise** (per video, or for a selection or category). Ratings stay a separate, personal step.
2. Beyond YouTube's summary: **key claims and quotes**, **related moments in other videos**, **check-yourself questions**.
3. Learn more: **from the library + YouTube search links** for gaps (`https://www.youtube.com/results?search_query=<q>`, no account).
4. Knowledge base: **topic pages**, one per category of a library, built from its videos' summaries, every point cited.

## Design notes

### A. Summaries (T1, T2)
- New module `summarizer.py` and table `video_summaries(video_id PK, tldr, minutes, claims, quiz, takeaways, learn_more, model, language, created_at)`; JSON columns default `'[]'` (`learn_more` default `'{}'`). Migration in `storage.init_db`; `storage.save_summary(video_id, s, model)` and `get_summary(video_id)`; `_docs_for(kind="summary")` also indexes the summary's minutes, claims and takeaways (search and Ask find them).
- New AI task `summarize` in `llm.TASKS` ("Summaries"); it follows `rate` (`llm.TASK_FOLLOWS`).
- Model reply (JSON), all text in `settings["language"]`:
  ```json
  {"tldr": "<=3 sentences",
   "minutes": [{"t": "<time>", "topic": "<=8 words", "notes": ["<=20 words", "... up to 3"]}],
   "claims": [{"t": "<time>", "kind": "claim|quote|number|step", "text": "<=30 words, exact wording for quotes"}],
   "quiz": [{"q": "...", "a": "<=25 words", "t": "<time where it is answered>"}],
   "takeaways": ["<=20 words"],
   "learn_more": {"related": [{"video_id": "<one of the candidates>", "relation": "prerequisite|deeper|other_view|same_topic", "why": "<=15 words"}],
                  "searches": [{"query": "<=8 words", "why": "<=15 words"}]}}
  ```
  Limits: minutes 3–10, claims 3–10, quiz 3–5, takeaways 3–5, related ≤ 4, searches ≤ 3.
- Prompt input: title, channel, length, chapters, the active transcript (`storage.get_segments`, sampled like `rater.fit_transcript` to `llm.context_chars`), slides text if any, and **candidates**: the 8 most related other videos in the same library (title, channel, TL;DR or first chapter), from `retrieval` by meaning (use the video's title + first 2,000 transcript characters as the query; exclude the video itself).
- `summarizer.validate(raw, duration, candidate_ids)`: times through `rater.parse_time` and the same 0..duration rule, drop related entries whose `video_id` isn't a candidate, clamp lengths and counts, sort by time. A reply without minutes raises `LLMError`.
- **Related moments are computed in code, not by the model** (`summarizer.related_moments(video_id)`): for each minute topic, embed `topic + notes` and find the best `transcript`/`slide` piece in another video of the same library with cosine ≥ 0.55; keep at most 2 per topic. Computed when the summary is shown (`GET /api/summary?id=` returns `{summary, moments}`), not stored. Skip silently without embeddings.
- Job kind `summarize` (stoppable between videos; `AUTO_EMBED_KINDS` includes it). `POST /api/summarize {ids}` (required; at most 50 per call) → queued like `/api/rate`. `rater.estimate`-style cost confirm: `POST /api/summarize/estimate {ids}`.
- Panel (T2), order: TL;DR → **Minutes** (clickable times, each topic followed by "Also in: <video> @ m:ss" chips from `moments`) → **Key claims and quotes** (kind badge + clickable time) → **Check yourself** (questions, answer hidden behind "Show answer", with time) → **Takeaways** → **Learn more** (related videos grouped by relation, each opens that video; YouTube search links open in a new tab, `rel="noopener"`). A video without a summary shows **Summarise** (one click, ~30 s locally). The rating box stays as it is; when a summary exists, the rating's minutes and takeaways are hidden to avoid duplicates. Library selection bar and the Triage page get **Summarise selected**.
- **Description:** the video panel shows the description collapsed ("Show description", `<details>`). Settings → General gets "Show descriptions in the video panel" (`settings["show_descriptions"]`, default false): when true, the `<details>` starts open.

### B. Knowledge base (T3, T4)
- Table `topic_pages(library_id, category, content, sources, video_ids_hash, model, created_at, PRIMARY KEY(library_id, category))`.
- `knowledge.py`: `build_page(library, category)` gathers the summaries (TL;DR, minutes, claims, takeaways) of every summarised video in that category, numbers them as sources `[n]` (video + time), and asks the `summarize` task for:
  ```json
  {"overview": "<=4 sentences", "sections": [{"heading": "...", "points": [{"text": "...", "cites": [1, 3]}]}],
   "open_questions": ["things the videos disagree on or don't cover"]}
  ```
  Citations are checked in code like `asker.check_citations` (drop numbers that weren't sent). Videos without a summary are listed as "not included yet: Summarise them first". If the sources exceed the model's context, use TL;DR + takeaways only for the oldest videos (state that on the page).
- `video_ids_hash` marks a page **stale** when the category's summarised videos change; the page shows "Update" then.
- API: `GET /api/knowledge?library=` (categories with status: none / ready / stale, summarised count / total), `GET /api/knowledge/page?library=&category=`, `POST /api/knowledge/build {category}` (job kind `knowledge`), `GET /api/export/knowledge?library=&category=` (Markdown: headings, points, citations as timestamp links, sources list).
- UI (T4): new tab **Knowledge** (between Ask and Plan): list of the library's categories with status and buttons Build / Update / Open; the page view shows overview, sections with citation chips that play the moment, open questions, Export note.

## Tasks

### T1: Summaries engine (backend)
- **worker:** claude
- **status:** done (real check: 3 videos, ~100 s each locally with qwen3.5:4b; the UI should say ~1-2 min, not ~30 s)
- **files:** `summarizer.py` (new), `storage.py`, `llm.py`, `dashboard.py`, `tests/test_summarizer.py` (new)
- **steps:** table, save and get, search indexing; prompt with related candidates; validate; `related_moments`; job `summarize` with stop; `/api/summarize`, `/api/summarize/estimate`, `/api/summary`. Check on a **copy** of the real DB with the local model on 3 videos.
- **acceptance:** [ ] summaries for 3 real videos look right; [ ] tests cover validate (bad times, unknown candidate ids, limits), save and get round trip, related_moments with fake vectors, the endpoint and stop.
- **verify:** `.venv/bin/python -m unittest`

### T2: Summary in the video panel, Summarise buttons, description toggle
- **worker:** codex
- **status:** done (agy timed out at 20 min; codex built it; orchestrator fixed: loading state, empty-summary reply instead of 404, chips for related moments, threshold 0.6)
- **depends on:** T1
- **files:** `web/index.html`, `dashboard.py` (setting only), `exporter.py` (notes include the summary sections), `tests/test_extractor_exporter.py`
- **steps:** Design notes A "Panel" and "Description"; exported notes gain Summary, Minutes, Key claims, Check yourself, Takeaways, Learn more.
- **acceptance:** [ ] no duplicate timestamps; [ ] all AI text escaped; [ ] export test covers the new sections.
- **verify:** `.venv/bin/python -m unittest` and a check in the dashboard

### T3: Knowledge pages (backend)
- **worker:** claude
- **status:** todo
- **depends on:** T1
- **files:** `knowledge.py` (new), `storage.py`, `dashboard.py`, `exporter.py`, `tests/test_knowledge.py` (new)
- **steps:** Design notes B (table, build, citation check, staleness, API, Markdown export). Check one real category on a DB copy.
- **acceptance:** [ ] a real page reads well and every point is cited; [ ] tests for citation checking, staleness, too-large input, export.
- **verify:** `.venv/bin/python -m unittest`

### T4: Knowledge tab
- **worker:** codex
- **status:** todo
- **depends on:** T3, T2
- **files:** `web/index.html`
- **steps:** Design notes B "UI".
- **acceptance:** [ ] build, open, update and export work; citation chips play the moment; escaped text.
- **verify:** `.venv/bin/python -m unittest` and a check in the dashboard

### T5: Docs and manual tests
- **worker:** claude
- **status:** todo
- **depends on:** T1–T4
- **files:** `docs/SETUP.md`, `docs/MANUAL-TESTS.md` (new session S7: summaries, learn more, knowledge pages), `CLAUDE.md`, `docs/PRD-Phase-3.md` (decisions above + results)
- **verify:** read-through

## Open question for the user
- "UI issues and layout we could revisit": which pages or parts? Send a short list or screenshots, and they become their own small plan after this one.
