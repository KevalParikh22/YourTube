# Roadmap

Open issues from testing (2026-11-19). Bugs and small changes have a fix plan below; new features each have their own file.

## Fixes

Done on 2026-11-19 (marked **Done**): 1, 4, 5, 7. The rest are planned, not started.

| # | Issue | Cause found in the code | Fix |
|---|-------|-------------------------|-----|
| 1 **Done** | Clicking **Transcript** in the video panel "does nothing" after switching to Gemini until DevTools opens | `taskFinished()` (`web/static/app.js:944`) calls `load()` → `render()` → `renderDrawer()`, which rebuilds the panel's `innerHTML` (`app.js:2057`). Every finished task does this, and every sync/details/asr/visual/rate/summarize task queues an automatic "Update meaning index" task (`yourtube/dashboard.py:287`). Switching the embedding provider to Gemini starts a full index rebuild, so tasks keep finishing and each one closes the open `<details>` transcript and resets scroll. The `jw is not defined` error is not from this app: a `VMxxx:1` anonymous script is code typed into the console or injected by a browser extension. | In `renderDrawer()`, remember which `<details>` are open and the panel's scroll position before replacing `innerHTML`, then restore them (same pattern `taskFinished` already uses for the notes box). Better still, skip the rebuild when the finished task can't have changed this video (`embed`, `pull`, `backup`). Confirm by reproducing: open a video, start an index rebuild, open the transcript. |
| 2 | Index updates after every action | `AUTO_EMBED_KINDS` (`dashboard.py:398`) queues an index update after each task. | Stop the automatic update. Show "N pieces not indexed yet · Update" in the Ask and Search pages (count from `storage.docs_missing_embeddings`, already used by `/api/embed/estimate`), and update the index when the user clicks it or right before an Ask/meaning search if the user turns that on. Keep a setting "Update the index automatically" (off by default). |
| 3 | Gemini rate limit still fails | `llm._open` retries 3 times on 429/503 but gives up at once on a used-up daily quota, and nothing paces calls, so a batch hits the per-minute limit over and over. | Add a per-provider pacer in `llm.py` (min seconds between calls, e.g. 4 s for Gemini free tier = 15/min) applied before each request. On a daily-quota error, stop the batch cleanly with "Gemini's daily limit is used up; the rest will run tomorrow" and keep the unprocessed IDs so **Retry** continues them. Show the limit in Settings → AI. |
| 4 **Done** | **Rate N unrated** button misaligned at the top of some pages | `.verdict-bar` (`app.css:295`) is `space-between` + `flex-wrap`; when the chips wrap, the button drops to its own line and sits left. | Give the button `margin-left: auto` and align the bar with `align-items: center` on one row; check at phone, tablet and desktop widths. Also make it appear on every list page that has unrated videos (playlist, category, Library), not only where `verdict-bar` is shown. |
| 5 **Done** | Already summarised/rated videos look the same; bulk actions redo them | `rate-sel` / `summarize-sel` (`app.js:2456`) send all selected IDs. | Show a small icon on cards that have a summary (and the verdict badge already shows a rating). Before a bulk Rate/Summarise, split the selection: if some are already done, show a confirm: "3 of 10 are already summarised. Summarise only the 7 new ones / Redo all / Cancel". |
| 6 | Parallel AI processing still wrong in places | Tasks run in three lanes (`local`, `cloud`, `youtube`, `dashboard.py:99`); the same-task check uses kind + label, so two Rate batches with different videos both run, and a cloud task and its helper can hit the same rate limit. | Needs a reproduction list first (which tasks, what went wrong). Likely fixes: dedupe by video ID across queued tasks of the same kind; share the pacer from #3 between a task and its helper threads. |
| 7 **Done** | Show only videos with transcript pending | Sources page shows counts only (`app.js:685`). | Add a "Transcript pending" filter chip in the Library (`needsDetails(v)` already exists, `app.js:168`), with a "Fetch these" button. |
| 8 | Remove single videos and their files in one step | Only whole playlists can be removed (`storage.remove_playlist`, `dashboard._remove_playlist`). | Add `storage.remove_videos(ids)` reusing the per-video cleanup inside `remove_playlist` (transcripts, frames, embeddings, summaries; keep notes unless asked), a `/api/videos/remove` route (+ `docs/openapi.json`), and a "Remove from YourTube" bulk action with a confirm that lists what is deleted. |

Unclear items, need the reporter's input before planning:
- "Consistency of video in another language": summaries already follow Settings → Language (`summarizer.py:60`). Is the problem that ratings, key points or titles stay in the video's language, or that old summaries keep the previous language? See [language-consistency.md](language-consistency.md) for the working assumption.
- "Individual operations and tracking": read as a per-video history of what ran on it. See [video-activity.md](video-activity.md).

## New features

| Feature | File | Size |
|---------|------|------|
| Token use tracking and cutting it down | [token-tracking.md](token-tracking.md) | M |
| One output language for every video | [language-consistency.md](language-consistency.md) | S |
| Schedule big summaries for the night | [off-peak-queue.md](off-peak-queue.md) | M |
| Follow channels, summarise new videos automatically | [channel-follow.md](channel-follow.md) | L |
| Whole channel into the knowledge base | [channel-knowledge.md](channel-knowledge.md) | M |
| A 4–5 hour video becomes a weekly course | [video-course.md](video-course.md) | L |
| Per-video activity history | [video-activity.md](video-activity.md) | S |
| Recommendations from how you choose and drop videos | [taste-model.md](taste-model.md) | XL |

Suggested order: fixes 1–5 and 7 (small, user-visible), then 3+6 together, 2, 8; then token tracking (needed by off-peak and course), off-peak queue, channel follow, channel knowledge, course, taste model.
