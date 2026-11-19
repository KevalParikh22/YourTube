# Per-video activity history

**Problem:** You can't see what has run on a video (details, transcription, rating, summary, index), when, with which model, and whether it failed.

**Plan**
1. Record: when a task processes a video, write a row to `video_activity` (video_id, kind, time, ok, model, tokens). Reuse the `ai_usage` rows from [token-tracking.md](token-tracking.md) for AI tasks.
2. Show: an "Activity" list at the bottom of the video panel, and per-video status icons on cards (transcript, rated, summarised, indexed).
3. Filter the Library by any of those states (generalises fix #7 in the README).

**Done when:** opening any video shows its full processing history.
