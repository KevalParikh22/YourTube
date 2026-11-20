# Roadmap

Open issues from testing (2026-11-19). Bugs and small changes have a fix plan below; new features each have their own file.

## Fixes

Done on 2026-11-19 (marked **Done**): all eight. Fix 6 covers the rate-limit part; other parallel problems need a reproduction.

| # | Issue | Cause found in the code | Fix |
|---|-------|-------------------------|-----|
| 1 **Done** | Clicking **Transcript** in the video panel "does nothing" after switching to Gemini until DevTools opens | `taskFinished()` (`web/static/app.js:944`) calls `load()` → `render()` → `renderDrawer()`, which rebuilds the panel's `innerHTML` (`app.js:2057`). Every finished task does this, and every sync/details/asr/visual/rate/summarize task queues an automatic "Update meaning index" task (`yourtube/dashboard.py:287`). Switching the embedding provider to Gemini starts a full index rebuild, so tasks keep finishing and each one closes the open `<details>` transcript and resets scroll. The `jw is not defined` error is not from this app: a `VMxxx:1` anonymous script is code typed into the console or injected by a browser extension. | In `renderDrawer()`, remember which `<details>` are open and the panel's scroll position before replacing `innerHTML`, then restore them (same pattern `taskFinished` already uses for the notes box). Better still, skip the rebuild when the finished task can't have changed this video (`embed`, `pull`, `backup`). Confirm by reproducing: open a video, start an index rebuild, open the transcript. |
| 2 **Done** | Index updates after every action | `AUTO_EMBED_KINDS` (`dashboard.py:398`) queues an index update after each task. | Done: the automatic update only runs with the new `auto_index` setting (Settings → AI by task, off by default). Search and Ask show "N pieces not in the meaning index yet · Update index" from `/api/embed/status`. Not done: updating right before an Ask. |
| 3 **Done** | Gemini rate limit still fails | `llm._open` retries 3 times on 429/503 but gives up at once on a used-up daily quota, and nothing paces calls, so a batch hits the per-minute limit over and over. The meaning index (`embedder.py`) called Gemini/OpenAI with no retry at all. | Done: `llm._open` paces calls per endpoint (gap starts at 0, doubles on each 429 from 4 s up to 60 s, shrinks while calls succeed), and the embedder now goes through it. A used-up daily quota raises `llm.QuotaError`: Rate/Summarise stop at once, keep what's done, and **Retry** skips videos finished since the task was queued (`done_since`). Not done: showing the learned limit in Settings → AI. |
| 4 **Done** | **Rate N unrated** button misaligned at the top of some pages | `.verdict-bar` (`app.css:295`) is `space-between` + `flex-wrap`; when the chips wrap, the button drops to its own line and sits left. | Give the button `margin-left: auto` and align the bar with `align-items: center` on one row; check at phone, tablet and desktop widths. Also make it appear on every list page that has unrated videos (playlist, category, Library), not only where `verdict-bar` is shown. |
| 5 **Done** | Already summarised/rated videos look the same; bulk actions redo them | `rate-sel` / `summarize-sel` (`app.js:2456`) send all selected IDs. | Show a small icon on cards that have a summary (and the verdict badge already shows a rating). Before a bulk Rate/Summarise, split the selection: if some are already done, show a confirm: "3 of 10 are already summarised. Summarise only the 7 new ones / Redo all / Cancel". |
| 6 **Done** (rate-limit part) | Parallel AI processing still wrong in places | Tasks run in three lanes (`local`, `cloud`, `youtube`, `dashboard.py:99`); the same-task check uses the exact ID list, so two overlapping Rate batches both rate the shared videos, and a task, its helper and Ask each hit the same rate limit on their own. A helper out of daily quota failed three videos before stopping. | Done: the pacer from #3 is shared by every thread calling an endpoint; a batch queued behind an overlapping one skips videos the first one finished (same `done_since`); a worker out of daily quota stops at once and the other AI takes its videos (`ends_worker` in `parallel.share`). Other parallel problems still need a reproduction. |
| 7 **Done** | Show only videos with transcript pending | Sources page shows counts only (`app.js:685`). | Add a "Transcript pending" filter chip in the Library (`needsDetails(v)` already exists, `app.js:168`), with a "Fetch these" button. |
| 8 **Done** | Remove single videos and their files in one step | Only whole playlists can be removed (`storage.remove_playlist`, `dashboard._remove_playlist`). | Done: Library → select → **Remove from YourTube** (`POST /api/videos/remove`, `storage.remove_videos`). Deletes the video with its notes, data, search pieces, vectors and slide pictures; the confirm says so and counts notes. A new `removed_videos` table stops a playlist sync from adding it back; adding it by its own link does. |

Unclear items, need the reporter's input before planning:
- "Consistency of video in another language": summaries already follow Settings → Language (`summarizer.py:60`). Is the problem that ratings, key points or titles stay in the video's language, or that old summaries keep the previous language? See [language-consistency.md](language-consistency.md) for the working assumption.
- "Individual operations and tracking": read as a per-video history of what ran on it. See [video-activity.md](video-activity.md).

## New features

| Feature | File | Size |
|---------|------|------|
| Token use tracking and cutting it down (**tracking, daily cap, 48,000-character rating cap and prompt order done**) | [token-tracking.md](token-tracking.md) | M |
| One output language for every video | [language-consistency.md](language-consistency.md) | S |
| Schedule big summaries for the night (**done**) | [off-peak-queue.md](off-peak-queue.md) | M |
| Follow channels, summarise new videos automatically (**done**, Home list included) | [channel-follow.md](channel-follow.md) | L |
| Whole channel into the knowledge base (**done**, pages built per topic or with **Build all**) | [channel-knowledge.md](channel-knowledge.md) | M |
| A 4–5 hour video becomes a weekly course (**done**, reminders open) | [video-course.md](video-course.md) | L |
| Per-video activity history (**done**: card marks, Library filters, index steps) | [video-activity.md](video-activity.md) | S |
| Recommendations from how you choose and drop videos | [taste-model.md](taste-model.md) | XL |

Suggested order: fixes 1–5 and 7 (small, user-visible), then 3+6 together, 2, 8; then token tracking (needed by off-peak and course), off-peak queue, channel follow, channel knowledge, course, taste model.
