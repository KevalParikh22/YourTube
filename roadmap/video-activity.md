# Per-video activity history

**Problem:** You can't see what has run on a video (details, transcription, rating, summary, index), when, with which model, and whether it failed.

**Status (2026-11-20):** steps 1 and 2 (the Activity list) are done.
- `storage.video_activity` gets one row per step on a video: details (`save_details`), transcription and slides (`set_job` from *downloading* to *done*/*failed*), rating and summary (`storage.video_step` around `_rate_one` / `_summarize_one`). Cloud AI calls made during a step (`record_usage`) add their tokens and cost to it; failures keep the error. A new table is filled once from saved details, media jobs, ratings and summaries (without tokens).
- The video panel ends with **Activity** (`GET /api/video/activity`), newest first, reloaded whenever the panel redraws.
- Not done: status icons on cards beyond the existing summary icon and verdict badge, Library filters by these states (Transcript pending exists), meaning-index updates (they run over many videos at once, not per video).

**Plan**
1. Record: when a task processes a video, write a row to `video_activity` (video_id, kind, time, ok, model, tokens). Reuse the `ai_usage` rows from [token-tracking.md](token-tracking.md) for AI tasks.
2. Show: an "Activity" list at the bottom of the video panel, and per-video status icons on cards (transcript, rated, summarised, indexed).
3. Filter the Library by any of those states (generalises fix #7 in the README).

**Done when:** opening any video shows its full processing history.
