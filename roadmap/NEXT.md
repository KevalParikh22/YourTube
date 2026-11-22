# What's next: handoff for the next machine or session

Written 2026-11-22. State at that time: GitHub `master` at `74491aa`, **481 tests pass** (`python -m unittest`, 4 skipped). Every branch from the 2026-11-19 → 11-22 roadmap work is merged into `master`; nothing is waiting on another branch.

Read this first, then [README.md](README.md) (fix table and feature table) and the feature file of whatever you pick up.

## 1. Start on a new machine

```bash
git clone git@github.com:KevalParikh22/YourTube.git && cd YourTube
bash scripts/setup.sh                     # creates .venv and installs everything (Windows: scripts/setup.ps1)
.venv/bin/python -m unittest              # must pass before and after any change
.venv/bin/python dashboard.py --no-open --port 8765
```

- **Your data is not in git:** `yt_kb.db`, `settings.json` (AI keys and settings), `taxonomy.json` (categories) live in the repo folder (or in `YTKB_DATA_DIR`). Copy them from the old machine if you want the same library. Never commit them.
- **Testing without touching real data:** copy the three files into a scratch folder and start with `YTKB_DATA_DIR=<scratch folder> .venv/bin/python dashboard.py --no-open --port 8799`. The start line prints a link with `?t=<token>`; open that link (the login cookie is per port, so two dashboards on two ports don't log each other out).
- **Not in git on purpose** (commit `8583117`: development notes stay local): `docs/MANUAL-TESTS.md`, `docs/plan/`, `AGENTS.md`, `CLAUDE.md`, `HANDOFF.md`, `.conductor/`. The parts of them that matter for the next work are copied into this file (sections 3, 4 and the appendix). Copy the old machine's `docs/MANUAL-TESTS.md` too if you can; it also has the older sessions S1–S17.
- **Git on the old machine** had no identity set; commits used `git -c "user.name=Keval Parikh" -c "user.email=73889148+KevalParikh22@users.noreply.github.com" commit …`. The `gh` CLI was not installed, so pull requests were opened from the GitHub compare page.
- In a git worktree there is no `.venv`; use the main checkout's (`/path/to/YourTube/.venv/bin/python`).

## 2. Pending work, in suggested order

| # | Task | Size | Where | Notes |
|---|---|---|---|---|
| 1 | **Price for Gemini 3.5 Flash-Lite** (and any other model you use) | XS | `yourtube/llm.py` `PRICES` | The model in use, `gemini-3.5-flash-lite`, is missing from `PRICES` (it only has the 2.5 models), so Settings → Usage shows "price unknown", estimates show no cost, and **the daily budget never stops it** (its calls add $0). Needs the real input/output USD per 1M tokens from Google's pricing page; don't guess. Add a test in `tests/test_llm.py` `CostTest`. |
| 2 | **Run the manual tests** S18–S24 | — | appendix below | Nothing in them has been checked with real YouTube and real AI yet. S22.1 (rating quality after the prompt change) matters most. Write results in the Result column and turn every problem into a task. |
| 3 | **Token saving, rest** | M | `yourtube/llm.py`, `rater.py`, `summarizer.py`, `storage.py` | (a) Claude prompt caching: the prompt must be sent as two text blocks (fixed instructions with `cache_control: {"type": "ephemeral"}`, then the video), which means `build_prompt` returns the two parts and `make_completer`/`_anthropic` accept them. (b) Count cached tokens at their lower price: Gemini `usageMetadata.cachedContentTokenCount`, OpenAI `usage.prompt_tokens_details.cached_tokens`, Claude `usage.cache_read_input_tokens` (today they are all priced as normal input, so Usage over-reports). (c) Rate from an existing summary instead of the transcript. (d) `video_id` column in `ai_usage`. (e) Show actual vs estimated cost after a run. Details: [token-tracking.md](token-tracking.md). |
| 4 | **Daily budget field polish** | XS | `web/static/app.js` (`viewAI`, search `daily-cap`) | The field sits in a bare card under Usage with no heading or explanation. Give it a heading and one line: what it covers (all cloud AI, Claude included), that it resets at local midnight, empty = no cap. |
| 5 | **Courses and learning paths, rest** | M | `summarizer.py`, `storage.py`, `exporter.py`, `app.js` | Markdown export of a whole learning path; reorder videos in a path after it is made; make a path from a playlist in one click. Reminders need a notification mechanism first (none exists; the dashboard only runs locally). Idea: split parts at topic changes with the LLM instead of chapters/pauses. Details: [video-course.md](video-course.md). |
| 6 | **One output language, rest** | S | `classifier.py`, `knowledge.py` | Category names and keywords stay in their language (keywords must match the videos' own titles); knowledge pages made in an old language are not flagged. Details: [language-consistency.md](language-consistency.md). |
| 7 | **Channel follow, rest** | S–M | `dashboard.py`, `app.js` | Suggest channels to follow from the ones you watch most (step 5). Details: [channel-follow.md](channel-follow.md). |
| 8 | **Night queue limits** | S | `dashboard.py` (`Job`) | Tasks waiting for the night live in memory and are lost when the dashboard restarts; there is no end time, so a night task that starts runs to the end. Details: [off-peak-queue.md](off-peak-queue.md). |
| 9 | **Small leftovers from the fixes** | S | see [README.md](README.md) fix table | Fix 2: update the meaning index right before an Ask. Fix 3: show the learned rate limit in Settings → AI. Fix 6: "other parallel problems" need steps to reproduce first; ask the user before working on it. |
| 10 | **Recommendations from how you choose and drop videos** | XL | [taste-model.md](taste-model.md) | Last. Needs a few hundred real keep/skip decisions before it can beat the AI ratings. |

## 3. Rules every AI working here follows (from the local `AGENTS.md`)

- Stack: Python 3 standard library only (`http.server`, `sqlite3`) in `yourtube/`; vanilla JS single-page app in `web/static/app.js` + `app.css`; yt-dlp for YouTube. No new dependencies or JS/CSS libraries without asking.
- Tests: `unittest` in `tests/`, one file per module; `tests/__init__.py` points every module at throwaway files. `tests/test_retrieval.py` fails when run alone but passes in the full suite (known, old).
- Every `/api/` route in `yourtube/dashboard.py` must be in `docs/openapi.json` (`tests/test_openapi.py` checks both ways; new tags must also be added to `ALLOWED_TAGS` there). Edit `openapi.json` by hand in small hunks; never rewrite it with `json.dump` (that reformats 4,000 lines).
- Settings: `llm.load_settings()`; defaults in `llm.default_settings()`, sent to the UI by `llm.public_settings()`, saved through `POST /api/ai/settings` (validate every new field there).
- Cloud AI calls go through `llm._open` (pacing, retries, `QuotaError`, daily budget) except Claude, which uses the SDK in `llm._anthropic` (it calls `check_cap` itself). Every cloud call records usage with `llm.record_usage`.
- Long-running work is a task in `dashboard.Job` (`JOB.start(kind, label, fn, key=…, not_before=…)`); AI tasks run in the AI lane (`lane_for`), which may be "local" or "cloud".
- UI text: plain English, short sentences. Keep the existing patterns (`data-act` click handlers, `data-change` change handlers, `toast`, `queued`, `saveAi`).
- Manual tests: anything a person must check by hand gets rows in `docs/MANUAL-TESTS.md` (session table row + `| # | Test | Steps | Expect | Result |` table, dated). Number new sessions after the highest S-number (next: **S25**).
- Never touch `yt_kb.db`, `settings.json`, `taxonomy.json`.

## 4. Working with conductor-ai (how the roadmap was built)

Claude Code plans and reviews; `conductor-ai` (installed globally with npm, v0.11.3) hands tasks to other models (codex-cli, antigravity-cli, opencode, free API models).

```bash
conductor-ai doctor                                        # providers ready?
conductor-ai init --controller claude-code --verify "<path>/.venv/bin/python -m unittest"
# then fill section 1 of the generated AGENTS.md with the rules in section 3 above
conductor-ai plan check docs/plan/<feature>.md             # every task needs files, checkable acceptance, verify, ≤4 files besides tests
conductor-ai batch --plan docs/plan/<feature>.md --tasks T1,T2   # parallel, one worktree each, branch conductor/<id>
conductor-ai diff conductor/T1                             # review before merging
git merge --no-ff conductor/T1 && conductor-ai worktree rm T1
```

What we learned:
- Write concrete **Design notes** in the plan (function names, signatures, exact behaviour): workers must not make design decisions. Template: `docs/plan/_TEMPLATE.md` (created by `init`).
- codex-cli's sandbox blocks local sockets, so the dashboard's HTTP tests fail inside it and it reports **PARTIAL** even when the work is complete; conductor's own verify still says PASS. Read the diff, merge it yourself, and run the full suite. Because of this, `batch --all` stops its dependency chain; run dependent tasks one batch at a time instead.
- A `repro:` line must be a command that **fails** before the fix, or conductor refuses the task (`--allow-no-repro` skips the check).
- Tasks that touch the same files (`app.js`, `dashboard.py`) should depend on each other, not run in parallel.
- Always check the worker's claims: one docs worker wrote "stale or ready pages" where it meant "stale or unbuilt"; one filter counted every video with details as "has a transcript" (the right check is `v.details.sources`, the saved transcripts).

## Appendix: pending manual tests (copy of `docs/MANUAL-TESTS.md` S18–S24)

Start the dashboard with `.venv/bin/python dashboard.py`. For each test, note what you saw in the Result column, especially anything confusing, wrong or slow.

| Session | Time | Before you start |
|---|---|---|
| S18 · Video activity history | ~15 min | A few videos already rated or summarised |
| S19 · One output language | ~15 min + waiting | An AI for ratings and summaries; one Hindi (or other non-English) video with captions |
| S20 · Weekly course from a long video | ~20 min + waiting | An AI for summaries; a video over 2 h with captions |
| S21 · Roadmap fixes and new features | ~60 min + waiting | A cloud AI key; 2–3 videos over 1 h; one YouTube channel you like. Some steps spend a little AI quota |
| S22 · Token saving | ~20 min | A cloud AI key; Settings → Cloud AI → Usage open in a second tab |
| S23 · Merged side work | ~25 min | Two dashboards on two ports; a followed channel under 50 videos; a cloud AI key |
| S24 · Learning paths | ~15 min | 2–3 videos with a weekly course, or long videos to make them |

### S18 · Video activity history (2026-11-21)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S18.1 | Old work shows | Open a video you rated or summarised before this update → expand **Activity** at the bottom of the panel | Earlier details, transcription, rating and summary steps are listed with dates and models (no tokens for old steps) | |
| S18.2 | New step with cost | Rate one video with a cloud AI; reopen its panel | A new "Rated" line at the top with the model, tokens in/out and a cost close to Settings → Cloud AI → Usage for that call | |
| S18.3 | Failure shows | Make a step fail (e.g. Transcribe a private/removed video, or rate with a wrong API key) | A line marked "failed" with the error underneath | |
| S18.4 | Live update | Keep a panel open with Activity expanded while Summarise runs on that video | When the task finishes the new line appears without closing the panel or losing scroll | |
| S18.5 | Index step | Settings → update the meaning index after adding a video | That video's Activity shows "Added to meaning index · N pieces" | |
| S18.6 | Card marks | Look at the Library list | Cards show *transcript*, *summarised* and *indexed* where true; nothing looks crowded on a phone | |
| S18.7 | Filters | Library → Show → Not rated, Not summarised, Not in meaning index | Each lists only matching videos; counts match; the chip is hidden when none match | |

### S19 · One output language (2026-11-22)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S19.1 | Hindi video in English | Settings → About you → Response language = English; Rate and Summarise a Hindi video | Reason, key points, minutes, takeaways, summary and quiz all in English; names and on-screen text kept as they are | |
| S19.2 | Knowledge page and Ask | Build a knowledge page in a category with Hindi and English videos; Ask a question answered by the Hindi video | Page and answer in English; citations still open the right moments | |
| S19.3 | Change language | Switch Response language to another language and Save; open a video summarised before | The panel shows "Summary in English · Redo in <new>" (and the same for the rating) | |
| S19.4 | Redo one | Click Redo in that panel | A task runs; afterwards the summary is in the new language and the note is gone | |
| S19.5 | Redo all | Settings → About you shows "N summaries and M ratings are in another language" → Redo in … | Cost confirms appear first (one for summaries, one for ratings); after the tasks the line is gone | |

### S20 · Weekly course from a long video (2026-11-20)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S20.1 | Make a course | Open a video over 2 h → pick "2 parts a week" → Make a weekly course | A "Course" task runs; afterwards the panel says "Course: 0 of N parts watched · Open course" | |
| S20.2 | Parts make sense | Open course → read the part titles and summaries | Parts are 20–40 min and start at sensible places (chapters or topic changes), not mid-sentence | |
| S20.3 | Watch and tick | ▶ Watch on part 2; tick it and one action; reload the page | Video starts at part 2's time; ticks stay after reload; Home → Courses shows 1 watched | |
| S20.4 | Pace | Change to 3 parts a week | Weeks regroup, ticks stay | |
| S20.5 | Export / end | Export Markdown, then End course | The .md has weeks, watch links and [x] for done actions; the course leaves Home, the video panel offers Make again | |
| S20.6 | Long summary in parts | Summarise a 3–4 h video (no course yet) | The estimate is several times a normal video's; the task log shows "part 1/N…"; the summary covers the whole video, not just samples; making a course on it afterwards needs no AI task | |
| S20.8 | Quiz per part | On a course made after 2026-11-21: answer a "Check yourself" question wrong, then click ▶ Replay the moment, then answer right; reload | Wrong shows "Not quite" and replays the video where it is explained; you can try again; the right answer shows ✓ after reload. Questions make sense for that part (real AI) | |
| S20.9 | Part 1 first | Make a weekly course on a new 3–4 h video (real AI); stay on the course page that opens | Parts show "being written…" and fill in one by one; you can watch and tick part 1 before the rest is done | |
| S20.10 | Continue | While a course is being written, Stop the task in the task list; then click Continue on the course page | Written parts and ticks stay; Continue writes only the missing parts | |
| S20.11 | Daily pace | On a course page choose "30 min a day"; then change Speed (header or Settings) and reopen the course; Export Markdown | Days of about 30 min at your speed with a "done by" date that fits your plan; a faster speed means fewer days; the export shows the same days | |
| S20.12 | Skip what you know | On a course about a topic you partly know (made after 2026-11-21, real AI): Skip what you know → answer honestly → Tick what I know | Only parts you answered right are ticked; the finish date moves earlier; the questions are fair for deciding you can skip that part | |
| S20.13 | Learning path | Library → tick 3 videos of one topic (one without a course) → Make a learning path; follow it for a few days | One plan over all parts in order; the new video's parts appear as written; ticks match each course page; changing the pace changes weeks/days and the date; Delete path keeps the courses | |
| S20.7 | Night queue | Settings → Night queue on (90 min); Make a weekly course on a 3 h video | The task waits for the night start time with Run now | |

### S21 checks (roadmap fixes and new features, 2026-11-20)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S21.1 | Transcript stays open | Open a video → open Transcript; start any task (e.g. Fetch details) and let it finish | The transcript stays open at the same scroll; no `jw is not defined` in the console | |
| S21.2 | Rate button | Library, a playlist and a category page with unrated videos; also at phone width | **Rate N unrated** sits on the right of the chips row on every one | |
| S21.3 | Already done | Select 10 videos, some already summarised → Summarise | Cards show the **summarised** mark; a confirm offers "only the new ones / redo all / cancel" | |
| S21.4 | Meaning index on click | Settings → AI by task: automatic index off. Rate a video, then open Search and Ask | "N pieces not in the meaning index yet · Update index"; clicking it updates; turning automatic on updates after tasks | |
| S21.5 | Rate limit | Rate 30+ videos with Gemini free tier | It slows down instead of failing ("Slowing down to one request every N s"); if the daily quota runs out, it stops once with a clear message and Retry continues the rest | |
| S21.6 | Remove videos | Library → select 2 videos (one with notes) → Remove from YourTube → then sync their playlist | The confirm counts the notes; they are gone with their data; the sync does not add them back; adding one by its own link does | |
| S21.7 | Usage | Run a Rate of 3 videos on cloud AI → Settings → Cloud AI → Usage | Today's calls, tokens and cost per task and model; the task log ends with "AI use: … about $…" | |
| S21.8 | Night queue | Settings → AI by task → Night queue on (90 min, start a few minutes from now) → Summarise a 2 h video and a short one | The short one runs now; the long one shows "Later" with the time and ☾ in the bar; it starts by itself at that time; **Run now** starts it at once | |
| S21.9 | Follow a channel | Open a video → Follow channel (or Playlists → Followed channels → paste a link) → Check now | The channel is listed with its name; nothing old is added; a new upload (wait for one, or lower the interval) is added, sorted and rated/summarised as chosen | |
| S21.10 | New on Home | After S21.9 brings in a new video | Home shows **New from followed channels** with it (unwatched, last 7 days, newest first) | |
| S21.11 | Import a whole channel | Followed channels → Import all on a small channel (under 50 videos) | The confirm shows videos, hours, how many are new and the cost; then they are added, details fetched and summaries queued; knowledge pages are queued after the summaries | |
| S21.12 | Channel knowledge and Ask | Knowledge → Pages from → the channel; open a page; Channels card → Ask | Pages cite only that channel's videos; Ask answers only from that channel | |
| S21.13 | Build all | Knowledge page with some stale or unbuilt topics → **Build all (N)** | One task builds those N pages; the button hides when nothing needs building | |
| S21.14 | Weekly course | Open a video over 1 h → Make a weekly course (2 parts a week) → open the course | Parts of 20–40 min with titles, summaries, timed points and 1–3 actions; ▶ Watch starts at the part; ticks survive a reload; pace change keeps ticks; Export Markdown opens in Obsidian | |
| S21.15 | Long summary in parts | Summarise a 3 h+ video | The summary covers the whole video (also its end); the parts are reused if you then make a course | |
| S21.16 | Activity | Open a video you rated and summarised → Activity | Each step with time, model, tokens and cost; a failed step shows its error | |
| S21.17 | Filters and transcript mark | Library → **Has transcript**, **Not summarised**, **Summarised** | Counts add up; cards with a transcript show **transcript** | |
| S21.18 | Daily budget | Settings → Cloud AI → Daily budget: 0.01 → Rate 5 videos | It stops once the budget is reached with "Today's cloud AI budget … is used up"; empty the field and Retry continues | |

### S22 checks (token saving, 2026-11-20)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S22.1 | Ratings still good | Re-rate 5 videos you know, 2 of them over 1 h | Verdicts and minutes are as good as before; long videos' minutes still cover the end | |
| S22.2 | Fewer tokens | Settings → Cloud AI → Usage before and after rating a 2 h video | The rating uses about 12k input tokens or fewer (it was up to ~50k) | |
| S22.3 | Cache discount | Rate 10 videos in a row on Gemini 2.5 or OpenAI | In the provider's console, part of the input shows as cached (YourTube still counts it at full price) | |
| S22.4 | Budget stops Claude | Choose Claude for ratings; Daily budget 0.01; Rate 5 videos | It stops with the budget message, like Gemini | |

### S23 checks (merged side work, 2026-11-20)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S23.1 | Two dashboards, two logins | Start the dashboard on 8765 and a copy on 8799 (`--port 8799`); open each with its own link in the same browser; reload both | Both stay logged in; opening one no longer logs the other out | |
| S23.2 | Pages build by themselves | Import all on a small followed channel; wait for the summaries | The task list shows "Knowledge pages: <channel>" waiting while Summarise runs, then building; Knowledge → Pages from → the channel shows pages for topics with 3+ summaries | |
| S23.3 | Estimate leaves out removed videos | Remove 2 of a channel's videos from YourTube → Import all on that channel | The confirm's "new" count does not include the 2 removed ones | |
| S23.4 | Marks and filters | Library: look at cards; open Show list | Cards show **transcript**, **summarised** and **indexed** marks where true; filters **Has transcript**, **Not summarised**, **Summarised**, **Not in meaning index**; a video without captions or local transcript is not under Has transcript | |
| S23.5 | Index in Activity | Update the meaning index → open a video that was added | Activity shows "Added to meaning index" with the model and the number of pieces | |
| S23.6 | Long video estimate | Select a 3 h+ video (no course yet) → Summarise | The cost shown is clearly higher than for a 30 min video and matches the real use in Settings → Usage within about 30% | |

### S24 checks (learning paths, 2026-11-22)

| # | Test | Steps | Expect | Result |
|---|---|---|---|---|
| S24.1 | Make a path | Library → select 3 videos of one library → Make a learning path | One task writes courses for videos without one; the path page lists every part of every video by week (or day) with one finish date | |
| S24.2 | Pace | Change parts per week, then minutes a day | Weeks/days and the finish date update; ticks stay | |
| S24.3 | Shared ticks | Tick a part on the path, then open that video's course page | The same part is ticked there, and the other way round | |
| S24.4 | Home | Open Home | The path is listed; its videos are not repeated under Courses | |
| S24.5 | Rename and delete | Rename the path; then Delete it | Renamed everywhere; after Delete each video keeps its own course and ticks | |
